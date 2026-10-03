"""Coordinate independent gates without importing project application code."""

from pathlib import Path

from .architecture import check_architecture
from .imports import module_context
from .paths import maintained_files
from .python_checks import check_complexity, parse_python
from .report import Report
from .text import check_lines, read_text


def check_python(path: Path, text: str, report: Report) -> None:
    report.python_files += 1
    report.architecture_files += int(module_context(path, report.root) is not None)
    tree = parse_python(path, text, report)
    if tree is None:
        return
    check_complexity(path, tree, report)
    check_architecture(path, tree, report)


def check_file(path: Path, report: Report) -> None:
    text = read_text(path, report)
    if text is None:
        return
    report.text_files += 1
    check_lines(path, text, report)
    if path.suffix == ".py":
        check_python(path, text, report)


def run(root: Path) -> Report:
    report = Report(root.resolve())
    if not report.root.is_dir():
        report.add(report.root, 1, "root", "root must be an existing directory")
        return report
    for path in maintained_files(report.root, report):
        check_file(path, report)
    return report
