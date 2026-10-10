"""Development blob store: atomic writes under opaque keys inside one root directory."""

import os
import re
from pathlib import Path
from uuid import uuid4

KEY = re.compile(r"^[0-9a-f]{32}$")


class FilesystemBlobStore:
    def __init__(self, root: Path):
        self._root = root

    def write(self, data: bytes) -> str:
        key = uuid4().hex
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".partial")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return key

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        if not KEY.match(key):
            raise KeyError(key)
        return self._root / key[:2] / key
