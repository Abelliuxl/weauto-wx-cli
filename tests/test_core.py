import time
from pathlib import Path
from tempfile import TemporaryDirectory

from weauto_wx_cli.agent import Agent
from weauto_wx_cli.config import load_config
from weauto_wx_cli.bot import AutoSpeakBot
from weauto_wx_cli.detector import title_matches
from weauto_wx_cli.detector import _pick_title_preview
from weauto_wx_cli.people import PeopleContextBuilder, PersonAliasResolver
from weauto_wx_cli.wx_cli import normalize_messages


def test_config_example_loads():
    cfg = load_config("config.toml.example")
    assert cfg.wx_binary == "wx"
    assert cfg.dry_run is True
    assert cfg.processing_mode == "agent"


def test_normalize_messages_accepts_common_wx_keys():
    msgs = normalize_messages(
        {
            "messages": [
                {
                    "chatTitle": "测试群",
                    "sender": "A",
                    "content": "@机器人 hello",
                    "msgId": "m1",
                }
            ]
        }
    )
    assert len(msgs) == 1
    assert msgs[0].chat_title == "测试群"
    assert msgs[0].text == "@机器人 hello"
    assert msgs[0].message_id == "m1"


def test_normalize_messages_accepts_real_wx_cli_keys():
    msgs = normalize_messages(
        [
            {
                "chat": "群-测试",
                "chat_type": "group",
                "content": "hello",
                "local_id": 123,
                "sender": "A",
                "time": "2026-05-14 16:00",
                "timestamp": 1778745600,
                "type": "文本",
                "username": "room@chatroom",
            }
        ]
    )
    assert len(msgs) == 1
    assert msgs[0].chat_title == "群-测试"
    assert msgs[0].chat_type == "group"
    assert msgs[0].message_type == "text"
    assert msgs[0].message_id == "local:room@chatroom:123"


def test_normalize_image_message_with_local_path():
    msgs = normalize_messages(
        {
            "data": [
                {
                    "session": "测试群",
                    "sender": "A",
                    "msgType": "image",
                    "image": {"path": "/tmp/pic.jpg", "name": "pic.jpg"},
                }
            ]
        }
    )
    assert len(msgs) == 1
    assert msgs[0].message_type == "image"
    assert msgs[0].text == "[图片]"
    assert msgs[0].attachments[0].path == "/tmp/pic.jpg"


def test_normalize_real_wx_cli_image_without_path():
    msgs = normalize_messages(
        [
            {
                "chat": "群-游戏",
                "chat_type": "group",
                "content": "[图片] local_id=229",
                "local_id": 229,
                "sender": ".凯",
                "type": "图片",
                "username": "room@chatroom",
            }
        ]
    )
    assert msgs[0].message_type == "image"
    assert msgs[0].attachments[0].type == "image"
    assert msgs[0].attachments[0].raw["local_id"] == "229"


def test_normalize_real_wx_cli_link_includes_url_attachment():
    msgs = normalize_messages(
        [
            {
                "chat": "群-游戏",
                "content": "[链接] 1天被蜇50次？养蜂人都在做什么？",
                "sender": "用户丙",
                "type": "链接/文件",
                "url": "https://b23.tv/sz2OYMs?share_source=weixin",
            }
        ]
    )
    assert msgs[0].message_type == "file"
    assert msgs[0].attachments[0].url == "https://b23.tv/sz2OYMs?share_source=weixin"


def test_agent_action_parser_accepts_actions_json():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_actions(
        '{"actions":[{"type":"send_message","title":"测试群","message":"ok"}]}',
        default_title="测试群",
    )
    assert actions == [{"type": "send_message", "title": "测试群", "message": "ok"}]


def test_agent_history_text_appends_link_url():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    text = agent._format_history_text(
        "[链接] 1天被蜇50次？养蜂人都在做什么？",
        {"url": "https://b23.tv/sz2OYMs?share_source=weixin"},
    )
    assert text == "[链接] 1天被蜇50次？养蜂人都在做什么？ url=https://b23.tv/sz2OYMs?share_source=weixin"


def test_agent_action_parser_accepts_params_alias():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_actions(
        '{"actions":[{"type":"search_web_brave","params":{"query":"蜂蜜 视频","proxy":true}}]}',
        default_title="测试群",
    )
    assert actions == [{"type": "search_web_brave", "query": "蜂蜜 视频", "proxy": True}]


def test_agent_parses_chat_completion_tool_calls():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_tool_calls(
        {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "search_web_brave",
                        "arguments": '{"query":"影视飓风 蜂蜜","proxy":true}',
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "send_message",
                        "arguments": '{"message":"查到了"}',
                    },
                },
            ]
        },
        default_title="群-游戏",
    )
    assert actions == [
        {"type": "search_web_brave", "query": "影视飓风 蜂蜜", "proxy": True},
        {"type": "send_message", "title": "群-游戏", "message": "查到了"},
    ]


def test_agent_call_llm_uses_registered_tools():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    seen = {}

    def fake_chat(messages, **kwargs):
        seen.update(kwargs)
        return {
            "message": {
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "send_message",
                            "arguments": '{"message":"ok"}',
                        },
                    }
                ]
            }
        }

    agent._main_llm.chat = fake_chat
    result = agent._call_llm([{"role": "user", "content": "hi"}], default_title="测试群")
    assert seen["tool_choice"] == "auto"
    assert any(item["function"]["name"] == "send_message" for item in seen["tools"])
    assert result.actions == [{"type": "send_message", "title": "测试群", "message": "ok"}]


def test_agent_action_parser_repairs_missing_final_brace():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_actions(
        '{"actions":[{"type":"send_message","title":"测试群","message":"ok"}]',
        default_title="测试群",
    )
    assert actions == [{"type": "send_message", "title": "测试群", "message": "ok"}]


def test_agent_payload_includes_attachments():
    cfg = load_config("config.toml.example")
    msg = normalize_messages(
        [{"title": "测试群", "type": "image", "image_path": "/tmp/a.png"}]
    )[0]
    payload = Agent(cfg)._build_payload(msg)
    event = payload["event"]
    assert event["message_type"] == "image"
    assert event["attachments"][0]["path"] == "/tmp/a.png"


def test_agent_payload_includes_wx_cli_image_local_id():
    cfg = load_config("config.toml.example")
    msg = normalize_messages(
        [{"chat": "群-游戏", "content": "[图片] local_id=229", "local_id": 229, "type": "图片"}]
    )[0]
    event = Agent(cfg)._build_payload(msg)["event"]
    assert event["message_type"] == "image"
    assert event["attachments"][0]["raw"]["local_id"] == "229"


def test_group_message_without_keyword_replies_with_cooldown():
    cfg = load_config("config.toml.example")
    cfg.processing_mode = "hermes"
    cfg.group_reply_cooldown_sec = 0.0
    bot = AutoSpeakBot(cfg)
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "A", "content": "普通群消息", "type": "文本"}]
    )[0]
    assert bot._should_reply(msg) is True


def test_group_urgent_keyword_bypasses_cooldown():
    cfg = load_config("config.toml.example")
    cfg.group_reply_cooldown_sec = 999.0
    bot = AutoSpeakBot(cfg)
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "A", "content": "机器人 帮个忙", "type": "文本"}]
    )[0]
    assert bot._should_reply(msg) is True


def test_group_urgent_at_bypasses_cooldown():
    cfg = load_config("config.toml.example")
    cfg.group_reply_cooldown_sec = 999.0
    bot = AutoSpeakBot(cfg)
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "B", "content": "@助手 查一下", "type": "文本"}]
    )[0]
    assert bot._should_reply(msg) is True


def test_group_non_urgent_blocked_by_cooldown():
    cfg = load_config("config.toml.example")
    cfg.group_reply_cooldown_sec = 999.0
    bot = AutoSpeakBot(cfg)
    bot._last_group_reply["群-测试"] = time.time()
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "A", "content": "普通群消息", "type": "文本"}]
    )[0]
    assert bot._should_reply(msg) is False


def test_title_matching_tolerates_ocr_noise():
    assert title_matches("测试群", "测试群 (12)")
    assert title_matches("memberalpha", "member alpha")
    assert not title_matches("测试群", "另一个群")


def test_pick_title_preview_ignores_date_and_splits_inline_preview():
    assert _pick_title_preview(["04/2", "文件传输助手"]) == ("文件传输助手", "")
    assert _pick_title_preview(["memberalpha", "确实靠自己打了17铁", "星期二"]) == (
        "memberalpha",
        "确实靠自己打了17铁",
    )


def test_person_alias_resolver_supports_exact_and_wildcard():
    tmp = TemporaryDirectory()
    path = Path(tmp.name) / "PEOPLE_ALIASES.md"
    path.write_text(
        "- 用户甲 -> memberalpha, 亮哥\n"
        "- 统皇 -> *cong\n",
        encoding="utf-8",
    )
    try:
        resolver = PersonAliasResolver(str(path))
        assert resolver.resolve("memberalpha") == "用户甲"
        assert resolver.resolve("餮虢cong") == "统皇"
        assert "亮哥" in resolver.aliases_for("用户甲")
    finally:
        tmp.cleanup()


def test_people_context_enriches_sender_mentions_and_text_aliases():
    tmp = TemporaryDirectory()
    path = Path(tmp.name) / "PEOPLE_ALIASES.md"
    path.write_text(
        "- 用户甲 -> memberalpha, 亮哥\n"
        "- 张捷 -> 巴音布鲁克之王\n",
        encoding="utf-8",
    )
    try:
        builder = PeopleContextBuilder(aliases_path=str(path), max_items=8)
        payload = builder.build(sender="memberalpha", text="@巴音布鲁克之王 亮哥这个怎么打")
        assert payload["sender_identity"]["canonical_name"] == "用户甲"
        mentioned = payload["mentioned_people"]
        assert mentioned[0]["canonical_name"] == "张捷"
        names = {item["canonical_name"] for item in payload["people_context"]}
        assert {"用户甲", "张捷"} <= names
    finally:
        tmp.cleanup()


def test_agent_payload_includes_people_context():
    tmp = TemporaryDirectory()
    path = Path(tmp.name) / "PEOPLE_ALIASES.md"
    path.write_text("- 小蔡 -> Kamille\n- 张捷 -> 巴音布鲁克之王\n", encoding="utf-8")
    try:
        cfg = load_config("config.toml.example")
        cfg.people.enabled = True
        cfg.people.aliases_path = str(path)
        msg = normalize_messages(
            [{"chat": "群-魔兽", "sender": "Kamille", "content": "@巴音布鲁克之王 这个呢"}]
        )[0]
        event = Agent(cfg)._build_payload(msg)["event"]
        assert event["sender_identity"]["canonical_name"] == "小蔡"
        assert event["mentioned_people"][0]["canonical_name"] == "张捷"
    finally:
        tmp.cleanup()


def test_agent_payload_resolves_sender_identity():
    tmp = TemporaryDirectory()
    path = Path(tmp.name) / "PEOPLE_ALIASES.md"
    memory_dir = Path(tmp.name) / "people"
    path.write_text("- 小蔡 -> Kamille\n", encoding="utf-8")
    try:
        cfg = load_config("config.toml.example")
        cfg.people.enabled = True
        cfg.people.aliases_path = str(path)
        cfg.people.memory_dir = str(memory_dir)
        msg = normalize_messages([{"chat": "群-魔兽", "sender": "Kamille", "content": "打本"}])[0]
        event = Agent(cfg)._build_payload(msg)["event"]
        assert event["sender_identity"]["canonical_name"] == "小蔡"
        assert event["sender_identity"]["observed_name"] == "Kamille"
    finally:
        tmp.cleanup()


def test_agent_payload_does_not_expose_person_memory_files():
    cfg = load_config("config.toml.example")
    cfg.people.enabled = True
    cfg.people.memory_dir = "data/people"
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "用户乙", "content": "@助手 记忆在哪里"}]
    )[0]
    event = Agent(cfg)._build_payload(msg)["event"]
    assert "memory_file" not in event["sender_identity"]
    assert all("memory_file" not in item for item in event["mentioned_people"])
    assert all("memory_file" not in item for item in event["people_context"])


def test_image_resolver_detect_no_v2_magic_returns_none():
    from weauto_wx_cli.image_resolver import ImageResolver
    data = b"\x00" * 20
    assert ImageResolver._decrypt_v2(data, b"key", 0) is None
    assert ImageResolver._decrypt_legacy_xor(data) is None
    assert ImageResolver._detect_format(b"\xff\xd8\xff\xe0test") == "jpg"
    assert ImageResolver._detect_format(b"\x89PNGtest") == "png"


def test_image_resolver_extract_md5_from_packed():
    from weauto_wx_cli.image_resolver import ImageResolver
    packed = b'\x08\x03\x10\x02\x1a"" 88dadbe12edff01ff6dac1bf0323d225X\x00'
    md5 = ImageResolver._extract_md5_from_packed(packed)
    assert md5 == "88dadbe12edff01ff6dac1bf0323d225"


def test_image_resolver_clean_attachments():
    from weauto_wx_cli.image_resolver import ImageResolver
    from weauto_wx_cli.models import Attachment
    dirty = [Attachment(type="image"), Attachment(type="image", path="/tmp/a.png")]
    clean = ImageResolver._clean_attachments(dirty)
    assert len(clean) == 1
    assert clean[0].path == "/tmp/a.png"


def test_agent_store_memory():
    from weauto_wx_cli.agent_store import MemoryStore
    import tempfile, os
    tmp = tempfile.mkdtemp()
    try:
        s = MemoryStore(tmp)
        s.write("core", "hello world")
        assert s.read("core") == "hello world"
        s.write("timeline", "updated")
        assert s.read("timeline") == "updated"
        s.write("random-name", "kept in core")
        assert s.read("core") == "kept in core"
        assert sorted(s.load_all()) == ["core", "timeline"]
        backup_path = s.backup("core")
        assert backup_path is not None
        assert backup_path.read_text(encoding="utf-8") == "kept in core"
    finally:
        import shutil
        shutil.rmtree(tmp)


def test_agent_write_memory_preserves_existing_on_merge_failure():
    from weauto_wx_cli.agent import Agent
    from weauto_wx_cli.agent_store import MemoryStore
    import tempfile, shutil

    tmp = tempfile.mkdtemp()
    try:
        cfg = load_config("config.toml.example")
        agent = Agent(cfg)
        agent.memory = MemoryStore(tmp)
        agent.memory.write("core", "# Core\n\n- old fact\n")
        agent._main_llm.chat = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("merge failed"))
        agent._execute_internal_action({"type": "write_memory", "name": "core", "content": "- new fact"})
        content = agent.memory.read("core")
        assert "- old fact" in content
        assert "- new fact" in content
        assert "待整理更新" in content
        assert list((Path(tmp) / ".backup").glob("core-*.md"))
    finally:
        shutil.rmtree(tmp)


def test_agent_store_skill_crud():
    from weauto_wx_cli.agent_store import SkillStore
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        s = SkillStore(tmp)
        s.write("greeter", "## Hello")
        assert s.read("greeter") == "## Hello"
        assert "greeter" in s.list()
        s.delete("greeter")
        assert "greeter" not in s.list()
    finally:
        shutil.rmtree(tmp)


def test_agent_store_people():
    from weauto_wx_cli.agent_store import PeopleStore
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        s = PeopleStore(tmp)
        s.write("张三", "## Events\n- test")
        assert "张三" in s.list()
        assert "test" in s.all_impressions()
    finally:
        shutil.rmtree(tmp)


def test_image_resolver_three_month_candidates():
    from weauto_wx_cli.image_resolver import ImageResolver
    candidates = ImageResolver._three_month_candidates(1778758428)
    assert any("2026-05" in c for c in candidates)
