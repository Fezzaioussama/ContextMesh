"""Resolve static imports without executing application modules."""

import ast
from dataclasses import dataclass
from pathlib import Path

from .report import Report

PACKAGE_PATH = Path("backend/src/context_mesh")
LAYERS = frozenset({"domain", "application", "adapters", "bootstrap"})


@dataclass(frozen=True)
class Module:
    name: str
    package: str
    layer: str


def module_name(relative: Path) -> str:
    parts = ["context_mesh", *relative.with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_layer(name: str) -> str:
    parts = name.split(".")
    if len(parts) == 1:
        return "package"
    return parts[1]


def module_context(path: Path, root: Path) -> Module | None:
    try:
        relative = path.relative_to(root / PACKAGE_PATH)
    except ValueError:
        return None
    name = module_name(relative)
    package = name.rsplit(".", 1)[0]
    if path.name == "__init__.py":
        package = name
    return Module(name, package, module_layer(name))


def relative_base(node: ast.ImportFrom, module: Module) -> str:
    package = module.package.split(".")
    if node.level > len(package):
        raise ValueError("relative import escapes context_mesh")
    prefix = package[: len(package) - node.level + 1]
    suffix = (node.module or "").split(".")
    return ".".join([*prefix, *filter(None, suffix)])


def from_base(node: ast.ImportFrom, module: Module) -> str:
    if node.level:
        return relative_base(node, module)
    return node.module or ""


def from_targets(node: ast.ImportFrom, module: Module) -> list[str]:
    base = from_base(node, module)
    if base.split(".")[0] == "context_mesh":
        return [f"{base}.{alias.name}" for alias in node.names]
    return [base]


def targets(node: ast.AST, module: Module) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return from_targets(node, module)


def resolved_targets(path: Path, node: ast.AST, module: Module, report: Report) -> list[str]:
    try:
        return targets(node, module)
    except ValueError as error:
        report.add(path, node.lineno, "architecture", str(error))
        return []
