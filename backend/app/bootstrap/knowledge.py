"""Wire source management for the API and job handlers for the ingestion worker."""

import os
import socket
from uuid import uuid4

from sqlalchemy import Engine

from app.bootstrap.services import ExternalServices
from app.core.config import Settings
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.repositories.indexing_repository import IndexingRepository
from app.db.repositories.job_repository import JobRepository
from app.db.repositories.retrieval_repository import RetrievalRepository
from app.db.repositories.source_repository import SourceRepository
from app.parsers.html import HtmlPageReader
from app.parsers.registry import default_parsers
from app.services.ingestion.crawler import CrawlPolicy, SiteCrawler
from app.services.ingestion.erasers import DocumentEraser, SourceEraser
from app.services.ingestion.indexer import DocumentIndexer
from app.services.ingestion.worker import IngestionWorker
from app.services.sources import SourceService
from app.storage.filesystem import FilesystemBlobStore


def source_service(engine: Engine, config: Settings) -> SourceService:
    return SourceService(
        SourceRepository(engine),
        RetrievalRepository(engine),
        FilesystemBlobStore(config.blob_dir),
        max_upload_bytes=config.max_upload_bytes,
    )


def ingestion_worker(
    engine: Engine, config: Settings, services: ExternalServices
) -> IngestionWorker:
    jobs = JobRepository(engine, config.job_lease_seconds)
    store = IndexingRepository(engine)
    blobs = FilesystemBlobStore(config.blob_dir)
    indexer = DocumentIndexer(
        jobs, store, blobs, default_parsers(), services.embeddings, services.vectors
    )
    crawler = SiteCrawler(
        jobs,
        CrawlRepository(engine),
        services.fetcher,
        HtmlPageReader(),
        blobs,
        CrawlPolicy(config.crawl_max_pages, config.crawl_max_depth),
    )
    handlers = {
        "source.sync_requested": crawler,
        "document.index_requested": indexer,
        "document.delete_requested": DocumentEraser(jobs, store, blobs, services.vectors),
        "source.delete_requested": SourceEraser(jobs, store, blobs, services.vectors),
    }
    return IngestionWorker(jobs, handlers, worker_identity())


def worker_identity() -> str:
    return f"{socket.gethostname()[:60]}:{os.getpid()}:{uuid4().hex[:8]}"
