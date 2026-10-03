"""Syntax and Radon complexity for every Python function, including closures."""

import ast
from pathlib import Path

from radon.complexity import cc_visit_ast
from radon.visitors import ComplexityVisitor

from .report import Report

MAX_COMPLEXITY = 4
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def parse_python(path: Path, text: str, report: Report) -> ast.Module | None:
    try:
        tree = ast.parse(text, filename=str(path))
        compile(tree, str(path), "exec")
    except (SyntaxError, ValueError, TypeError) as error:
        report.add(path, getattr(error, "lineno", None) or 1, "python-syntax", str(error))
        return None
    return tree


def function_metric(node: ast.AST) -> tuple[str, int]:
    if isinstance(node, ast.Lambda):
        return "<lambda>", ComplexityVisitor.from_ast(node).complexity
    block = cc_visit_ast(node)[0]
    return block.name, block.complexity


def check_function(path: Path, node: ast.AST, report: Report) -> None:
    try:
        name, complexity = function_metric(node)
    except Exception as error:
        report.add(path, node.lineno, "complexity-error", str(error))
        return
    if complexity > MAX_COMPLEXITY:
        message = f"{name} has complexity {complexity}; maximum is {MAX_COMPLEXITY}"
        report.add(path, node.lineno, "python-complexity", message)


def check_complexity(path: Path, tree: ast.Module, report: Report) -> None:
    for node in ast.walk(tree):
        if isinstance(node, FUNCTION_NODES):
            check_function(path, node, report)
