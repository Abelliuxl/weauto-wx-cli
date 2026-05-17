from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

from .config import AppConfig, RegionRatio
from .models import ChatRow
from .window import WindowBounds

if TYPE_CHECKING:
    import numpy as np

    from .ocr import OcrEngine, OcrLine


_TIME_RE = re.compile(r"^(?:\d{1,2}:\d{2}|\d{1,2}/\d{1,2}|星期[一二三四五六日天])$")
_UNREAD_NUM_RE = re.compile(r"^\d{1,3}$")


def normalize_title(text: str) -> str:
    return re.sub(r"[\s:：,，。.!！?？@]+", "", text or "").lower()


def title_matches(expected: str, actual: str) -> bool:
    exp = normalize_title(expected)
    act = normalize_title(actual)
    if not exp or not act:
        return False
    return exp == act or exp in act or act in exp


def _title_quality(title: str) -> int:
    t = (title or "").strip()
    if not t:
        return -99
    score = len(t)
    if any("\u4e00" <= ch <= "\u9fff" for ch in t):
        score += 3
    if _UNREAD_NUM_RE.match(t):
        score -= 8
    if t in {"群", "群-", "群—"}:
        score -= 7
    if t.endswith(("-", "—", ":", "：", "/", "／")):
        score -= 4
    return score


def _pick_better_title(base_title: str, region_title: str) -> str:
    base = (base_title or "").strip()
    region = (region_title or "").strip()
    if not region:
        return base
    if not base:
        return region
    return region if _title_quality(region) >= _title_quality(base) else base


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _normalize_text(lines: list[OcrLine]) -> list[str]:
    values: list[str] = []
    for line in lines:
        txt = _clean_text(line.text)
        if txt:
            values.append(txt)
    return values


def _fingerprint(values: list[str]) -> str:
    return hashlib.sha1(" | ".join(values).encode("utf-8")).hexdigest()


def _load_manual_row_boxes(cfg: AppConfig, bounds: WindowBounds) -> list[tuple[int, int, int, int]]:
    enabled = bool(cfg.use_manual_row_boxes or cfg.manual_rows.enabled)
    if not enabled:
        return []
    path = Path(cfg.manual_row_boxes_path or cfg.manual_rows.path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    boxes_raw = raw.get("boxes", []) if isinstance(raw, dict) else []
    if not isinstance(boxes_raw, list):
        return []

    boxes: list[tuple[int, int, int, int]] = []
    for item in boxes_raw:
        if not isinstance(item, dict):
            continue
        try:
            rx = float(item.get("x", 0.0))
            ry = float(item.get("y", 0.0))
            rw = float(item.get("w", 0.0))
            rh = float(item.get("h", 0.0))
        except Exception:
            continue
        x = int(bounds.width * max(0.0, min(1.0, rx)))
        y = int(bounds.height * max(0.0, min(1.0, ry)))
        w = int(bounds.width * max(0.0, min(1.0, rw)))
        h = int(bounds.height * max(0.0, min(1.0, rh)))
        if w < 20 or h < 12 or x >= bounds.width or y >= bounds.height:
            continue
        boxes.append((x, y, min(w, max(1, bounds.width - x)), min(h, max(1, bounds.height - y))))
    return boxes


def _estimate_row_start_y(ocr_lines: list[OcrLine], list_w: int, row_height: int) -> int:
    candidates: list[float] = []
    for line in ocr_lines:
        txt = (line.text or "").strip()
        if not txt or _TIME_RE.match(txt):
            continue
        if "=" in txt and len(txt) >= 8:
            continue
        if not (list_w * 0.16 <= line.x_center <= list_w * 0.84):
            continue
        candidates.append(float(line.y_center))
    if not candidates:
        return 0
    start = int(round(min(candidates) - (row_height * 0.46))) - 2
    max_shift = int(row_height * 0.42)
    return max(0, min(max_shift, start))


def _extract_title_preview(values: list[str]) -> tuple[str, str]:
    cleaned = [_clean_text(v) for v in values if _clean_text(v)]
    cleaned = [v for v in cleaned if not _TIME_RE.match(v)]
    while len(cleaned) >= 2 and _UNREAD_NUM_RE.match(cleaned[0].strip()):
        if _UNREAD_NUM_RE.match(cleaned[1].strip()):
            break
        cleaned = cleaned[1:]
    if not cleaned:
        return "", ""

    title = cleaned[0]
    preview = " ".join(cleaned[1:]) if len(cleaned) > 1 else ""

    def _is_sender_prefixed(text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        sep = "：" if "：" in raw else (":" if ":" in raw else "")
        if not sep:
            return False
        left = raw.split(sep, 1)[0].strip(" []【】()（）")
        return 1 <= len(left) <= 24

    if len(cleaned) >= 2:
        first = cleaned[0].strip()
        second = cleaned[1].strip()
        first_prefixed = _is_sender_prefixed(first)
        second_prefixed = _is_sender_prefixed(second)
        if first_prefixed and (not second_prefixed) and 1 <= len(second) <= 24 and len(first) >= 10:
            title = second
            preview = " ".join([first, *cleaned[2:]]).strip()

    if _UNREAD_NUM_RE.match((title or "").strip()) and preview:
        match = re.match(r"^\s*([^\s]{1,36})\s+(.+?)\s*$", preview)
        if match:
            title = match.group(1).strip()
            preview = match.group(2).strip()

    return _clean_text(title), _clean_text(preview)


def _extract_text_from_region(
    row_img_bgr: np.ndarray,
    region: RegionRatio,
    ocr_engine: OcrEngine,
    *,
    title_mode: bool,
) -> str:
    h, w = row_img_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return ""
    x1 = int(w * max(0.0, min(1.0, region.x)))
    box_w = int(w * max(0.0, min(1.0, region.w)))
    box_h = int(h * max(0.0, min(1.0, region.h)))
    y_from_bottom = int(h * max(0.0, min(1.0, region.y)))
    y2 = h - y_from_bottom
    y1 = y2 - box_h
    x2 = x1 + box_w
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return ""
    roi = row_img_bgr[y1:y2, x1:x2]
    if roi.size == 0:
        return ""
    values = [v for v in _normalize_text(ocr_engine.detect_lines(roi)) if v and not _TIME_RE.match(v)]
    if not values:
        return ""
    if not title_mode:
        return " ".join(values).strip()

    parts = [x.strip() for x in values if x and x.strip()]
    if not parts:
        return ""
    if _UNREAD_NUM_RE.match(parts[0]) and len(parts) >= 2:
        parts = parts[1:]
    if not parts:
        return ""
    title = parts[0]
    for nxt in parts[1:3]:
        if (
            title.endswith(("-", "—", ":", "：", "/", "／"))
            or title in {"群", "群-", "群—"}
            or len(title) <= 2
        ):
            title = f"{title}{nxt}"
        else:
            break
    return title[:48]


def _extract_title_from_region(row_img_bgr: np.ndarray, cfg: AppConfig, ocr_engine: OcrEngine) -> str:
    if not cfg.row_title_region_enabled:
        return ""
    return _extract_text_from_region(row_img_bgr, cfg.row_title_region, ocr_engine, title_mode=True)


def _extract_preview_from_region(row_img_bgr: np.ndarray, cfg: AppConfig, ocr_engine: OcrEngine) -> str:
    if not cfg.preview_region_enabled:
        return ""
    return _extract_text_from_region(row_img_bgr, cfg.preview_text_region, ocr_engine, title_mode=False)


def _row_to_chat(
    *,
    idx: int,
    bounds: WindowBounds,
    values: list[str],
    title: str,
    preview: str,
    click_x: int,
    click_y: int,
) -> ChatRow | None:
    title = _clean_text(title)
    preview = _clean_text(preview)
    if not title and not preview:
        return None
    return ChatRow(
        row_idx=idx,
        title=title,
        preview=preview,
        text=" ".join(values),
        click_x_ratio=max(0.0, min(1.0, click_x / max(1.0, bounds.width))),
        click_y_ratio=max(0.0, min(1.0, click_y / max(1.0, bounds.height))),
        fingerprint=_fingerprint(values if values else [title, preview]),
    )


def detect_chat_rows(
    image_rgb: np.ndarray,
    bounds: WindowBounds,
    cfg: AppConfig,
    ocr: OcrEngine,
) -> list[ChatRow]:
    import cv2

    img_h, img_w = image_rgb.shape[:2]
    scale_x = float(img_w) / float(max(1, bounds.width))
    scale_y = float(img_h) / float(max(1, bounds.height))
    if scale_x <= 0 or scale_y <= 0:
        scale_x = 1.0
        scale_y = 1.0

    manual_boxes = _load_manual_row_boxes(cfg, bounds)
    if manual_boxes:
        rows: list[ChatRow] = []
        for idx, (bx, by, bw, bh) in enumerate(manual_boxes):
            sx = max(0, min(img_w - 1, int(round(bx * scale_x))))
            sy = max(0, min(img_h - 1, int(round(by * scale_y))))
            sw = max(1, min(img_w - sx, int(round(bw * scale_x))))
            sh = max(1, min(img_h - sy, int(round(bh * scale_y))))
            row_rgb = image_rgb[sy : sy + sh, sx : sx + sw]
            if row_rgb.size == 0:
                continue
            row_bgr = cv2.cvtColor(row_rgb, cv2.COLOR_RGB2BGR)
            values = _normalize_text(ocr.detect_lines(row_bgr))
            title, preview = _extract_title_preview(values)
            region_title = _extract_title_from_region(row_bgr, cfg, ocr)
            if region_title:
                title = _pick_better_title(title, region_title)
            region_preview = _extract_preview_from_region(row_bgr, cfg, ocr)
            if region_preview:
                preview = region_preview
            row = _row_to_chat(
                idx=idx,
                bounds=bounds,
                values=values,
                title=title,
                preview=preview,
                click_x=bx + int(bw * 0.24),
                click_y=by + (bh // 2),
            )
            if row is not None:
                rows.append(row)
        return rows

    x = int(bounds.width * cfg.list_region.x * scale_x)
    y = int(bounds.height * cfg.list_region.y * scale_y)
    w = int(bounds.width * cfg.list_region.w * scale_x)
    h = int(bounds.height * cfg.list_region.h * scale_y)
    list_rgb = image_rgb[y : y + h, x : x + w]
    if list_rgb.size == 0:
        return []
    list_bgr = cv2.cvtColor(list_rgb, cv2.COLOR_RGB2BGR)
    lines = ocr.detect_lines(list_bgr)

    non_chat_like = 0
    for line in lines:
        txt = (line.text or "").strip()
        if txt and ("=" in txt or txt.startswith("/Users") or txt.startswith("[vision]")):
            non_chat_like += 1
    if non_chat_like >= 4 and len(lines) >= 4:
        return []

    row_height = max(20, int(h * cfg.row_height_ratio))
    start_y = _estimate_row_start_y(lines, w, row_height)
    rows: list[ChatRow] = []
    for idx in range(max(1, cfg.rows_max)):
        top = start_y + idx * row_height
        bottom = min(h, top + row_height)
        if top >= h:
            break
        bucket = [line for line in lines if top <= line.y_center < bottom and 0 <= line.x_center < w]
        values = _normalize_text(bucket)
        title, preview = _extract_title_preview(values)
        if not title and not preview:
            continue
        row_bgr = list_bgr[top:bottom, :]
        region_title = _extract_title_from_region(row_bgr, cfg, ocr)
        if region_title:
            title = _pick_better_title(title, region_title)
        region_preview = _extract_preview_from_region(row_bgr, cfg, ocr)
        if region_preview:
            preview = region_preview
        window_click_x = int((x + int(w * 0.24)) / max(1.0, scale_x))
        window_click_y = int((y + ((top + bottom) // 2)) / max(1.0, scale_y))
        row = _row_to_chat(
            idx=idx,
            bounds=bounds,
            values=values,
            title=title,
            preview=preview,
            click_x=window_click_x,
            click_y=window_click_y,
        )
        if row is not None:
            rows.append(row)
    return rows


def _pick_title_preview(values: list[str]) -> tuple[str, str]:
    return _extract_title_preview(values)
