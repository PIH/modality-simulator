"""The acquisition log: every result, one JSON object per line, under /data."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from modality_simulator.acquire import AcquisitionResult


@dataclass
class AccessionHistory:
    stored: bool = False
    failures: int = 0


class AcquisitionLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, result: AcquisitionResult) -> None:
        record = asdict(result)
        record["ok"] = result.ok
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:  # a line cut short by a crash
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def recent(self, limit: int = 20) -> list[dict]:
        return list(reversed(self.records()))[:limit]

    def history(self) -> dict[str, AccessionHistory]:
        histories: dict[str, AccessionHistory] = {}
        for record in self.records():
            h = histories.setdefault(record.get("accession_number", ""), AccessionHistory())
            if record.get("ok"):
                h.stored = True
            else:
                h.failures += 1
        return histories
