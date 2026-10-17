"""Measure files against the strict limits the implementer and reviewer agents enforce.

These limits are stricter than the repo-wide lint config (ruff.toml, eslint.config.mjs):

- a source file has at most 500 physical lines;
- a function body has at most 40 lines, its docstring excluded;
- a function has cyclomatic complexity at most 4 (Ruff C901, ESLint `complexity`);
- every public Python module, class, and function has a Google-style docstring.

Tests are held to the file-length and complexity limits only: a `describe` block
legitimately spans many lines, and a test's name states what it checks.

Run it from the repository root:

    python3 .claude/scripts/check_limits.py             # files changed since main, plus untracked
    python3 .claude/scripts/check_limits.py a.py b.tsx  # only these files

It reports every violation in the files it checks, including ones that predate the change,
and exits 1 when there is at least one.
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_FILE_LINES = 500
MAX_FUNCTION_LINES = 40
MAX_COMPLEXITY = 4

RUFF = ".venv/bin/ruff"
ESLINT = "node_modules/.bin/eslint"
PYTHON_SUFFIXES = {".py"}
SCRIPT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
SOURCE_SUFFIXES = PYTHON_SUFFIXES | SCRIPT_SUFFIXES | {".css"}
TEST_FOLDERS = {"tests", "test"}
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

RUFF_FINDING = re.compile(r"^\S+:\d+:\d+: ")
RUFF_COMPLEXITY = ["--select", "C901"]
# Magic methods and __init__ are described by their class docstring.
RUFF_DOCSTRINGS = ["--select", "D", "--ignore", "D105,D107"]

ESLINT_CONFIG_ERROR = 2
ESLINT_COMPLEXITY = {"complexity": ["error", {"max": MAX_COMPLEXITY, "variant": "classic"}]}
ESLINT_FUNCTION_LENGTH = {
    "max-lines-per-function": [
        "error",
        {"max": MAX_FUNCTION_LINES, "skipBlankLines": True, "skipComments": True},
    ],
}


def main(arguments: list[str]) -> int:
    """Check the named files, or the branch's changed files, and print each violation.

    Returns:
        The process exit code: 1 when any limit is exceeded, otherwise 0.
    """
    candidates = [Path(argument) for argument in arguments] or changed_files()
    files = [path for path in candidates if path.suffix in SOURCE_SUFFIXES and path.is_file()]
    violations = check_files(files)
    print("\n".join(violations) or f"OK: {len(files)} file(s) within the limits.")
    return 1 if violations else 0


def changed_files() -> list[Path]:
    """Files changed since the branch left main, plus untracked files git does not ignore."""
    base = git("merge-base", "HEAD", "main").strip()
    changed = git("diff", "--name-only", "--diff-filter=d", base)
    untracked = git("ls-files", "--others", "--exclude-standard")
    return [Path(name) for name in (changed + untracked).splitlines()]


def git(*arguments: str) -> str:
    """Run a read-only git command and return its standard output."""
    command = ["git", *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def check_files(files: list[Path]) -> list[str]:
    """Every violation across the files, as `path:line: rule detail` lines."""
    tests = [path for path in files if is_test(path)]
    production = [path for path in files if path not in tests]
    return (
        [line for path in files for line in file_length(path)]
        + [line for path in of_type(production, PYTHON_SUFFIXES) for line in long_functions(path)]
        + ruff(RUFF_COMPLEXITY, of_type(files, PYTHON_SUFFIXES))
        + ruff(RUFF_DOCSTRINGS, of_type(production, PYTHON_SUFFIXES))
        + eslint(ESLINT_COMPLEXITY | ESLINT_FUNCTION_LENGTH, of_type(production, SCRIPT_SUFFIXES))
        + eslint(ESLINT_COMPLEXITY, of_type(tests, SCRIPT_SUFFIXES))
    )


def is_test(path: Path) -> bool:
    """Whether the file is a test, by this repo's layout: a tests/ or test/ folder, or *.test.*."""
    return bool(TEST_FOLDERS & set(path.parts)) or ".test." in path.name


def of_type(files: list[Path], suffixes: set[str]) -> list[Path]:
    """The files whose extension is one of the suffixes."""
    return [path for path in files if path.suffix in suffixes]


def file_length(path: Path) -> list[str]:
    """A violation when the file has more than MAX_FILE_LINES physical lines."""
    count = len(path.read_text(encoding="utf-8").splitlines())
    if count <= MAX_FILE_LINES:
        return []
    return [f"{path}:1: file-length {count} > {MAX_FILE_LINES}"]


def long_functions(path: Path) -> list[str]:
    """Python functions and methods whose body, docstring excluded, exceeds MAX_FUNCTION_LINES."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [node for node in ast.walk(tree) if isinstance(node, FUNCTION_NODES)]
    return [
        f"{path}:{node.lineno}: function-length `{node.name}` {length} > {MAX_FUNCTION_LINES}"
        for node in functions
        if (length := body_length(node)) > MAX_FUNCTION_LINES
    ]


def body_length(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Physical lines from the first statement after the docstring to the function's end."""
    has_docstring = ast.get_docstring(function, clean=False) is not None
    body = function.body[1:] if has_docstring else function.body
    if not body:
        return 0
    return function.end_lineno - body[0].lineno + 1


def ruff(selection: list[str], files: list[Path]) -> list[str]:
    """Ruff's findings for the selected rules, using the repo config with the strict limits."""
    if not files:
        return []
    command = [
        RUFF, "check", "--no-cache", "--output-format", "concise",
        "--config", "ruff.toml",
        "--config", f"lint.mccabe.max-complexity = {MAX_COMPLEXITY}",
        "--config", "lint.pydocstyle.convention = 'google'",
        *selection, *map(str, files),
    ]  # fmt: skip
    output = subprocess.run(command, capture_output=True, text=True, check=False).stdout
    return [line for line in output.splitlines() if RUFF_FINDING.match(line)]


def eslint(rules: dict, files: list[Path]) -> list[str]:
    """ESLint's findings, using the repo config with the given rules overriding it.

    Raises:
        SystemExit: ESLint could not run, for example because its config is invalid.
    """
    if not files:
        return []
    options = ["--rule", json.dumps(rules), "--format", "json", "--no-warn-ignored"]
    result = subprocess.run(
        [ESLINT, *options, *map(str, files)], capture_output=True, text=True, check=False
    )
    if result.returncode == ESLINT_CONFIG_ERROR:
        raise SystemExit(f"ESLint failed:\n{result.stderr}")
    reports = json.loads(result.stdout)
    return [
        eslint_line(report["filePath"], item) for report in reports for item in report["messages"]
    ]


def eslint_line(file_path: str, message: dict) -> str:
    """One ESLint message in the same `path:line: rule detail` shape as the other checks."""
    path = Path(file_path).relative_to(Path.cwd())
    return f"{path}:{message.get('line', 1)}: {message.get('ruleId')} {message['message']}"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
