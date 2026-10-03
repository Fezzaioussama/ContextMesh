"""Temporary repositories for behavioral quality-gate tests."""

import tempfile
import unittest
from pathlib import Path

from scripts.quality.report import Report
from scripts.quality.runner import run


class RepositoryCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def backend(self, name: str, text: str) -> Path:
        return self.write(f"backend/src/context_mesh/{name}", text)

    def scan(self) -> Report:
        return run(self.root)

    def rules(self, report: Report) -> list[str]:
        return [violation.rule for violation in report.violations]
