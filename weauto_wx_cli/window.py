from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import os


@dataclass
class WindowBounds:
    x: int
    y: int
    width: int
    height: int
    window_id: int


class WindowNotFoundError(RuntimeError):
    pass


def _autorelease_pool():
    try:
        import objc
    except Exception:
        return nullcontext()
    return objc.autorelease_pool()


def get_front_window_bounds(app_name: str) -> WindowBounds:
    import Quartz

    aliases = [x.strip() for x in app_name.split("|") if x.strip()]
    if "WeChat" in aliases and "微信" not in aliases:
        aliases.append("微信")

    candidates: list[WindowBounds] = []
    with _autorelease_pool():
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
        )
        if window_list is None:
            raise WindowNotFoundError(
                "Unable to query macOS windows. Grant Screen Recording/Accessibility permissions."
            )
        for window in window_list:
            owner = str(window.get("kCGWindowOwnerName", ""))
            if not any(alias.lower() in owner.lower() for alias in aliases):
                continue
            if int(window.get("kCGWindowLayer", 0)) != 0:
                continue
            raw_bounds = window.get("kCGWindowBounds", {})
            width = int(raw_bounds.get("Width", 0))
            height = int(raw_bounds.get("Height", 0))
            if width <= 0 or height <= 0:
                continue
            candidates.append(
                WindowBounds(
                    x=int(raw_bounds.get("X", 0)),
                    y=int(raw_bounds.get("Y", 0)),
                    width=width,
                    height=height,
                    window_id=int(window.get("kCGWindowNumber", 0)),
                )
            )
    if not candidates:
        raise WindowNotFoundError(
            f"WeChat window not found for app_name={app_name!r}. Open WeChat and keep it visible."
        )
    return max(candidates, key=lambda item: item.width * item.height)


def screenshot_region(left: int, top: int, width: int, height: int):
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid screenshot region: width={width} height={height}")
    import pyautogui

    if os.environ.get("WEAUTO_WX_SCREENSHOT_BACKEND", "").strip().lower() == "pyautogui":
        return pyautogui.screenshot(region=(left, top, width, height))

    try:
        import Quartz
        from PIL import Image

        with _autorelease_pool():
            rect = Quartz.CGRectMake(int(left), int(top), int(width), int(height))
            cg_img = Quartz.CGWindowListCreateImage(
                rect,
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
                Quartz.kCGWindowImageDefault,
            )
            if cg_img is None:
                raise RuntimeError("empty CGImage")
            w = int(Quartz.CGImageGetWidth(cg_img))
            h = int(Quartz.CGImageGetHeight(cg_img))
            bytes_per_row = int(Quartz.CGImageGetBytesPerRow(cg_img))
            provider = Quartz.CGImageGetDataProvider(cg_img)
            data = Quartz.CGDataProviderCopyData(provider)
            return Image.frombytes("RGBA", (w, h), bytes(data), "raw", "BGRA", bytes_per_row, 1)
    except Exception:
        return pyautogui.screenshot(region=(left, top, width, height))
