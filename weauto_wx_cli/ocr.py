from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .config import OcrConfig


@dataclass
class OcrLine:
    text: str
    score: float
    x_center: float
    y_center: float


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_points(raw_box: object) -> np.ndarray | None:
    try:
        pts = np.array(raw_box, dtype=np.float32).reshape(-1, 2)
    except Exception:
        return None
    if pts.shape[0] < 4:
        return None
    return pts[:4]


def _collect_hits(raw: Any, out: list[tuple[np.ndarray, str, float]]) -> None:
    if raw is None:
        return
    if isinstance(raw, dict):
        polys = raw.get("dt_polys")
        if polys is None:
            polys = raw.get("rec_polys")
        texts = raw.get("rec_texts")
        scores = raw.get("rec_scores")
        if isinstance(texts, list) and polys is not None:
            score_items = scores if isinstance(scores, list) else []
            for idx, text in enumerate(texts):
                pts = _to_points(polys[idx] if idx < len(polys) else None)
                clean = str(text or "").strip()
                if pts is not None and clean:
                    out.append((pts, clean, _safe_float(score_items[idx] if idx < len(score_items) else 0.0)))
            return
        box = raw.get("box") or raw.get("points") or raw.get("bbox") or raw.get("poly")
        text = str(raw.get("text") or raw.get("rec_text") or raw.get("label") or "").strip()
        score = _safe_float(raw.get("score") or raw.get("rec_score") or raw.get("confidence"))
        pts = _to_points(box)
        if pts is not None and text:
            out.append((pts, text, score))
            return
        for value in raw.values():
            _collect_hits(value, out)
        return
    if isinstance(raw, (list, tuple)):
        if len(raw) >= 2:
            pts = _to_points(raw[0])
            text = ""
            score = 0.0
            if isinstance(raw[1], str):
                text = raw[1].strip()
                score = _safe_float(raw[2] if len(raw) >= 3 else 0.0)
            elif isinstance(raw[1], (list, tuple)) and raw[1]:
                text = str(raw[1][0]).strip()
                score = _safe_float(raw[1][1] if len(raw[1]) >= 2 else 0.0)
            if pts is not None and text:
                out.append((pts, text, score))
                return
        for item in raw:
            _collect_hits(item, out)


class OcrEngine:
    def __init__(self, cfg: OcrConfig) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self.cfg = cfg
        self._engine = RapidOCR()

    def detect_lines(self, image_bgr: np.ndarray) -> list[OcrLine]:
        img = self._enhance(image_bgr) if self.cfg.enhance else image_bgr
        raw, _ = self._engine(img)
        hits: list[tuple[np.ndarray, str, float]] = []
        _collect_hits(raw or [], hits)
        lines: list[OcrLine] = []
        for pts, text, score in hits:
            if score < self.cfg.min_score:
                continue
            lines.append(
                OcrLine(
                    text=text,
                    score=score,
                    x_center=float(np.mean(pts[:, 0])),
                    y_center=float(np.mean(pts[:, 1])),
                )
            )
        return sorted(lines, key=lambda line: (line.y_center, line.x_center))

    @staticmethod
    def _enhance(image_bgr: np.ndarray) -> np.ndarray:
        h, w = image_bgr.shape[:2]
        scale = max(1.0, min(2.5, 900.0 / float(min(h, w) or 1)))
        if scale > 1.01:
            image_bgr = cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        return image_bgr
