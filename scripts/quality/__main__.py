"""CLI with fixed thresholds; exemptions are reviewable source configuration."""

import argparse
from pathlib import Path

from .report import Report
from .runner import run


def print_report(report: Report) -> None:
    for violation in report.violations:
        print(violation.format(report.root))
    status = "FAIL" if report.violations else "PASS"
    print(
        f"{status}: checked {report.text_files} text files and {report.python_files} Python files"
    )
    print(f"Skipped {report.binary_files} binary files and {report.symlinks} symlinks")
    print_architecture_status(report)


def print_architecture_status(report: Report) -> None:
    if report.architecture_files:
        print(f"Architecture: checked {report.architecture_files} context_mesh modules")
        return
    print("SKIP architecture: no Python modules in backend/src/context_mesh")


def main() -> int:
    parser = argparse.ArgumentParser(description="ContextMesh repository quality gates")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    arguments = parser.parse_args()
    report = run(arguments.root)
    print_report(report)
    return int(bool(report.violations))


if __name__ == "__main__":
    raise SystemExit(main())
