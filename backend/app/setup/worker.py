"""Ingestion worker process: owns its resources and stops cleanly on SIGTERM/SIGINT."""

import logging
import signal
import threading
from contextlib import ExitStack

from sqlalchemy.exc import SQLAlchemyError

from app.data.db.session import create_database_engine
from app.services.ingestion.worker import IngestionWorker
from app.setup.knowledge import ingestion_worker
from app.setup.services import create_services
from app.utils.config import Settings
from app.utils.logging import show_flows

logger = logging.getLogger("context_mesh.worker")


def serve(worker: IngestionWorker, stop: threading.Event, idle_seconds: float = 1.0) -> None:
    while not stop.is_set():
        if not _step(worker):
            stop.wait(idle_seconds)


def _step(worker: IngestionWorker) -> bool:
    try:
        return worker.run_once()
    except SQLAlchemyError:
        logger.warning("Database unavailable; the worker will retry.")
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Request URLs are not job progress.
    config = Settings()
    stop = threading.Event()
    show_flows()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with ExitStack() as resources:
        engine = create_database_engine(config.database_url)
        resources.callback(engine.dispose)
        worker = ingestion_worker(engine, config, create_services(config, resources))
        logger.info("Ingestion worker started; worker_id=%s", worker.worker_id)
        serve(worker, stop)
    logger.info("Ingestion worker stopped.")
