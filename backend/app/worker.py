"""The ingestion worker process: `python -m app.worker`. Stops cleanly on SIGTERM/SIGINT."""

import logging
import signal
import threading
from contextlib import ExitStack

from sqlalchemy.exc import SQLAlchemyError

from app.data.db import create_database_engine
from app.services.indexing import IngestionWorker
from app.utils.config import Settings
from app.utils.logging import show_flows
from app.wiring import build_worker, create_services

logger = logging.getLogger("context_mesh.worker")


def serve(worker: IngestionWorker, stop: threading.Event, idle_seconds: float = 1.0) -> None:
    """Run jobs back to back; wait a moment when the queue is empty or the database is down."""
    while not stop.is_set():
        try:
            busy = worker.run_once()
        except SQLAlchemyError:
            logger.warning("Database unavailable; the worker will retry.")
            busy = False
        if not busy:
            stop.wait(idle_seconds)


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
        worker = build_worker(engine, config, create_services(config, resources))
        logger.info("Ingestion worker started; worker_id=%s", worker.worker_id)
        serve(worker, stop)
    logger.info("Ingestion worker stopped.")


if __name__ == "__main__":
    main()
