"""Walk maintained files, including hidden files and files not tracked by Git."""

import os
import stat
from collections.abc import Iterator
from pathlib import Path

from .report import Report

# These are dependency, build, cache, and generated directories, not source layers.
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".venv-quality",
        "venv",
        "env",
        "node_modules",
        "vendor",
        "third_party",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "coverage",
        "htmlcov",
        ".coverage_html",
        "generated",
        "site-packages",
        ".data",
    }
)
EXCLUDED_LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    }
)
EXCLUDED_FILES = EXCLUDED_LOCKFILES | {".coverage", ".DS_Store"}


def excluded_directory(parent: Path, name: str, report: Report) -> bool:
    if (parent / name).is_symlink():
        report.symlinks += 1
        return True
    return name in EXCLUDED_DIRECTORIES or name.endswith(".egg-info")


def prune_directories(parent: Path, names: list[str], report: Report) -> None:
    names[:] = [name for name in names if not excluded_directory(parent, name, report)]


def regular_file(path: Path, report: Report) -> bool:
    if path.is_symlink():
        report.symlinks += 1
        return False
    try:
        mode = path.stat().st_mode
    except OSError as error:
        report.add(path, 1, "read-error", str(error))
        return False
    if not stat.S_ISREG(mode):
        report.add(path, 1, "read-error", "unsupported non-regular file")
        return False
    return True


def files_in_directory(parent: Path, names: list[str], report: Report) -> Iterator[Path]:
    for name in sorted(names):
        if name in EXCLUDED_FILES:
            continue
        path = parent / name
        if not regular_file(path, report):
            continue
        yield path


def maintained_files(root: Path, report: Report) -> Iterator[Path]:
    for current, directories, files in os.walk(root, onerror=report.walk_error):
        parent = Path(current)
        prune_directories(parent, directories, report)
        yield from files_in_directory(parent, files, report)
