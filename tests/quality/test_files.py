"""Boundary, scope, and failure tests for maintained-file checks."""

import os
from pathlib import Path
from unittest.mock import patch

from helpers import RepositoryCase


class FileGateTests(RepositoryCase):
    def test_exactly_1000_lines_passes(self) -> None:
        self.write("module.txt", "# Comment\n" * 1000)
        self.assertEqual(self.scan().violations, [])

    def test_1001_lines_fails_with_location(self) -> None:
        self.write("module.txt", "\n" * 1001)
        violation = self.scan().violations[0]
        self.assertEqual(violation.rule, "file-lines")
        self.assertEqual(violation.line, 1001)
        self.assertIn("1001 lines", violation.message)

    def test_final_line_without_newline_counts(self) -> None:
        self.write("notes.md", "\n" * 1000 + "final line")
        self.assertEqual(self.rules(self.scan()), ["file-lines"])

    def test_crlf_and_cr_are_physical_line_breaks(self) -> None:
        self.write("windows.txt", "line\r\n" * 500 + "line\r" * 500)
        self.assertEqual(self.scan().violations, [])

    def test_unicode_separator_does_not_create_physical_lines(self) -> None:
        self.write("unicode.txt", "text\u2028" * 1001)
        self.assertEqual(self.scan().violations, [])

    def test_hidden_and_untracked_configurations_are_checked(self) -> None:
        self.write(".codex/agents/backend.toml", "\n" * 1001)
        self.write("new-untracked.md", "\n" * 1001)
        self.assertEqual(self.rules(self.scan()), ["file-lines", "file-lines"])

    def test_source_test_documentation_and_infra_directories_are_checked(self) -> None:
        for name in ("tests", "docs", "infra", "backend"):
            self.write(f"{name}/maintained.txt", "\n" * 1001)
        self.assertEqual(self.rules(self.scan()), ["file-lines"] * 4)

    def test_dependencies_caches_and_generated_directories_are_excluded(self) -> None:
        for name in ("node_modules", ".git", ".venv", "build", "generated"):
            self.write(f"{name}/dependency.txt", "\n" * 1001)
        self.assertEqual(self.scan().text_files, 0)

    def test_only_precise_lockfile_names_are_excluded(self) -> None:
        self.write("package-lock.json", "\n" * 1001)
        self.write("local.lock", "\n" * 1001)
        self.assertEqual(self.rules(self.scan()), ["file-lines"])

    def test_binary_files_are_counted_as_skipped(self) -> None:
        path = self.write("image.png", "")
        path.write_bytes(b"\x89PNG\r\n\x00\xff")
        report = self.scan()
        self.assertEqual(report.binary_files, 1)
        self.assertEqual(report.violations, [])

    def test_symlinked_files_and_directories_are_skipped(self) -> None:
        target = self.write("generated/large.txt", "\n" * 1001)
        (self.root / "linked.txt").symlink_to(target)
        (self.root / "linked-directory").symlink_to(target.parent, target_is_directory=True)
        report = self.scan()
        self.assertEqual(report.symlinks, 2)
        self.assertEqual(report.violations, [])

    def test_invalid_utf8_text_fails(self) -> None:
        path = self.write("notes.md", "")
        path.write_bytes(b"bad encoding: \xff")
        self.assertEqual(self.rules(self.scan()), ["encoding"])

    def test_read_error_fails_closed(self) -> None:
        self.write("notes.md", "text")
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            report = self.scan()
        self.assertEqual(self.rules(report), ["read-error"])

    def test_named_pipe_fails_without_blocking_scan(self) -> None:
        os.mkfifo(self.root / "pipe.txt")
        self.assertEqual(self.rules(self.scan()), ["read-error"])

    def test_missing_root_fails(self) -> None:
        from scripts.quality.runner import run

        report = run(self.root / "missing")
        self.assertEqual(self.rules(report), ["root"])
