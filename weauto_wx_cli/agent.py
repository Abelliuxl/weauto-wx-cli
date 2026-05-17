from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from .agent_store import ChatHistoryStore, HotFile, MemoryStore, PeopleStore, SkillStore
from .config import AppConfig, MainLLMConfig, VisionLLMConfig
from .models import WxMessage
from .people import PeopleContextBuilder


@dataclass
class AgentResult:
    actions: list[dict[str, Any]]
    raw_response: str


class AgentError(RuntimeError):
    pass


ACTION_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a text message to a WeChat chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Exact WeChat chat title."},
                    "message": {"type": "string", "description": "Message text to send."},
                },
                "required": ["title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_image",
            "description": "Send a local image file to a WeChat chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Exact WeChat chat title."},
                    "image_path": {"type": "string", "description": "Local image path."},
                },
                "required": ["title", "image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_chat",
            "description": "Focus/select a WeChat chat without sending a message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Exact WeChat chat title."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "noop",
            "description": "Take no visible action.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "Replace or merge durable agent memory. name must be core or timeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": ["core", "timeline"]},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_skill",
            "description": "Save a reusable local strategy/procedure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_skill",
            "description": "Delete an obsolete saved skill.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_impression",
            "description": "Read one person's stored impression before replying or updating it.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Canonical Chinese name."}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_chat_history",
            "description": "Read recent WeChat chat history for a chat. Use this instead of guessing local message file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_title": {"type": "string", "description": "Exact WeChat chat title. Defaults to the inbound chat when omitted."},
                    "limit": {"type": "integer", "description": "Number of recent messages to read, between 1 and 100."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_impression",
            "description": "Replace a person's stored impression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Canonical Chinese name."},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch raw HTML/text from a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "proxy": {"type": "boolean"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web with Tavily.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "proxy": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_brave",
            "description": "Search the web with Brave.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "proxy": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web_volc",
            "description": "Search the web with Volcengine Ark's built-in web search.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse_url",
            "description": "Open a rendered page with Playwright and return visible text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "proxy": {"type": "boolean"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file inside the project directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List project files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
]

FINAL_ACTION_TOOL_SPECS: list[dict[str, Any]] = [
    spec for spec in ACTION_TOOL_SPECS
    if spec.get("function", {}).get("name") in {"send_message", "noop"}
]


class LLMClient:
    def __init__(self, cfg: MainLLMConfig | VisionLLMConfig) -> None:
        self.api_key = cfg.api_key or os.environ.get(cfg.api_key_env or "", "") or ""
        self.model = cfg.model
        self.timeout_sec = cfg.timeout_sec
        self.max_tokens = cfg.max_tokens
        self.base_url = self._resolve_base_url(cfg)

    @staticmethod
    def _resolve_base_url(cfg: MainLLMConfig | VisionLLMConfig) -> str:
        env_key = cfg.api_key_env or ""
        env_base_var = f"{env_key.split('_', 1)[0]}_BASE_URL" if "_" in env_key else ""
        if cfg.base_url:
            return cfg.base_url.rstrip("/")
        if env_base_var:
            env_val = os.environ.get(env_base_var, "")
            if env_val:
                return env_val.rstrip("/")
        return ""

    def _url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith(("/v1", "/v2", "/v3")):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens, "stream": False}
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        if response_format:
            body["response_format"] = response_format
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(self._url(), data=data, headers=self._headers(), method="POST")
        host = (urlparse(self._url()).hostname or "").lower()
        try:
            opener = None
            if host in {"127.0.0.1", "localhost", "::1"}:
                opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
            open_fn = opener.open if opener is not None else urllib_request.urlopen
            with open_fn(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise AgentError(f"LLM http {exc.code}: {raw[:500]}")
        except urllib_error.URLError as exc:
            raise AgentError(f"LLM request failed: {exc.reason}")
        except (TimeoutError, OSError) as exc:
            raise AgentError(f"LLM request failed: {exc}")
        payload = json.loads(raw)
        choices = payload.get("choices", [])
        if not choices:
            raise AgentError("LLM returned empty choices")
        return choices[0]


class Agent:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._main_llm = LLMClient(cfg.agent.main)
        self._vision_llm = LLMClient(cfg.agent.vision)
        self._people_builder: PeopleContextBuilder | None = None
        self.memory = MemoryStore()
        self.skills = SkillStore()
        self.skills.cleanup()
        self.people = PeopleStore(cfg.people.memory_dir)
        self.people.cleanup()
        self.chat_history = ChatHistoryStore()
        self._identity_file = HotFile(cfg.agent.identity_path)
        self._personality_file = HotFile(cfg.agent.personality_path)
        self._alias_resolver: Any | None = None
        self._self_name = (cfg.self_names or ["助手"])[0]
        self._own_identifiers: set[str] = set()

    def add_self_identifiers(self, wxid: str, display: str) -> None:
        for v in (wxid, display):
            if v.strip():
                self._own_identifiers.add(v.strip())
        self._bootstrap_chat_history()

    def _resolve_sender(self, raw_sender: str) -> str:
        if not raw_sender:
            return ""
        if raw_sender in self._own_identifiers:
            return self._self_name
        try:
            if self._alias_resolver is None:
                from .people import PersonAliasResolver
                self._alias_resolver = PersonAliasResolver(self.cfg.people.aliases_path)
            canonical = self._alias_resolver.resolve(raw_sender)
            return canonical or raw_sender
        except Exception:
            return raw_sender

    def _bootstrap_chat_history(self) -> None:
        import subprocess, json
        BIN = self.cfg.wx_binary or "wx"
        try:
            proc = subprocess.run([BIN, "sessions", "--json"], capture_output=True, text=True, timeout=10)
            sessions = json.loads(proc.stdout or "[]")
            if not isinstance(sessions, list):
                return
            for s in sessions:
                title = s.get("chat", "")
                chat_type = str(s.get("chat_type", "") or "")
                if not title:
                    continue
                try:
                    hist_proc = subprocess.run([BIN, "history", title, "-n", "30", "--json"],
                                               capture_output=True, text=True, timeout=15)
                    msgs = json.loads(hist_proc.stdout or "[]")
                    if not isinstance(msgs, list):
                        continue
                    seen: set[str] = set()
                    for m in msgs:
                        raw_sender = str(m.get("sender", "") or "").strip()
                        if not raw_sender and chat_type == "private":
                            raw_sender = title
                        sender = self._resolve_sender(raw_sender)
                        text = str(m.get("text", m.get("content", "")) or "").strip()
                        ts = str(m.get("time", m.get("timestamp", "")) or "")
                        if text:
                            key = f"{sender}:{text}:{ts}"
                            if key not in seen:
                                seen.add(key)
                                self.chat_history.append(title, sender, text, ts)
                except Exception:
                    continue
        except Exception:
            pass

    # ── Message handling ──────────────────────────────────────────

    def handle_message(self, msg: WxMessage) -> AgentResult:
        if not self.cfg.agent.enabled:
            return AgentResult(actions=[], raw_response="")
        raw_sender = msg.sender or (msg.chat_title if msg.chat_type == "private" else "")
        sender = self._resolve_sender(raw_sender)
        self.chat_history.append(msg.chat_title, sender, msg.text, msg.timestamp)
        payload = self._build_payload(msg)
        vision_note = self._pre_analyze_images(msg)
        context = self._build_context(msg)
        messages = self._build_messages(payload, context, vision_note)
        return self._call_llm(messages, default_title=msg.chat_title)

    # ── Heartbeat ─────────────────────────────────────────────────

    def heartbeat(self) -> AgentResult:
        if not self.cfg.agent.enabled or not self.cfg.agent.heartbeat_enabled:
            return AgentResult(actions=[], raw_response="")
        context = self._build_context()
        prompt = (
            f"[system]\n{self.cfg.agent.system_prompt}\n\n"
            f"[identity]\n{self._identity()}\n\n"
            f"[current state]\n{context}\n\n"
            "This is a scheduled self-reflection heartbeat. "
            "Scan the [recent chat activity] section below. For EVERY person who appears there, "
            "update or create their impression with new observations from today's conversations. "
            "People impressions are not preloaded; use read_impression for relevant people before updating "
            "an existing impression. Maintain memory only through write_memory name=core or name=timeline.\n\n"
            "IMPORTANT write_memory rules:\n"
            "- write_memory FULLY REPLACES either data/memory/core.md or data/memory/timeline.md\n"
            "- name=core stores stable facts, preferences, self-improvement rules, and durable knowledge\n"
            "- name=timeline stores dated events and recent activity, newest first when practical\n\n"
            "IMPORTANT write_impression rules:\n"
            "- write_impression FULLY REPLACES the entire impression for that person\n"
            "- Use read_impression first when preserving an existing person record matters\n"
            "- Use canonical Chinese name (NOT a file path)\n"
            "- Recommended markdown structure:\n"
            "  ## 基本特征\n  Personality, background, habits.\n"
            "  ## 事件纪要\n  Key past interactions with approximate dates.\n"
            "  ## 人物关系\n  Relationships to other known people.\n\n"
            "Use the registered tools for actions. If tool calls are unavailable, return JSON actions. "
            "Supported: send_message, write_memory, write_skill, read_impression, write_impression, noop."
        )
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": "Heartbeat tick. Reflect and act."}]
        return self._call_llm(messages, allow_internal=True)

    # ── Payload & context ──────────────────────────────────────────

    def _build_payload(self, msg: WxMessage) -> dict[str, Any]:
        event = msg.to_event_payload()
        ctx = self._people_context(msg)
        event.update(ctx)
        return {"type": "wechat_message", "event": event}

    def _people_context(self, msg: WxMessage) -> dict[str, Any]:
        if not self.cfg.people.enabled:
            return {}
        if self._people_builder is None:
            self._people_builder = PeopleContextBuilder(
                aliases_path=self.cfg.people.aliases_path, memory_dir=self.cfg.people.memory_dir,
                max_items=self.cfg.people.max_items,
            )
        return self._people_builder.build(sender=msg.sender, text=msg.text)

    def _build_context(self, msg: WxMessage | None = None) -> str:
        parts: list[str] = []
        mem = self.memory.read("core")
        if mem:
            parts.append(f"[memories]\n{mem.strip()}")
        tim = self.memory.read("timeline")
        if tim:
            parts.append(f"[timeline]\n{tim.strip()}")
        people_names = self.people.list()
        if people_names:
            parts.append(
                "[people impressions]\n"
                "Person impressions are available on demand, not preloaded. "
                "These are records about people, not the agent's core/timeline memory. "
                "Use action read_impression with a canonical name when a person's stored impression is relevant.\n"
                f"Available names: {', '.join(people_names)}"
            )
        skills_list = self.skills.list()
        if skills_list:
            skill_texts: list[str] = []
            for i, s in enumerate(skills_list, 1):
                content = self.skills.read(s)
                if content.strip():
                    skill_texts.append(f"{i}. {s}\n{content.strip()}")
            parts.append(f"[skills ({len(skill_texts)} total)]\n" + "\n\n".join(skill_texts))

        if msg and msg.chat_title:
            try:
                history = self._get_history(msg.chat_title, limit=50)
                if history:
                    parts.append(f"[recent chat history - {msg.chat_title}]\n{history}")
            except Exception:
                pass
        else:
            try:
                all_history = self._get_all_recent(limit=50, max_chats=10)
                if all_history:
                    parts.append(f"[recent chat activity]\n{all_history}")
            except Exception:
                pass
        return "\n\n".join(parts)

    def _get_history(self, chat_title: str, limit: int = 10) -> str:
        import subprocess, json
        from .image_resolver import ImageResolver
        from .models import WxMessage
        BIN = self.cfg.wx_binary or "wx"
        proc = subprocess.run([BIN, "history", chat_title, "-n", str(limit), "--json"],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return ""
        msgs = json.loads(proc.stdout or "[]")
        if not isinstance(msgs, list):
            return ""
        resolver = ImageResolver()
        lines: list[str] = []
        for m in msgs:
            raw_sender = str(m.get("sender", m.get("from", "")) or "").strip()
            sender = self._resolve_sender(raw_sender)
            text = str(m.get("text", m.get("content", "")) or "").strip()
            text = self._format_history_text(text, m)
            ts = str(m.get("time", m.get("timestamp", "")) or "")
            msg_type = str(m.get("type", "") or "").strip()
            tag = f"[{ts}] {sender}: " if sender else f"[{ts}] "
            if msg_type in ("图片", "image") or ("local_id" in m and m["type"] in ("图片", "image")):
                local_id = m.get("local_id")
                if local_id:
                    hist_msg = WxMessage(
                        chat_title=chat_title,
                        text=text,
                        sender=sender,
                        message_type="image",
                        raw=m,
                    )
                    resolved = resolver.resolve(hist_msg)
                    for att in resolved:
                        if att.path:
                            desc = self._analyze_image(att.path)
                            if desc:
                                lines.append(f"{tag}[图片描述: {desc}]")
                                break
                    else:
                        lines.append(f"{tag}{text}")
                else:
                    lines.append(f"{tag}{text}")
            elif sender or text:
                lines.append(f"{tag}{text}")
        return "\n".join(lines[-limit:]) if lines else ""

    def _get_all_recent(self, limit: int = 50, max_chats: int = 8) -> str:
        return self.chat_history.read_all_recent(limit_per_chat=limit, max_chats=max_chats)

    @classmethod
    def _format_history_text(cls, text: str, raw: dict[str, Any]) -> str:
        urls = cls._extract_urls(raw)
        if not urls:
            return text
        existing = set(re.findall(r"https?://\S+", text or ""))
        additions = [u for u in urls if u not in existing]
        if not additions:
            return text
        suffix = " ".join(f"url={u}" for u in additions[:3])
        return f"{text} {suffix}".strip()

    @classmethod
    def _extract_urls(cls, value: Any) -> list[str]:
        urls: list[str] = []

        def walk(v: Any, parent_key: str = "") -> None:
            if isinstance(v, dict):
                for key, child in v.items():
                    walk(child, str(key))
                return
            if isinstance(v, list):
                for item in v:
                    walk(item, parent_key)
                return
            if isinstance(v, str):
                clean = v.strip()
                if not clean:
                    return
                key = parent_key.lower()
                if key.endswith("url") or key in {"url", "link", "href"}:
                    for found in re.findall(r"https?://[^\s\"'<>]+", clean):
                        urls.append(found)
                elif clean.startswith(("http://", "https://")):
                    urls.append(clean)

        walk(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    def _identity(self) -> str:
        text = self.cfg.agent.identity_text
        file_text = self._identity_file.read()
        personality = self._personality_file.read()
        parts: list[str] = []
        used_text = file_text if file_text and not text else text
        if used_text:
            parts.append(f"[identity]\n{used_text}")
        if personality:
            parts.append(f"[personality]\n{personality}")
        return "\n\n".join(parts)

    def _build_messages(self, payload: dict[str, Any], context: str, vision_note: str = "") -> list[dict[str, Any]]:
        event_json = json.dumps(payload, ensure_ascii=False, indent=2)
        identity = self._identity()
        sys_parts = [self.cfg.agent.system_prompt]
        if identity:
            sys_parts.append(f"[identity]\n{identity}")
        sys_parts.append(f"[context]\n{context}")
        user_content = f"Inbound WeChat event JSON:\n{event_json}"
        if vision_note:
            user_content += f"\n\nPre-analyzed image content:\n{vision_note}"
        return [{"role": "system", "content": "\n\n".join(sys_parts)}, {"role": "user", "content": user_content}]

    # ── Pre-analyze images ────────────────────────────────────────

    def _pre_analyze_images(self, msg: WxMessage) -> str:
        notes: list[str] = []
        for att in msg.attachments:
            if att.type == "image" and att.path:
                desc = self._analyze_image(att.path)
                if desc:
                    notes.append(f"[image:{att.path}]: {desc}")
        return "\n".join(notes)

    def _analyze_image(self, path: str) -> str:
        p = Path(path)
        if not p.is_file():
            return ""
        try:
            b64 = base64.b64encode(p.read_bytes()).decode()
            suffix = p.suffix.lower()
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}.get(suffix, "image/png")
            choice = self._vision_llm.chat([{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in detail."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }])
            return choice.get("message", {}).get("content", "") or ""
        except Exception as e:
            return f"[vision error: {e}]"

    # ── LLM call ──────────────────────────────────────────────────

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        allow_internal: bool = False,
        default_title: str = "",
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = "auto",
    ) -> AgentResult:
        try:
            choice = self._main_llm.chat(
                messages,
                tools=ACTION_TOOL_SPECS if tools is None else tools,
                tool_choice=tool_choice,
            )
        except AgentError as exc:
            raise
        raw_response = json.dumps(choice, ensure_ascii=False)
        msg = choice.get("message", {}) if isinstance(choice, dict) else {}
        actions = self._parse_tool_calls(msg, default_title=default_title)
        content = msg.get("content", "")
        if not actions:
            actions = self._parse_actions(content or "", default_title=default_title)
        if not actions:
            actions = self._fallback_parse_longcat(content or "", default_title)
        for a in actions:
            self._execute_internal_action(a)
        return AgentResult(actions=actions, raw_response=raw_response)

    def finalize_response(
        self,
        *,
        chat_title: str,
        original_sender: str = "",
        original_text: str = "",
        tool_trace: str = "",
    ) -> AgentResult:
        prompt = (
            "You are finalizing a WeChat reply after tool use. "
            "Call exactly one registered tool: send_message or noop. "
            "Do not answer in ordinary assistant content. "
            "If the user directly asked or mentioned you, prefer send_message; if the available data is insufficient, say that plainly."
        )
        user = (
            f"Original chat: {chat_title}\n"
            f"Original sender: {original_sender}\n"
            f"Original message: {original_text}\n\n"
            f"Tool results and attempts:\n{tool_trace or '[none]'}\n\n"
            "Finalize now with exactly one tool call."
        )
        result = self._call_llm(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
            default_title=chat_title,
            tools=FINAL_ACTION_TOOL_SPECS,
            tool_choice="required",
        )
        final_actions = [a for a in result.actions if a.get("type") in {"send_message", "noop"}]
        if final_actions:
            return AgentResult(actions=final_actions[:1], raw_response=result.raw_response)

        content = self._raw_message_content(result.raw_response).strip()
        if content:
            return AgentResult(
                actions=[{"type": "send_message", "title": chat_title, "message": content}],
                raw_response=result.raw_response,
            )
        return AgentResult(
            actions=[{"type": "send_message", "title": chat_title, "message": "我这边没拿到原链接或视频内容，刚刚查漏了。"}],
            raw_response=result.raw_response,
        )

    @staticmethod
    def _raw_message_content(raw_response: str) -> str:
        try:
            parsed = json.loads(raw_response)
        except Exception:
            return ""
        msg = parsed.get("message", {}) if isinstance(parsed, dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False) if content else ""

    def _execute_internal_action(self, action: dict[str, Any]) -> None:
        kind = str(action.get("type") or "").strip()
        if kind == "write_memory":
            self._write_memory_merged(str(action.get("name", "core")), str(action.get("content", "")))
        elif kind == "write_skill":
            self.skills.write(str(action.get("name", "")), str(action.get("content", "")))
        elif kind == "delete_skill":
            self.skills.delete(str(action.get("name", "")))
        elif kind == "write_impression":
            name = str(action.get("name", "") or "")
            name = re.sub(r"_[0-9a-f]{8}$", "", name)
            new_content = str(action.get("content", "") or "")
            existing = self.people.read(name)
            if existing.strip() and new_content:
                merged = self._merge_impression(name, existing, new_content)
                if merged.strip():
                    self.people.write(name, merged)
                    return
            if new_content:
                self.people.write(name, new_content)

    def _write_memory_merged(self, name: str, new_content: str) -> None:
        clean_name = MemoryStore.normalize_name(name)
        incoming = (new_content or "").strip()
        if not incoming:
            return
        existing = self.memory.read(clean_name)
        if existing.strip():
            merged = self._merge_memory(clean_name, existing, incoming)
            content = merged.strip() if merged.strip() else self._append_memory_update(existing, incoming)
        else:
            content = incoming
        self.memory.backup(clean_name)
        self.memory.write(clean_name, content.rstrip() + "\n")

    def _merge_memory(self, name: str, existing: str, new_content: str) -> str:
        if name == "timeline":
            guidance = (
                "This is data/memory/timeline.md. Preserve all dated events and important recent activity. "
                "Integrate new events into the right date sections, keeping dates concrete when available."
            )
        else:
            guidance = (
                "This is data/memory/core.md. Preserve stable preferences, rules, durable facts, and self-improvement notes. "
                "Do not move dated one-off events here unless they became a durable rule or stable fact."
            )
        prompt = (
            f"Merge a memory update into {name}.md.\n"
            f"{guidance}\n"
            "Rules:\n"
            "- Return the COMPLETE replacement markdown document.\n"
            "- Preserve every factual detail from the existing document unless the new update clearly supersedes it.\n"
            "- Add or update only what the new content justifies.\n"
            "- Remove exact duplicates and keep the document concise.\n\n"
            f"=== Existing {name}.md ===\n{existing}\n\n"
            f"=== Incoming memory update ===\n{new_content}\n\n"
            "Complete merged markdown:"
        )
        try:
            choice = self._main_llm.chat([{"role": "user", "content": prompt}])
            return choice.get("message", {}).get("content", "") or ""
        except Exception:
            return ""

    @staticmethod
    def _append_memory_update(existing: str, new_content: str) -> str:
        return (
            existing.rstrip()
            + "\n\n## 待整理更新\n\n"
            + new_content.strip()
            + "\n"
        )

    def _merge_impression(self, name: str, existing: str, new_content: str) -> str:
        prompt = (
            f"Consolidate the following old and new impressions about \"{name}\" "
            "into one markdown document. Keep ALL factual details from both. "
            "Remove duplicates. Use this structure:\n"
            "## 基本特征\n\n## 事件纪要\n\n## 人物关系\n\n"
            f"=== Existing impression ===\n{existing}\n\n"
            f"=== New information ===\n{new_content}\n\n"
            "Consolidated impression:"
        )
        try:
            choice = self._main_llm.chat([{"role": "user", "content": prompt}])
            return choice.get("message", {}).get("content", "") or ""
        except Exception:
            return ""

    # ── Parsers ───────────────────────────────────────────────────

    @staticmethod
    def _fallback_parse_longcat(text: str, default_title: str) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for match in re.finditer(r"<longcat_tool_call>(\w+)(.*?)</longcat_tool_call>", text, re.S):
            kind = match.group(1).strip()
            body = match.group(2)
            args: dict[str, str] = {}
            for am in re.finditer(r"<longcat_arg_key>(.*?)</longcat_arg_key>\s*<longcat_arg_value>(.*?)</longcat_arg_value>", body, re.S):
                args[am.group(1).strip()] = am.group(2).strip()
            title = args.get("title", default_title)
            if kind == "send_message" and args.get("message"):
                actions.append({"type": "send_message", "title": title, "message": args["message"]})
            elif kind == "send_image" and args.get("image_path"):
                actions.append({"type": "send_image", "title": title, "image_path": args["image_path"]})
            elif kind == "focus_chat":
                actions.append({"type": "focus_chat", "title": title})
            elif kind == "read_impression" and args.get("name"):
                actions.append({"type": "read_impression", "name": args["name"]})
            elif kind == "noop":
                actions.append({"type": "noop"})
        return actions

    def _parse_tool_calls(self, message: dict[str, Any], *, default_title: str) -> list[dict[str, Any]]:
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raw_calls = []
        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            raw_calls.append({"function": function_call})

        actions: list[dict[str, Any]] = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            arguments = fn.get("arguments")
            if isinstance(arguments, dict):
                args = dict(arguments)
            else:
                args = self._parse_jsonish(str(arguments or ""))
                if not isinstance(args, dict):
                    args = {}
            item = {"type": name, **args}
            action = self._normalize_action(item, default_title=default_title)
            if action is not None:
                actions.append(action)
        return actions

    def _parse_actions(self, text: str, *, default_title: str) -> list[dict[str, Any]]:
        data = self._parse_jsonish(text)
        if isinstance(data, dict):
            raw_actions = data.get("actions", [])
        elif isinstance(data, list):
            raw_actions = data
        else:
            raw_actions = []
        actions: list[dict[str, Any]] = []
        if isinstance(raw_actions, list):
            for item in raw_actions:
                if not isinstance(item, dict):
                    continue
                action = self._normalize_action(item, default_title=default_title)
                if action is not None:
                    actions.append(action)
        return actions

    @staticmethod
    def _parse_jsonish(text: str) -> Any:
        clean = text.strip()
        if not clean:
            return None
        parsed = Agent._try_json_repair(clean)
        if parsed is not None:
            return parsed
        fenced = re.search(r"```(?:json)?\s*(.*?)```", clean, re.S | re.I)
        if fenced:
            parsed = Agent._try_json_repair(fenced.group(1).strip())
            if parsed is not None:
                return parsed
        start = clean.find("{")
        end = clean.rfind("}")
        if 0 <= start < end:
            parsed = Agent._try_json_repair(clean[start: end + 1])
            if parsed is not None:
                return parsed
            truncated = clean[start: end + 1].rstrip("}")
            for _ in range(5):
                parsed = Agent._try_json_repair(truncated)
                if parsed is not None:
                    return parsed
                truncated = truncated.rstrip("}")
        return None

    @staticmethod
    def _try_json_repair(text: str) -> Any:
        candidates = [text]
        open_curly = text.count("{") - text.count("}")
        open_square = text.count("[") - text.count("]")
        if open_curly > 0:
            candidates.append(text + ("}" * open_curly))
        if open_square > 0:
            candidates.append(text + ("]" * open_square))
        if open_curly < 0:
            trimmed = text.rstrip("}")
            if trimmed != text:
                candidates.append(trimmed)
        for c in candidates:
            try:
                return json.loads(c)
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _normalize_action(item: dict[str, Any], *, default_title: str) -> dict[str, Any] | None:
        params = item.get("parameters") or item.get("params") or item.get("args")
        if isinstance(params, dict):
            for k, v in params.items():
                item.setdefault(k, v)
        raw_type = str(item.get("type") or item.get("action") or "").strip().lower()
        aliases = {
            "send": "send_message", "reply": "send_message", "message": "send_message",
            "wechat_send": "send_message", "image": "send_image", "send_photo": "send_image",
            "focus": "focus_chat", "open_chat": "focus_chat", "select_chat": "focus_chat",
            "none": "noop",
            "write_memory": "write_memory", "write_skill": "write_skill", "write_impression": "write_impression",
            "read_impression": "read_impression", "read_person": "read_impression", "read_people": "read_impression",
            "read_chat_history": "read_chat_history", "chat_history": "read_chat_history", "read_history": "read_chat_history",
        }
        kind = aliases.get(raw_type, raw_type)
        if not kind and (item.get("message") or item.get("text")):
            kind = "send_message"
        if kind in {"write_memory", "write_skill", "write_impression"}:
            return {"type": kind, "name": str(item.get("name", "")), "content": str(item.get("content", ""))}
        if kind == "read_impression":
            name = str(item.get("name", "") or item.get("person", "") or item.get("canonical_name", "")).strip()
            return {"type": "read_impression", "name": name} if name else None
        if kind == "read_chat_history":
            title = str(item.get("chat_title") or item.get("title") or default_title).strip()
            result: dict[str, Any] = {"type": "read_chat_history"}
            if title:
                result["chat_title"] = title
            try:
                limit = int(item.get("limit", 50))
            except (TypeError, ValueError):
                limit = 50
            result["limit"] = max(1, min(100, limit))
            return result
        if kind == "delete_skill":
            name = str(item.get("name", "")).strip()
            return {"type": "delete_skill", "name": name} if name else None
        if kind == "fetch_url":
            result: dict[str, Any] = {"type": "fetch_url", "url": str(item.get("url", ""))}
            if "proxy" in item:
                result["proxy"] = bool(item["proxy"])
            return result
        if kind == "search_web":
            result = {"type": "search_web", "query": str(item.get("query", ""))}
            if "proxy" in item:
                result["proxy"] = bool(item["proxy"])
            return result
        if kind == "search_web_volc":
            return {"type": "search_web_volc", "query": str(item.get("query", ""))}
        if kind == "search_web_brave":
            result = {"type": "search_web_brave", "query": str(item.get("query", ""))}
            if "proxy" in item:
                result["proxy"] = bool(item["proxy"])
            return result
        if kind == "browse_url":
            result = {"type": "browse_url", "url": str(item.get("url", ""))}
            if "proxy" in item:
                result["proxy"] = bool(item["proxy"])
            return result
        if kind == "read_file":
            return {"type": "read_file", "path": str(item.get("path", ""))}
        if kind == "list_files":
            return {"type": "list_files", "pattern": str(item.get("pattern", ""))}
        if kind not in {"send_message", "send_image", "focus_chat", "noop"}:
            return None
        if kind == "noop":
            return {"type": "noop"}
        title = str(item.get("title") or item.get("chat_title") or default_title).strip()
        if not title:
            return None
        if kind == "send_message":
            message = str(item.get("message") or item.get("text") or item.get("content") or "").strip()
            if not message:
                return None
            return {"type": "send_message", "title": title, "message": message}
        if kind == "send_image":
            image_path = str(item.get("image_path") or item.get("path") or item.get("media_path") or item.get("file_path") or "").strip()
            if not image_path:
                return None
            return {"type": "send_image", "title": title, "image_path": image_path}
        return {"type": "focus_chat", "title": title}
