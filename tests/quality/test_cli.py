"""Command exit codes and explicit skip output are public gate behavior."""

import os
import subprocess
import sys
from pathlib import Path

from helpers import RepositoryCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class CommandGateTests(RepositoryCase):
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
        command = [sys.executable, "-m", "scripts.quality", "--root", str(self.root)]
        return subprocess.run(
            [*command, *arguments],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_backend_outputs_explicit_architecture_skip(self) -> None:
        self.write("README.md", "project planning")
        result = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertIn("SKIP architecture: no Python modules", result.stdout)

    def test_violation_outputs_location_and_nonzero_exit(self) -> None:
        self.write("notes.md", "\n" * 1001)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("notes.md:1001: file-lines", result.stdout)

    def test_threshold_override_is_rejected(self) -> None:
        result = self.invoke("--max-complexity", "100")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)
