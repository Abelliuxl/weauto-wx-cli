from __future__ import annotations

import json
from pathlib import Path


class SeenState:
    def __init__(self, path: str, max_items: int = 5000) -> None:
        self.path = Path(path)
        self.max_items = max_items
        self.seen: list[str] = []
        self._set: set[str] = set()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        values = raw.get("seen", []) if isinstance(raw, dict) else []
        if not isinstance(values, list):
            return
        self.seen = [str(item) for item in values if str(item)]
        self.seen = self.seen[-self.max_items :]
        self._set = set(self.seen)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"seen": self.seen[-self.max_items :]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, fingerprint: str) -> bool:
        fp = str(fingerprint or "").strip()
        if not fp:
            return False
        if fp in self._set:
            return False
        self._set.add(fp)
        self.seen.append(fp)
        if len(self.seen) > self.max_items:
            removed = self.seen[: -self.max_items]
            self.seen = self.seen[-self.max_items :]
            for item in removed:
                self._set.discard(item)
        return True

    def contains(self, fingerprint: str) -> bool:
        return fingerprint in self._set
