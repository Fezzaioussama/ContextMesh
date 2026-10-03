"""Shared diagnostics and scan counts, independent of individual checks."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    message: str

    def format(self, root: Path) -> str:
        path = self.path.relative_to(root)
        return f"{path}:{self.line}: {self.rule}: {self.message}"


@dataclass
class Report:
    root: Path
    violations: list[Violation] = field(default_factory=list)
    text_files: int = 0
    python_files: int = 0
    architecture_files: int = 0
    binary_files: int = 0
    symlinks: int = 0

    def add(self, path: Path, line: int, rule: str, message: str) -> None:
        self.violations.append(Violation(path, line, rule, message))

    def walk_error(self, error: OSError) -> None:
        path = Path(error.filename or self.root)
        self.add(path, 1, "read-error", str(error))
