from __future__ import annotations

import json
import shlex
import subprocess

from .config import AppConfig
from .models import WxMessage


class ReplyGenerator:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def generate(self, msg: WxMessage) -> str:
        if self.cfg.reply_mode == "command":
            return self._command_reply(msg)
        return self._template_reply(msg)

    def _template_reply(self, msg: WxMessage) -> str:
        return self.cfg.reply_template.format(
            chat_title=msg.chat_title,
            sender=msg.sender,
            text=msg.text,
            timestamp=msg.timestamp,
        ).strip()

    def _command_reply(self, msg: WxMessage) -> str:
        if not self.cfg.reply_command.strip():
            return ""
        payload = {
            "chat_title": msg.chat_title,
            "sender": msg.sender,
            "text": msg.text,
            "timestamp": msg.timestamp,
            "raw": msg.raw,
        }
        proc = subprocess.run(
            shlex.split(self.cfg.reply_command),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "reply command failed")
        return proc.stdout.strip()
