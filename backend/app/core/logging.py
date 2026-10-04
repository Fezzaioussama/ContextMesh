"""Shared logger names; handlers emit safe identifiers rather than request content."""

import logging

http_logger = logging.getLogger("context_mesh.http")
