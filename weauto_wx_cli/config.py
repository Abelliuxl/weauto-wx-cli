from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import tomllib

from .models import PointRatio, RegionRatio


@dataclass
class OcrConfig:
    min_score: float = 0.25
    enhance: bool = False


@dataclass
class UnreadBadgeConfig:
    min_blob_pixels: int = 70


@dataclass
class UnreadBadgeCircleConfig:
    enabled: bool = False
    x: float = 0.86
    y: float = 0.66
    r: float = 0.18


@dataclass
class ManualRowsConfig:
    enabled: bool = False
    path: str = "data/manual_row_boxes.json"


@dataclass
class PeopleConfig:
    enabled: bool = True
    aliases_path: str = "data/PEOPLE_ALIASES.md"
    memory_dir: str = "data/people"
    max_items: int = 8


@dataclass
class ProxyConfig:
    enabled: bool = False
    url: str = ""
    no_proxy: str = "127.0.0.1,localhost,::1"


@dataclass
class ImageGenerationConfig:
    enabled: bool = False
    provider: str = "dashscope_z_image"
    base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
    base_url_env: str = "DASHSCOPE_BASE_URL"
    api_key: str = ""
    api_key_env: str = "DASHSCOPE_API_KEY"
    model: str = "z-image-turbo"
    timeout_sec: float = 90.0
    download_timeout_sec: float = 45.0
    default_size: str = "1024x1024"
    output_dir: str = "data/generated_images"


@dataclass
class ImageEditingConfig:
    enabled: bool = False
    provider: str = "dashscope_qwen_image_edit"
    base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
    base_url_env: str = "DASHSCOPE_BASE_URL"
    api_key: str = ""
    api_key_env: str = "DASHSCOPE_API_KEY"
    model: str = "qwen-image-2.0-pro"
    timeout_sec: float = 120.0
    download_timeout_sec: float = 45.0
    default_size: str = ""
    output_dir: str = "data/edited_images"
    watermark: bool = False
    prompt_extend: bool = True
    max_input_bytes: int = 10 * 1024 * 1024


@dataclass
class MainLLMConfig:
    api_key: str = ""
    api_key_env: str = "LLM_API_KEY"
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    timeout_sec: float = 60.0
    max_tokens: int = 4096
    temperature: float | None = 0.0


@dataclass
class VisionLLMConfig:
    api_key: str = ""
    api_key_env: str = "VISION_LLM_API_KEY"
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    timeout_sec: float = 30.0
    max_tokens: int = 1024
    temperature: float | None = None


@dataclass
class ReplyLLMConfig:
    api_key: str = ""
    api_key_env: str = "LLM_API_KEY"
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    timeout_sec: float = 60.0
    max_tokens: int = 2048
    temperature: float | None = 0.7


@dataclass
class AgentConfig:
    enabled: bool = True
    system_prompt: str = (
        "You are handling an inbound WeChat event. Use the registered tools to return actions. "
        "Prefer tool calls over writing JSON in message text. "
        "If tool calls are unavailable, return JSON with shape {\"actions\":[...]}. "
        "Supported action types: send_message (title+message), "
        "send_image (title+image_path), generate_image (title+prompt+optional size, when available), "
        "edit_image (title+prompt+optional image_path/image_url/size, when available; omit image_path for current image), "
        "focus_chat (title), noop, "
        "write_memory (name+content) — replace one memory file; name must be core or timeline. "
        "Use core for stable preferences/rules/facts; use timeline for dated events and recent activity. "
        "write_skill (name+content) — save a reusable procedure/strategy (use clear name). "
        "delete_skill (name) — delete an obsolete skill. "
        "write_impression (name+content) — full replace, canonical name only. "
        "read_impression (name) — read one person's impression when needed before replying or updating it. "
        "People impressions are not loaded by default; request only relevant people. "
        "The event sender_identity canonical_name is authoritative for identity; prefer it over raw wx IDs or older chat-history guesses. "
        "Your agent memory is data/memory/core.md and data/memory/timeline.md; "
        "data/people/*.md stores person impressions, not your own agent memory. "
        "If a web action fails or returns no data, try a different search method. "
        "Web action types (executed automatically, results fed back to you): "
        "fetch_url (url, proxy=true/false) — simple HTTP GET, returns raw HTML/text. "
        "search_web (query, proxy=true/false) — Tavily web search. "
        "search_web_brave (query, proxy=true/false) — Brave web search. "
        "search_web_volc (query) — Doubao LLM-powered web search (Volcengine Ark). "
        "browse_url (url, proxy=true/false) — Playwright browser, returns rendered page text. "
        "read_chat_history (chat_title, limit) — read recent WeChat chat history; use this instead of guessing local message file paths. "
        "run_python (code) — sandboxed Python for math/statistics/date calculations only; print the result. "
        "build_wow_character_url (character/server or player/class_name) — use data/skills/wow-character-link to build WoW CN character links; do not use run_python for URL encoding. "
        "read_file (path) — read a file inside the project directory. "
        "list_files (pattern) — list files matching a glob pattern (e.g. data/**/*.md). "
        "Set proxy=false for domestic sites, proxy=true for international sites that need the proxy. "
        "Use the inbound chat_title as title unless routing elsewhere. "
        "WeChat does not render Markdown; send plain text only. "
        "Do not use Markdown headings, bold/italic markers, blockquotes, tables, code fences, or bullet stars. "
        "Numbered lists such as 1. 2. 3. are okay when a list is genuinely useful."
    )
    identity_text: str = ""
    identity_path: str = "data/identity.md"
    personality_path: str = "data/personality.md"
    heartbeat_enabled: bool = False
    heartbeat_interval_sec: float = 300.0
    main: MainLLMConfig = field(default_factory=MainLLMConfig)
    vision: VisionLLMConfig = field(default_factory=VisionLLMConfig)
    reply: ReplyLLMConfig = field(default_factory=ReplyLLMConfig)


@dataclass
class AppConfig:
    wx_binary: str = "wx"
    poll_interval_sec: float = 2.0
    state_path: str = "data/state.json"
    dry_run: bool = True
    max_replies_per_tick: int = 2
    skip_existing_on_start: bool = True
    send_lock_path: str = "data/send.lock"
    send_action_interval_sec: float = 0.6

    app_name: str = "WeChat"
    activate_wait_sec: float = 0.6
    click_move_duration_sec: float = 0.18
    mouse_down_hold_sec: float = 0.03
    post_select_wait_sec: float = 0.35
    post_input_click_wait_sec: float = 0.08
    post_paste_wait_sec: float = 0.06
    focus_verify_enabled: bool = True
    focus_verify_max_clicks: int = 3

    processing_mode: str = "agent"
    reply_mode: str = "template"
    reply_template: str = "收到：{text}"
    reply_command: str = ""

    self_names: list[str] = field(default_factory=list)
    allow_chats: list[str] = field(default_factory=list)
    deny_chats: list[str] = field(default_factory=list)
    group_title_prefixes: list[str] = field(default_factory=lambda: ["群"])
    group_reply_keywords: list[str] = field(
        default_factory=lambda: ["@助手", "@机器人", "机器人", "bot", "小助手"]
    )
    group_reply_cooldown_sec: float = 0.0

    use_manual_row_boxes: bool = False
    manual_row_boxes_path: str = "data/manual_row_boxes.json"
    row_title_region_enabled: bool = False
    row_title_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.24, y=0.52, w=0.58, h=0.42)
    )
    preview_region_enabled: bool = False
    preview_text_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.24, y=0.10, w=0.72, h=0.52)
    )
    rows_max: int = 8
    row_height_ratio: float = 0.145

    list_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.065, y=0.12, w=0.325, h=0.82)
    )
    chat_title_region: RegionRatio = field(
        default_factory=lambda: RegionRatio(x=0.40, y=0.01, w=0.57, h=0.10)
    )
    input_point: PointRatio = field(default_factory=lambda: PointRatio(x=0.73, y=0.92))
    enable_image_resolver: bool = True
    image_output_dir: str = "data/images"
    ocr: OcrConfig = field(default_factory=OcrConfig)
    unread_badge: UnreadBadgeConfig = field(default_factory=UnreadBadgeConfig)
    unread_badge_circle: UnreadBadgeCircleConfig = field(default_factory=UnreadBadgeCircleConfig)
    manual_rows: ManualRowsConfig = field(default_factory=ManualRowsConfig)
    people: PeopleConfig = field(default_factory=PeopleConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)
    image_editing: ImageEditingConfig = field(default_factory=ImageEditingConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    tavily_api_key: str = ""
    brave_search_api_key: str = ""

    volc_ark_enabled: bool = False
    volc_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volc_ark_api_key: str = ""
    volc_ark_api_key_env: str = "ARK_API_KEY"
    volc_ark_model: str = "doubao-seed-1-8-251228"
    volc_ark_limit: int = 8
    volc_ark_max_keyword: int = 3
    volc_ark_timeout_sec: float = 20.0


def _region(raw: object, default: RegionRatio) -> RegionRatio:
    if not isinstance(raw, dict):
        return default
    return RegionRatio(
        x=float(raw.get("x", default.x)),
        y=float(raw.get("y", default.y)),
        w=float(raw.get("w", default.w)),
        h=float(raw.get("h", default.h)),
    )


def _point(raw: object, default: PointRatio) -> PointRatio:
    if not isinstance(raw, dict):
        return default
    return PointRatio(
        x=float(raw.get("x", default.x)),
        y=float(raw.get("y", default.y)),
    )


def _str_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _load_image_generation_provider(raw: object, default: str = "dashscope_z_image") -> str:
    value = str(raw if raw is not None else default).strip().lower()
    if value in ("openai", "openai_compat", "openai-compatible"):
        return "openai_compat"
    if value in ("dashscope", "dashscope_z_image", "dashscope-z-image", "aliyun", "bailian"):
        return "dashscope_z_image"
    if value != default:
        return _load_image_generation_provider(default, "dashscope_z_image")
    return "dashscope_z_image"


def load_config(path: str | Path) -> AppConfig:
    cfg = AppConfig()
    p = Path(path)
    if not p.exists():
        return cfg
    data = tomllib.loads(p.read_text(encoding="utf-8"))

    cfg.wx_binary = str(data.get("wx_binary", cfg.wx_binary))
    cfg.poll_interval_sec = float(data.get("poll_interval_sec", cfg.poll_interval_sec))
    cfg.state_path = str(data.get("state_path", cfg.state_path))
    cfg.dry_run = bool(data.get("dry_run", cfg.dry_run))
    cfg.max_replies_per_tick = int(data.get("max_replies_per_tick", cfg.max_replies_per_tick))
    cfg.skip_existing_on_start = bool(data.get("skip_existing_on_start", cfg.skip_existing_on_start))
    cfg.send_lock_path = str(data.get("send_lock_path", cfg.send_lock_path))
    cfg.send_action_interval_sec = float(
        data.get("send_action_interval_sec", cfg.send_action_interval_sec)
    )

    cfg.app_name = str(data.get("app_name", cfg.app_name))
    cfg.activate_wait_sec = float(data.get("activate_wait_sec", cfg.activate_wait_sec))
    cfg.click_move_duration_sec = float(
        data.get("click_move_duration_sec", cfg.click_move_duration_sec)
    )
    cfg.mouse_down_hold_sec = float(data.get("mouse_down_hold_sec", cfg.mouse_down_hold_sec))
    cfg.post_select_wait_sec = float(data.get("post_select_wait_sec", cfg.post_select_wait_sec))
    cfg.post_input_click_wait_sec = float(
        data.get("post_input_click_wait_sec", cfg.post_input_click_wait_sec)
    )
    cfg.post_paste_wait_sec = float(data.get("post_paste_wait_sec", cfg.post_paste_wait_sec))
    cfg.focus_verify_enabled = bool(data.get("focus_verify_enabled", cfg.focus_verify_enabled))
    cfg.focus_verify_max_clicks = int(
        data.get("focus_verify_max_clicks", cfg.focus_verify_max_clicks)
    )

    cfg.processing_mode = (
        str(data.get("processing_mode", cfg.processing_mode)).strip().lower() or "agent"
    )
    cfg.reply_mode = str(data.get("reply_mode", cfg.reply_mode)).strip().lower() or "template"
    cfg.reply_template = str(data.get("reply_template", cfg.reply_template))
    cfg.reply_command = str(data.get("reply_command", cfg.reply_command))

    cfg.self_names = _str_list(data.get("self_names", cfg.self_names))
    cfg.allow_chats = _str_list(data.get("allow_chats", cfg.allow_chats))
    cfg.deny_chats = _str_list(data.get("deny_chats", cfg.deny_chats))
    cfg.group_title_prefixes = _str_list(
        data.get("group_title_prefixes", cfg.group_title_prefixes)
    ) or cfg.group_title_prefixes
    cfg.group_reply_keywords = _str_list(
        data.get("group_reply_keywords", cfg.group_reply_keywords)
    )
    cfg.group_reply_cooldown_sec = float(
        data.get("group_reply_cooldown_sec", cfg.group_reply_cooldown_sec)
    )
    cfg.enable_image_resolver = bool(data.get("enable_image_resolver", cfg.enable_image_resolver))
    cfg.image_output_dir = str(data.get("image_output_dir", cfg.image_output_dir))

    cfg.use_manual_row_boxes = bool(
        data.get("use_manual_row_boxes", data.get("manual_row_boxes_enabled", cfg.use_manual_row_boxes))
    )
    cfg.manual_row_boxes_path = str(
        data.get("manual_row_boxes_path", cfg.manual_row_boxes_path)
    )
    cfg.row_title_region_enabled = bool(
        data.get("row_title_region_enabled", cfg.row_title_region_enabled)
    )
    cfg.preview_region_enabled = bool(
        data.get("preview_region_enabled", cfg.preview_region_enabled)
    )
    cfg.rows_max = int(data.get("rows_max", cfg.rows_max))
    cfg.row_height_ratio = float(data.get("row_height_ratio", cfg.row_height_ratio))

    cfg.list_region = _region(data.get("list_region"), cfg.list_region)
    cfg.row_title_region = _region(data.get("row_title_region"), cfg.row_title_region)
    cfg.preview_text_region = _region(data.get("preview_text_region"), cfg.preview_text_region)
    cfg.chat_title_region = _region(data.get("chat_title_region"), cfg.chat_title_region)
    cfg.input_point = _point(data.get("input_point"), cfg.input_point)

    ocr_raw = data.get("ocr", {})
    if isinstance(ocr_raw, dict):
        cfg.ocr = OcrConfig(
            min_score=float(ocr_raw.get("min_score", cfg.ocr.min_score)),
            enhance=bool(ocr_raw.get("enhance", cfg.ocr.enhance)),
        )

    manual_raw = data.get("manual_rows", {})
    if isinstance(manual_raw, dict):
        cfg.manual_rows = ManualRowsConfig(
            enabled=bool(
                manual_raw.get("enabled", cfg.manual_rows.enabled or cfg.use_manual_row_boxes)
            ),
            path=str(manual_raw.get("path", cfg.manual_row_boxes_path or cfg.manual_rows.path)),
        )
    if cfg.manual_rows.enabled:
        cfg.use_manual_row_boxes = True
    if cfg.manual_rows.path and cfg.manual_rows.path != ManualRowsConfig().path:
        cfg.manual_row_boxes_path = cfg.manual_rows.path

    people_raw = data.get("people", {})
    cfg.people = PeopleConfig(
        enabled=bool(data.get("people_aliases_enabled", cfg.people.enabled)),
        aliases_path=str(data.get("people_aliases_path", cfg.people.aliases_path)),
        memory_dir=str(data.get("people_memory_dir", cfg.people.memory_dir)),
        max_items=int(data.get("people_context_max_items", cfg.people.max_items)),
    )
    if isinstance(people_raw, dict):
        cfg.people = PeopleConfig(
            enabled=bool(people_raw.get("enabled", cfg.people.enabled)),
            aliases_path=str(people_raw.get("aliases_path", cfg.people.aliases_path)),
            memory_dir=str(people_raw.get("memory_dir", cfg.people.memory_dir)),
            max_items=int(people_raw.get("max_items", cfg.people.max_items)),
        )

    proxy_raw = data.get("proxy", {})
    cfg.proxy = ProxyConfig(
        enabled=bool(data.get("proxy_enabled", cfg.proxy.enabled)),
        url=str(data.get("proxy_url", cfg.proxy.url)),
        no_proxy=str(data.get("no_proxy", cfg.proxy.no_proxy)),
    )
    if isinstance(proxy_raw, dict):
        cfg.proxy = ProxyConfig(
            enabled=bool(proxy_raw.get("enabled", cfg.proxy.enabled)),
            url=str(proxy_raw.get("url", cfg.proxy.url)),
            no_proxy=str(proxy_raw.get("no_proxy", cfg.proxy.no_proxy)),
        )

    image_gen_raw = data.get("image_generation", {})
    if isinstance(image_gen_raw, dict):
        image_gen_base_url = str(
            image_gen_raw.get("base_url", cfg.image_generation.base_url)
        ).strip().rstrip("/")
        image_gen_base_url_env = str(
            image_gen_raw.get("base_url_env", cfg.image_generation.base_url_env)
        ).strip()
        if (not image_gen_base_url) and image_gen_base_url_env:
            image_gen_base_url = os.getenv(image_gen_base_url_env, "").strip().rstrip("/")
        cfg.image_generation = ImageGenerationConfig(
            enabled=bool(image_gen_raw.get("enabled", cfg.image_generation.enabled)),
            provider=_load_image_generation_provider(
                image_gen_raw.get("provider", cfg.image_generation.provider),
                cfg.image_generation.provider,
            ),
            base_url=image_gen_base_url,
            base_url_env=image_gen_base_url_env,
            api_key=str(image_gen_raw.get("api_key", cfg.image_generation.api_key)),
            api_key_env=str(image_gen_raw.get("api_key_env", cfg.image_generation.api_key_env)),
            model=str(image_gen_raw.get("model", cfg.image_generation.model)).strip(),
            timeout_sec=float(image_gen_raw.get("timeout_sec", cfg.image_generation.timeout_sec)),
            download_timeout_sec=float(
                image_gen_raw.get(
                    "download_timeout_sec",
                    cfg.image_generation.download_timeout_sec,
                )
            ),
            default_size=str(
                image_gen_raw.get("default_size", cfg.image_generation.default_size)
            ).strip(),
            output_dir=str(
                image_gen_raw.get("output_dir", cfg.image_generation.output_dir)
            ).strip(),
        )
        if not cfg.image_generation.base_url:
            cfg.image_generation.base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
        if not cfg.image_generation.model:
            cfg.image_generation.model = "z-image-turbo"
        if not cfg.image_generation.default_size:
            cfg.image_generation.default_size = "1024x1024"
        if not cfg.image_generation.output_dir:
            cfg.image_generation.output_dir = "data/generated_images"
        if cfg.image_generation.timeout_sec < 5.0:
            cfg.image_generation.timeout_sec = 5.0
        if cfg.image_generation.download_timeout_sec < 5.0:
            cfg.image_generation.download_timeout_sec = 5.0

    image_edit_present = isinstance(data.get("image_editing"), dict)
    image_edit_raw = data.get("image_editing", {}) if image_edit_present else {}
    if isinstance(image_edit_raw, dict):
        image_edit_base_url = str(
            image_edit_raw.get("base_url", cfg.image_generation.base_url or cfg.image_editing.base_url)
        ).strip().rstrip("/")
        image_edit_base_url_env = str(
            image_edit_raw.get(
                "base_url_env",
                cfg.image_generation.base_url_env or cfg.image_editing.base_url_env,
            )
        ).strip()
        if (not image_edit_base_url) and image_edit_base_url_env:
            image_edit_base_url = os.getenv(image_edit_base_url_env, "").strip().rstrip("/")
        cfg.image_editing = ImageEditingConfig(
            enabled=bool(
                image_edit_raw.get(
                    "enabled",
                    cfg.image_editing.enabled if image_edit_present else cfg.image_generation.enabled,
                )
            ),
            provider=str(
                image_edit_raw.get("provider", cfg.image_editing.provider)
            ).strip().lower()
            or "dashscope_qwen_image_edit",
            base_url=image_edit_base_url,
            base_url_env=image_edit_base_url_env,
            api_key=str(
                image_edit_raw.get("api_key", cfg.image_generation.api_key or cfg.image_editing.api_key)
            ),
            api_key_env=str(
                image_edit_raw.get(
                    "api_key_env",
                    cfg.image_generation.api_key_env or cfg.image_editing.api_key_env,
                )
            ),
            model=str(image_edit_raw.get("model", cfg.image_editing.model)).strip(),
            timeout_sec=float(image_edit_raw.get("timeout_sec", cfg.image_editing.timeout_sec)),
            download_timeout_sec=float(
                image_edit_raw.get(
                    "download_timeout_sec",
                    cfg.image_editing.download_timeout_sec,
                )
            ),
            default_size=str(
                image_edit_raw.get("default_size", cfg.image_editing.default_size)
            ).strip(),
            output_dir=str(
                image_edit_raw.get("output_dir", cfg.image_editing.output_dir)
            ).strip(),
            watermark=bool(image_edit_raw.get("watermark", cfg.image_editing.watermark)),
            prompt_extend=bool(
                image_edit_raw.get("prompt_extend", cfg.image_editing.prompt_extend)
            ),
            max_input_bytes=int(
                image_edit_raw.get("max_input_bytes", cfg.image_editing.max_input_bytes)
            ),
        )
        if not cfg.image_editing.base_url:
            cfg.image_editing.base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
        if not cfg.image_editing.model:
            cfg.image_editing.model = "qwen-image-2.0-pro"
        if not cfg.image_editing.output_dir:
            cfg.image_editing.output_dir = "data/edited_images"
        if cfg.image_editing.timeout_sec < 5.0:
            cfg.image_editing.timeout_sec = 5.0
        if cfg.image_editing.download_timeout_sec < 5.0:
            cfg.image_editing.download_timeout_sec = 5.0
        if cfg.image_editing.max_input_bytes <= 0:
            cfg.image_editing.max_input_bytes = 10 * 1024 * 1024

    unread_raw = data.get("unread_badge", {})
    if isinstance(unread_raw, dict):
        cfg.unread_badge = UnreadBadgeConfig(
            min_blob_pixels=int(
                unread_raw.get("min_blob_pixels", cfg.unread_badge.min_blob_pixels)
            )
        )

    unread_circle_raw = data.get("unread_badge_circle", {})
    if isinstance(unread_circle_raw, dict):
        cfg.unread_badge_circle = UnreadBadgeCircleConfig(
            enabled=bool(unread_circle_raw.get("enabled", cfg.unread_badge_circle.enabled)),
            x=float(unread_circle_raw.get("x", cfg.unread_badge_circle.x)),
            y=float(unread_circle_raw.get("y", cfg.unread_badge_circle.y)),
            r=float(unread_circle_raw.get("r", cfg.unread_badge_circle.r)),
        )

    agent_raw = data.get("agent", {})
    if isinstance(agent_raw, dict):
        main_raw = agent_raw.get("main", {})
        vision_raw = agent_raw.get("vision", {})
        reply_raw = agent_raw.get("reply", {})

        def _ak(raw_sec: object, default_key: str) -> str:
            if isinstance(raw_sec, dict):
                return str(raw_sec.get("api_key", default_key) or "")
            return default_key

        def _ake(raw_sec: object, default_env: str) -> str:
            if isinstance(raw_sec, dict):
                return str(raw_sec.get("api_key_env", default_env) or "")
            return default_env

        def _temp(raw_sec: object, default: float | None) -> float | None:
            if not isinstance(raw_sec, dict) or "temperature" not in raw_sec:
                return default
            value = raw_sec.get("temperature")
            if value is None:
                return None
            return float(value)

        cfg.agent = AgentConfig(
            enabled=bool(agent_raw.get("enabled", cfg.agent.enabled)),
            system_prompt=str(agent_raw.get("system_prompt", cfg.agent.system_prompt)),
            identity_text=str(agent_raw.get("identity_text", cfg.agent.identity_text)),
            identity_path=str(agent_raw.get("identity_path", cfg.agent.identity_path)),
            personality_path=str(agent_raw.get("personality_path", cfg.agent.personality_path)),
            heartbeat_enabled=bool(agent_raw.get("heartbeat_enabled", cfg.agent.heartbeat_enabled)),
            heartbeat_interval_sec=float(agent_raw.get("heartbeat_interval_sec", cfg.agent.heartbeat_interval_sec)),
            main=MainLLMConfig(
                api_key=_ak(main_raw, cfg.agent.main.api_key),
                api_key_env=_ake(main_raw, cfg.agent.main.api_key_env),
                model=str(main_raw.get("model", cfg.agent.main.model)) if isinstance(main_raw, dict) else cfg.agent.main.model,
                base_url=str(main_raw.get("base_url", cfg.agent.main.base_url)) if isinstance(main_raw, dict) else cfg.agent.main.base_url,
                timeout_sec=float(main_raw.get("timeout_sec", cfg.agent.main.timeout_sec)) if isinstance(main_raw, dict) else cfg.agent.main.timeout_sec,
                max_tokens=int(main_raw.get("max_tokens", cfg.agent.main.max_tokens)) if isinstance(main_raw, dict) else cfg.agent.main.max_tokens,
                temperature=_temp(main_raw, cfg.agent.main.temperature),
            ),
            vision=VisionLLMConfig(
                api_key=_ak(vision_raw, cfg.agent.vision.api_key),
                api_key_env=_ake(vision_raw, cfg.agent.vision.api_key_env),
                model=str(vision_raw.get("model", cfg.agent.vision.model)) if isinstance(vision_raw, dict) else cfg.agent.vision.model,
                base_url=str(vision_raw.get("base_url", cfg.agent.vision.base_url)) if isinstance(vision_raw, dict) else cfg.agent.vision.base_url,
                timeout_sec=float(vision_raw.get("timeout_sec", cfg.agent.vision.timeout_sec)) if isinstance(vision_raw, dict) else cfg.agent.vision.timeout_sec,
                max_tokens=int(vision_raw.get("max_tokens", cfg.agent.vision.max_tokens)) if isinstance(vision_raw, dict) else cfg.agent.vision.max_tokens,
                temperature=_temp(vision_raw, cfg.agent.vision.temperature),
            ),
            reply=ReplyLLMConfig(
                api_key=_ak(reply_raw, cfg.agent.reply.api_key),
                api_key_env=_ake(reply_raw, cfg.agent.reply.api_key_env),
                model=str(reply_raw.get("model", cfg.agent.reply.model)) if isinstance(reply_raw, dict) else cfg.agent.reply.model,
                base_url=str(reply_raw.get("base_url", cfg.agent.reply.base_url)) if isinstance(reply_raw, dict) else cfg.agent.reply.base_url,
                timeout_sec=float(reply_raw.get("timeout_sec", cfg.agent.reply.timeout_sec)) if isinstance(reply_raw, dict) else cfg.agent.reply.timeout_sec,
                max_tokens=int(reply_raw.get("max_tokens", cfg.agent.reply.max_tokens)) if isinstance(reply_raw, dict) else cfg.agent.reply.max_tokens,
                temperature=_temp(reply_raw, cfg.agent.reply.temperature),
            ),
        )

    def _find_key(d: dict, key: str) -> str:
        if key in d:
            return str(d[key] or "")
        for v in d.values():
            if isinstance(v, dict) and key in v:
                return str(v[key] or "")
        return ""
    cfg.tavily_api_key = _find_key(data, "TAVILY_API_KEY") or cfg.tavily_api_key
    cfg.brave_search_api_key = _find_key(data, "BRAVE_SEARCH_API_KEY") or cfg.brave_search_api_key

    volc_raw = data.get("volc_ark", {})
    if isinstance(volc_raw, dict):
        cfg.volc_ark_enabled = bool(volc_raw.get("enabled", cfg.volc_ark_enabled))
        cfg.volc_ark_base_url = str(volc_raw.get("base_url", cfg.volc_ark_base_url))
        cfg.volc_ark_api_key = str(volc_raw.get("api_key", cfg.volc_ark_api_key))
        cfg.volc_ark_api_key_env = str(volc_raw.get("api_key_env", cfg.volc_ark_api_key_env))
        cfg.volc_ark_model = str(volc_raw.get("model", cfg.volc_ark_model))
        cfg.volc_ark_limit = int(volc_raw.get("limit", cfg.volc_ark_limit))
        cfg.volc_ark_max_keyword = int(volc_raw.get("max_keyword", cfg.volc_ark_max_keyword))
        cfg.volc_ark_timeout_sec = float(volc_raw.get("timeout_sec", cfg.volc_ark_timeout_sec))

    return cfg
