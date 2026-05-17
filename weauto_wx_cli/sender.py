from __future__ import annotations

from contextlib import contextmanager
import fcntl
import subprocess
import threading
import time
from pathlib import Path

from .config import AppConfig
from .detector import detect_chat_rows, title_matches
from .models import ChatRow
from .window import WindowBounds, get_front_window_bounds, screenshot_region


class SendError(RuntimeError):
    pass


class WeChatSender:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._ocr: OcrEngine | None = None
        self._thread_lock = threading.RLock()
        self._lock_depth = 0

    @property
    def ocr(self):
        if self._ocr is None:
            from .ocr import OcrEngine

            self._ocr = OcrEngine(self.cfg.ocr)
        return self._ocr

    def list_visible_chats(self) -> list[ChatRow]:
        import numpy as np

        self.activate()
        time.sleep(self.cfg.activate_wait_sec)
        bounds = get_front_window_bounds(self.cfg.app_name)
        shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height)
        image_rgb = np.array(shot.convert("RGB"))
        return detect_chat_rows(image_rgb, bounds, self.cfg, self.ocr)

    def send_message(self, chat_title: str, message: str) -> bool:
        with self.send_lock():
            chat_title = str(chat_title or "").strip()
            message = str(message or "").strip()
            if not chat_title or not message:
                raise SendError("chat_title and message are required")

            if self.cfg.dry_run:
                rows = self.list_visible_chats()
                found = self._find_row(chat_title, rows)
                target = found.title if found else "<not visible>"
                print(f"[dry-run] target={chat_title!r} visible_match={target!r} message={message!r}")
                return found is not None

            bounds, row = self.focus_chat(chat_title)
            input_x = bounds.x + int(bounds.width * self.cfg.input_point.x)
            input_y = bounds.y + int(bounds.height * self.cfg.input_point.y)
            self._safe_click(input_x, input_y)
            time.sleep(max(0.0, self.cfg.post_input_click_wait_sec))
            self._paste_and_send(message)
            print(f"[sent] to={row.title!r} message={message!r}")
            return True

    def send_image(self, chat_title: str, image_path: str) -> bool:
        with self.send_lock():
            chat_title = str(chat_title or "").strip()
            image_path = str(image_path or "").strip()
            if not chat_title or not image_path:
                raise SendError("chat_title and image_path are required")
            path = Path(image_path).expanduser()
            if not path.exists():
                raise SendError(f"image file not found: {path}")

            if self.cfg.dry_run:
                rows = self.list_visible_chats()
                found = self._find_row(chat_title, rows)
                target = found.title if found else "<not visible>"
                print(f"[dry-run] target={chat_title!r} visible_match={target!r} image={str(path)!r}")
                return found is not None

            bounds, row = self.focus_chat(chat_title)
            input_x = bounds.x + int(bounds.width * self.cfg.input_point.x)
            input_y = bounds.y + int(bounds.height * self.cfg.input_point.y)
            self._safe_click(input_x, input_y)
            time.sleep(max(0.0, self.cfg.post_input_click_wait_sec))
            self._paste_image_and_send(path)
            print(f"[sent-image] to={row.title!r} image={str(path)!r}")
            return True

    def focus_chat(self, chat_title: str) -> tuple[WindowBounds, ChatRow]:
        with self.send_lock():
            self.activate()
            time.sleep(self.cfg.activate_wait_sec)
            last_rows: list[ChatRow] = []
            last_header = ""
            for _ in range(max(1, self.cfg.focus_verify_max_clicks)):
                bounds = get_front_window_bounds(self.cfg.app_name)
                rows = self._observe_rows(bounds)
                last_rows = rows
                row = self._find_row(chat_title, rows)
                if row is None:
                    break
                row_x = bounds.x + int(bounds.width * row.click_x_ratio)
                row_y = bounds.y + int(bounds.height * row.click_y_ratio)
                self._safe_click(row_x, row_y)
                time.sleep(max(0.05, self.cfg.post_select_wait_sec))
                bounds = get_front_window_bounds(self.cfg.app_name)
                if not self.cfg.focus_verify_enabled:
                    return bounds, row
                header = self._read_header(bounds)
                last_header = header
                if title_matches(chat_title, header) or title_matches(row.title, header):
                    return bounds, row

            visible = ", ".join(row.title for row in last_rows[:8])
            detail = f"; header={last_header!r}" if last_header else ""
            raise SendError(f"chat not focused: expected={chat_title!r}; visible=[{visible}]{detail}")

    @contextmanager
    def send_lock(self):
        with self._thread_lock:
            if self._lock_depth > 0:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return

            lock_path = Path(self.cfg.send_lock_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _observe_rows(self, bounds: WindowBounds) -> list[ChatRow]:
        import numpy as np

        shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height)
        image_rgb = np.array(shot.convert("RGB"))
        return detect_chat_rows(image_rgb, bounds, self.cfg, self.ocr)

    @staticmethod
    def _find_row(chat_title: str, rows: list[ChatRow]) -> ChatRow | None:
        for row in rows:
            if title_matches(chat_title, row.title):
                return row
        return None

    def _read_header(self, bounds: WindowBounds) -> str:
        import cv2
        import numpy as np

        region = self.cfg.chat_title_region
        x = bounds.x + int(bounds.width * region.x)
        y = bounds.y + int(bounds.height * region.y)
        w = max(1, int(bounds.width * region.w))
        h = max(1, int(bounds.height * region.h))
        shot = screenshot_region(x, y, w, h)
        image_bgr = cv2.cvtColor(np.array(shot.convert("RGB")), cv2.COLOR_RGB2BGR)
        lines = self.ocr.detect_lines(image_bgr)
        return " ".join(line.text.strip() for line in lines if line.text.strip())

    def activate(self) -> None:
        aliases = [x.strip() for x in self.cfg.app_name.split("|") if x.strip()]
        if "WeChat" in aliases and "微信" not in aliases:
            aliases.append("微信")
        for app in aliases or ["WeChat"]:
            proc = subprocess.run(
                ["osascript", "-e", f'tell application "{app}" to activate'],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return
        raise SendError("failed to activate WeChat")

    def _safe_click(self, x: int, y: int) -> None:
        import pyautogui

        pyautogui.moveTo(x, y, duration=max(0.0, self.cfg.click_move_duration_sec))
        pyautogui.mouseDown()
        time.sleep(max(0.0, self.cfg.mouse_down_hold_sec))
        pyautogui.mouseUp()

    def _paste_and_send(self, message: str) -> None:
        import pyperclip

        pyperclip.copy(message)
        time.sleep(0.05)
        self._paste_hotkey()
        time.sleep(max(0.0, self.cfg.post_paste_wait_sec))
        self._enter_hotkey()

    def _paste_image_and_send(self, image_path: Path) -> None:
        self._copy_image_to_clipboard(image_path)
        time.sleep(0.12)
        self._paste_hotkey()
        time.sleep(max(0.25, self.cfg.post_paste_wait_sec))
        self._enter_hotkey()

    @staticmethod
    def _copy_image_to_clipboard(image_path: Path) -> None:
        path = str(image_path.resolve())
        suffix = image_path.suffix.lower()
        if suffix in {".png"}:
            script = f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'
        elif suffix in {".jpg", ".jpeg"}:
            script = f'set the clipboard to (read (POSIX file "{path}") as JPEG picture)'
        elif suffix in {".tif", ".tiff"}:
            script = f'set the clipboard to (read (POSIX file "{path}") as TIFF picture)'
        else:
            script = f'set the clipboard to (POSIX file "{path}")'
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SendError(proc.stderr.strip() or "failed to copy image to clipboard")

    @staticmethod
    def _paste_hotkey() -> None:
        proc = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        import pyautogui

        pyautogui.hotkey("command", "v")

    @staticmethod
    def _enter_hotkey() -> None:
        proc = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 36'],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        import pyautogui

        pyautogui.press("enter")
