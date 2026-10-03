"""Enforce the planned hexagonal architecture's static dependency direction."""

import ast
import sys
from pathlib import Path

from .imports import LAYERS, Module, module_context, resolved_targets
from .report import Report

PURE_DEPENDENCIES = {
    "domain": frozenset({"domain"}),
    "application": frozenset({"domain", "application"}),
}
STDLIB = sys.stdlib_module_names | {"__future__"}
IMPORT_NODES = (ast.Import, ast.ImportFrom)


def target_layer(target: str) -> str:
    parts = target.split(".")
    if len(parts) < 2:
        return "package"
    return parts[1]


def pure_import_error(module: Module, target: str) -> str | None:
    root = target.split(".")[0]
    if root in STDLIB:
        return None
    if root != "context_mesh":
        return f"{module.layer} cannot import external dependency {target}"
    if target_layer(target) not in PURE_DEPENDENCIES[module.layer]:
        return f"{module.layer} cannot import {target}"
    return None


def adapter_import_error(module: Module, target: str) -> str | None:
    if target.startswith("context_mesh.bootstrap"):
        return f"adapters cannot import composition root {target}"
    outbound = module.name.startswith("context_mesh.adapters.outbound")
    if outbound and target.startswith("context_mesh.adapters.inbound"):
        return f"outbound adapters cannot import inbound adapter {target}"
    return None


def import_error(module: Module, target: str) -> str | None:
    if module.layer in PURE_DEPENDENCIES:
        return pure_import_error(module, target)
    if module.layer == "adapters":
        return adapter_import_error(module, target)
    return None


def check_import(path: Path, node: ast.AST, module: Module, report: Report) -> None:
    for target in resolved_targets(path, node, module, report):
        message = import_error(module, target)
        if message:
            report.add(path, node.lineno, "architecture", message)


def check_module(path: Path, tree: ast.Module, module: Module, report: Report) -> None:
    if module.layer not in LAYERS | {"package"}:
        report.add(path, 1, "architecture", f"unknown source layer {module.layer}")
    for node in ast.walk(tree):
        if isinstance(node, IMPORT_NODES):
            check_import(path, node, module, report)


def check_architecture(path: Path, tree: ast.Module, report: Report) -> None:
    module = module_context(path, report.root)
    if module is None:
        return
    check_module(path, tree, module, report)
