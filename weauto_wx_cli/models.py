from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class RegionRatio:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class PointRatio:
    x: float
    y: float


@dataclass
class Attachment:
    type: str
    path: str = ""
    url: str = ""
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.path:
            payload["path"] = self.path
        if self.url:
            payload["url"] = self.url
        if self.name:
            payload["name"] = self.name
        if self.raw:
            payload["raw"] = self.raw
        return payload


@dataclass
class WxMessage:
    chat_title: str
    text: str
    sender: str = ""
    timestamp: str = ""
    message_id: str = ""
    chat_type: str = ""
    message_type: str = "text"
    attachments: list[Attachment] = field(default_factory=list)
    is_self: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        if self.message_id:
            return f"id:{self.message_id}"
        payload = {
            "chat_title": self.chat_title,
            "sender": self.sender,
            "timestamp": self.timestamp,
            "text": self.text,
            "message_type": self.message_type,
            "attachments": [item.to_payload() for item in self.attachments],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "source": "wechat",
            "chat_title": self.chat_title,
            "chat_type": self.chat_type,
            "sender": self.sender,
            "timestamp": self.timestamp,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "text": self.text,
            "attachments": [item.to_payload() for item in self.attachments],
            "raw": self.raw,
        }


@dataclass
class ChatRow:
    row_idx: int
    title: str
    preview: str
    text: str
    click_x_ratio: float
    click_y_ratio: float
    fingerprint: str


@dataclass
class OutboundReply:
    chat_title: str
    message: str
    source_fingerprint: str
