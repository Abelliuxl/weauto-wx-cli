from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from .image_resolver import ImageResolver
from .models import Attachment, WxMessage


class WxCliError(RuntimeError):
    pass


class WxCliClient:
    def __init__(
        self,
        binary: str = "wx",
        timeout_sec: float = 15.0,
        resolve_images: bool = True,
        image_output_dir: str = "data/images",
    ) -> None:
        self.binary = binary
        self.timeout_sec = timeout_sec
        self.resolve_images = resolve_images
        self._image_output_dir = image_output_dir
        self._resolver: ImageResolver | None = None

    def is_installed(self) -> bool:
        return shutil.which(self.binary) is not None

    def run(self, *args: str, json_output: bool = True) -> Any:
        cmd = [self.binary, *args]
        if json_output and "--json" not in cmd:
            cmd.append("--json")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_sec,
        )
        if proc.returncode != 0:
            msg = proc.stderr.strip() or proc.stdout.strip() or f"{cmd!r} failed"
            raise WxCliError(msg)
        out = proc.stdout.strip()
        if not json_output:
            return out
        if not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return self._parse_text_messages(out)

    def sessions(self) -> list[dict[str, Any]]:
        return _as_list(self.run("sessions"))

    def unread(self) -> list[WxMessage]:
        return normalize_messages(self.run("unread"))

    def new_messages(self) -> list[WxMessage]:
        return normalize_messages(self.run("new-messages"))

    def history(self, chat_title: str, limit: int = 20) -> list[WxMessage]:
        raw = self.run("history", chat_title, "-n", str(limit))
        res = self.resolver if self.resolve_images else None
        return normalize_messages(raw, default_title=chat_title, resolver=res)

    def search(self, query: str, limit: int = 10) -> list[WxMessage]:
        raw = self.run("search", query, "-n", str(limit))
        res = self.resolver if self.resolve_images else None
        return normalize_messages(raw, resolver=res)

    @property
    def resolver(self) -> ImageResolver:
        if self._resolver is None:
            self._resolver = ImageResolver(output_dir=self._image_output_dir)
        return self._resolver

    def collect_incoming(self) -> list[WxMessage]:
        try:
            messages = self.new_messages()
        except WxCliError as exc:
            raise WxCliError(str(exc)) from exc
        deduped: dict[str, WxMessage] = {}
        for msg in messages:
            if msg.chat_title and (msg.text or msg.attachments):
                deduped[msg.fingerprint()] = msg

        resolved = list(deduped.values())
        if self.resolve_images:
            for msg in resolved:
                new_attachments = self.resolver.resolve(msg)
                if new_attachments:
                    msg.attachments = new_attachments
        return resolved

    @staticmethod
    def _parse_text_messages(text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        current_title = ""
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                continue
            title_match = re.match(r"^(?:会话|session|chat|title)[:：]\s*(.+)$", clean, re.I)
            if title_match:
                current_title = title_match.group(1).strip()
                continue
            msg_match = re.match(r"^(?:(?P<sender>[^:：]{1,32})[:：])?\s*(?P<text>.+)$", clean)
            if msg_match:
                rows.append(
                    {
                        "chat_title": current_title,
                        "sender": (msg_match.group("sender") or "").strip(),
                        "text": (msg_match.group("text") or "").strip(),
                    }
                )
        return rows


def _as_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("messages", "data", "items", "rows", "sessions", "result"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        return [raw]
    return []


def _first_str(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_bool(raw: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        if key in raw:
            return bool(raw.get(key))
    return False


def _first_obj(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw:
            return raw.get(key)
    return None


def _looks_like_image_ref(value: str) -> bool:
    raw = value.strip().lower()
    return raw.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"))


def _looks_like_image_text(value: str) -> bool:
    raw = value.strip().lower()
    if not raw:
        return False
    markers = ("[图片]", "[image]", "图片", "image", "<img", "msgtype=\"3\"", "msgtype='3'")
    return any(marker in raw for marker in markers)


def _normalize_message_type(raw_type: str, text: str, attachments: list[Attachment]) -> str:
    value = raw_type.strip().lower()
    if value in {"image", "img", "photo", "picture", "pic", "图片", "3"}:
        return "image"
    if value in {"video", "视频", "43", "mp4"}:
        return "video"
    if value in {"file", "attachment", "链接/文件", "文件", "49"}:
        return "file"
    if value in {"system", "系统", "10000"}:
        return "system"
    if attachments:
        return attachments[0].type
    if _looks_like_image_text(text):
        return "image"
    return "text"


def _attachment_type_from_ref(value: str, fallback: str = "file") -> str:
    if _looks_like_image_ref(value):
        return "image"
    raw = value.strip().lower()
    if raw.endswith((".mp4", ".mov", ".m4v")):
        return "video"
    return fallback


def _collect_attachments(value: Any, out: list[Attachment], *, parent_key: str = "") -> None:
    if value is None:
        return
    if isinstance(value, dict):
        path = _first_str(
            value,
            (
                "image_path",
                "imagePath",
                "media_path",
                "mediaPath",
                "file_path",
                "filePath",
                "local_path",
                "localPath",
                "thumb_path",
                "thumbPath",
                "path",
            ),
        )
        url = _first_str(value, ("url", "image_url", "imageUrl", "thumb", "thumb_url", "thumbUrl"))
        name = _first_str(value, ("name", "file_name", "fileName", "filename"))
        raw_type = _first_str(value, ("type", "media_type", "mediaType", "msg_type", "msgType"))
        if path or url:
            inferred = _normalize_message_type(raw_type, path or url, [])
            if inferred == "text":
                inferred = _attachment_type_from_ref(path or url)
            out.append(Attachment(type=inferred, path=path, url=url, name=name, raw=value))

        for key, child in value.items():
            if key in {"raw"}:
                continue
            _collect_attachments(child, out, parent_key=str(key))
        return
    if isinstance(value, list):
        for item in value:
            _collect_attachments(item, out, parent_key=parent_key)
        return
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return
        key = parent_key.lower()
        if (
            key.endswith("path")
            or key.endswith("url")
            or "image" in key
            or "media" in key
            or "thumb" in key
        ):
            if clean.startswith(("http://", "https://")):
                out.append(Attachment(type=_attachment_type_from_ref(clean, "image"), url=clean))
            elif _looks_like_image_ref(clean) or "/" in clean:
                out.append(Attachment(type=_attachment_type_from_ref(clean), path=clean))


def _dedupe_attachments(items: list[Attachment]) -> list[Attachment]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Attachment] = []
    for item in items:
        key = (item.type, item.path, item.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def normalize_messages(
    raw: Any, *, default_title: str = "", resolver: ImageResolver | None = None
) -> list[WxMessage]:
    rows = _as_list(raw)
    messages: list[WxMessage] = []
    for item in rows:
        if not isinstance(item, dict):
            text = str(item).strip()
            if text:
                message_type = "image" if _looks_like_image_text(text) else "text"
                messages.append(
                    WxMessage(chat_title=default_title, text=text, message_type=message_type)
                )
            continue

        chat_title = _first_str(
            item,
            (
                "chat_title",
                "chatTitle",
                "session_title",
                "sessionTitle",
                "session",
                "chat",
                "room_name",
                "roomName",
                "nickname",
                "name",
                "title",
                "talker",
                "username",
            ),
        ) or default_title
        text = _first_str(
            item,
            (
                "text",
                "content",
                "message",
                "msg",
                "body",
                "preview",
                "last_message",
                "lastMessage",
            ),
        )
        sender = _first_str(item, ("sender", "from", "from_user", "fromUser", "speaker", "displayName"))
        timestamp = _first_str(item, ("timestamp", "time", "createTime", "created_at", "datetime"))
        local_id = _first_str(item, ("local_id", "localId"))
        message_id = _first_str(item, ("id", "msg_id", "msgId", "message_id", "messageId"))
        chat_type = _first_str(item, ("chat_type", "chatType", "session_type", "sessionType"))
        raw_type = _first_str(
            item,
            (
                "message_type",
                "messageType",
                "msg_type",
                "msgType",
                "type",
                "msgTypeName",
            ),
        )
        attachments: list[Attachment] = []
        _collect_attachments(item, attachments)
        for key in ("attachments", "media", "medias", "files", "images", "image", "file", "content"):
            _collect_attachments(_first_obj(item, (key,)), attachments, parent_key=key)
        attachments = _dedupe_attachments(attachments)
        message_type = _normalize_message_type(raw_type, text, attachments)
        if message_type == "image" and local_id and not attachments:
            attachments.append(
                Attachment(
                    type="image",
                    raw={
                        "local_id": local_id,
                        "content": text,
                        "note": "wx-cli exposed image local_id but no local file path",
                    },
                )
            )
        if not text and message_type == "image":
            text = "[图片]"
        if local_id and not message_id:
            msg_scope = _first_str(item, ("username", "chat", "session", "title")) or default_title
            message_id = f"local:{msg_scope}:{local_id}"
        is_self = _first_bool(item, ("is_self", "isSelf", "from_self", "fromSelf"))
        if chat_title or text or attachments:
            messages.append(
                WxMessage(
                    chat_title=chat_title,
                    text=text,
                    sender=sender,
                    timestamp=timestamp,
                    message_id=message_id,
                    chat_type=chat_type,
                    message_type=message_type,
                    attachments=attachments,
                    is_self=is_self,
                    raw=item,
                )
            )
    if resolver:
        for msg in messages:
            new_attachments = resolver.resolve(msg)
            if new_attachments:
                msg.attachments = new_attachments
    return messages
