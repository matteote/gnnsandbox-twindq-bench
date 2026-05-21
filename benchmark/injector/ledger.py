"""defect_ledger.jsonl writer.

Append-only; refuses to overwrite an existing ledger unless explicitly told to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO

from injector.types import DefectRecord


class LedgerWriter:
    """Context manager: line-by-line append of `DefectRecord` rows."""

    def __init__(self, path: Path, *, overwrite: bool = False) -> None:
        self.path = Path(path)
        self.overwrite = overwrite
        self._fh: IO[str] | None = None

    def __enter__(self) -> "LedgerWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self.overwrite:
            raise FileExistsError(
                f"Ledger {self.path} already exists. Pass overwrite=True."
            )
        self._fh = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def write(self, record: DefectRecord) -> None:
        if self._fh is None:
            raise RuntimeError("LedgerWriter not entered")
        payload = record.model_dump(mode="json")
        self._fh.write(json.dumps(payload, sort_keys=True) + "\n")
