import time
from pathlib import Path
from tempfile import TemporaryDirectory

from weauto_wx_cli.agent import Agent, AgentResult
from weauto_wx_cli.config import load_config
from weauto_wx_cli.bot import AutoSpeakBot
from weauto_wx_cli.detector import title_matches
from weauto_wx_cli.detector import _pick_title_preview
from weauto_wx_cli.log_view import colorize_line, render_terminal_line
from weauto_wx_cli.people import PeopleContextBuilder, PersonAliasResolver
from weauto_wx_cli.python_sandbox import run_python_calculation
from weauto_wx_cli.sender import sanitize_wechat_message
from weauto_wx_cli.wx_cli import normalize_messages


def test_config_example_loads():
    cfg = load_config("config.toml.example")
    assert cfg.wx_binary == "wx"
    assert cfg.dry_run is True
    assert cfg.processing_mode == "agent"
    assert cfg.image_generation.enabled is False
    assert cfg.image_generation.provider == "dashscope_z_image"
    assert cfg.image_generation.model == "z-image-turbo"
    assert cfg.image_editing.enabled is False
    assert cfg.image_editing.provider == "dashscope_qwen_image_edit"
    assert cfg.image_editing.model == "qwen-image-2.0-pro"


def test_image_editing_inherits_generation_api_when_section_missing():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text(
            """
[image_generation]
enabled = true
base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
base_url_env = "BAILIAN_BASE_URL"
api_key = "test-key"
api_key_env = "BAILIAN_API_KEY"
model = "z-image-turbo"
""",
            encoding="utf-8",
        )
        cfg = load_config(path)
    assert cfg.image_editing.enabled is True
    assert cfg.image_editing.base_url == cfg.image_generation.base_url
    assert cfg.image_editing.base_url_env == cfg.image_generation.base_url_env
    assert cfg.image_editing.api_key == cfg.image_generation.api_key
    assert cfg.image_editing.api_key_env == cfg.image_generation.api_key_env
    assert cfg.image_editing.model == "qwen-image-2.0-pro"


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


def test_agent_parses_read_chat_history_tool_call():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_tool_calls(
        {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_chat_history",
                        "arguments": '{"limit":200}',
                    },
                }
            ]
        },
        default_title="群-游戏",
    )
    assert actions == [{"type": "read_chat_history", "chat_title": "群-游戏", "limit": 100}]


def test_agent_parses_generate_image_tool_call():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_tool_calls(
        {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "generate_image",
                        "arguments": '{"prompt":"一张赛博朋克风格猫咪海报","size":"1024x1024"}',
                    },
                }
            ]
        },
        default_title="群-游戏",
    )
    assert actions == [
        {
            "type": "generate_image",
            "title": "群-游戏",
            "prompt": "一张赛博朋克风格猫咪海报",
            "size": "1024x1024",
        }
    ]


def test_agent_parses_edit_image_tool_call_without_explicit_path():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_tool_calls(
        {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "edit_image",
                        "arguments": '{"prompt":"把背景换成海边日落","size":"1024x1024"}',
                    },
                }
            ]
        },
        default_title="群-游戏",
    )
    assert actions == [
        {
            "type": "edit_image",
            "title": "群-游戏",
            "prompt": "把背景换成海边日落",
            "size": "1024x1024",
        }
    ]


def test_agent_parses_run_python_tool_call():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_tool_calls(
        {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "run_python",
                        "arguments": '{"code":"print(round((32*60 - (7*60+30))/(32*60)*100, 2))"}',
                    },
                }
            ]
        },
        default_title="群-游戏",
    )
    assert actions == [{"type": "run_python", "code": "print(round((32*60 - (7*60+30))/(32*60)*100, 2))"}]


def test_agent_normalizes_calculate_expression():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_actions(
        '{"actions":[{"type":"calculate","expression":"(10 + 5) / 3"}]}',
        default_title="测试群",
    )
    assert actions == [{"type": "run_python", "code": "print((10 + 5) / 3)"}]


def test_agent_parses_wow_character_url_tool_call():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    actions = agent._parse_tool_calls(
        {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "build_wow_character_url",
                        "arguments": '{"player":"吴松竹","class_name":"战士"}',
                    },
                }
            ]
        },
        default_title="群-临沧",
    )
    assert actions == [
        {
            "type": "build_wow_character_url",
            "title": "群-临沧",
            "character": "",
            "server": "",
            "player": "吴松竹",
            "class_name": "战士",
        }
    ]


def test_human_log_summarizes_message_and_actions():
    line = AutoSpeakBot._human_log_line(
        "message",
        {
            "chat": "群-测试",
            "sender": "张三",
            "message_type": "text",
            "text": "帮我查一下今天的副本安排",
        },
    )
    assert line == "[msg] 群-测试 <- 张三 text: 帮我查一下今天的副本安排"

    summary = AutoSpeakBot._human_log_line(
        "agent-result",
        {
            "action_count": 2,
            "actions": [
                {"type": "search_web_volc", "query": "暴雪 魔兽世界 国服"},
                {"type": "send_message", "title": "群-测试", "message": "我查到了。"},
            ],
        },
    )
    assert summary == "[agent] actions=2 search_web_volc: 暴雪 魔兽世界 国服; send_message->群-测试: 我查到了。"


def test_log_view_hides_json_and_colors_human_lines():
    assert render_terminal_line('{"ts":"now","event":"tick","incoming":1}', color=False) is None
    assert render_terminal_line("[msg] 群 <- 张三 text: hi", color=False) == "[msg] 群 <- 张三 text: hi"
    colored = colorize_line("[error] failed", color=True)
    assert colored.startswith("\033[1m\033[31m")
    assert colored.endswith("\033[0m")


def test_agent_hides_generate_image_tool_until_enabled():
    cfg = load_config("config.toml.example")
    cfg.image_generation.enabled = False
    agent = Agent(cfg)
    names = {item["function"]["name"] for item in agent._available_tool_specs()}
    assert "generate_image" not in names

    cfg.image_generation.enabled = True
    agent = Agent(cfg)
    names = {item["function"]["name"] for item in agent._available_tool_specs()}
    assert "generate_image" in names
    assert "run_python" in names


def test_python_sandbox_runs_calculation():
    result = run_python_calculation(
        "xs = [12, 15, 18, 20, 21, 22, 30, 99]\n"
        "ys = sorted(xs)[1:-1]\n"
        "print(round(sum(ys) / len(ys), 2))"
    )
    assert result.ok is True
    assert result.output.strip() == "21.0"


def test_python_sandbox_allows_modular_pow():
    result = run_python_calculation(
        "mod = 10**8\n"
        "a = pow(987654321, 123456789, mod)\n"
        "b = pow(123456789, 987654321, mod)\n"
        "print(f'{(a + b) % mod:08d}')"
    )
    assert result.ok is True
    assert result.output.strip() == "82262470"


def test_python_sandbox_allows_small_helper_functions():
    result = run_python_calculation(
        "def mod_pow(base, exp, mod):\n"
        "    result = 1\n"
        "    base = base % mod\n"
        "    while exp > 0:\n"
        "        if exp & 1:\n"
        "            result = (result * base) % mod\n"
        "        exp >>= 1\n"
        "        base = (base * base) % mod\n"
        "    return result\n"
        "print(mod_pow(7, 13, 1000))"
    )
    assert result.ok is True
    assert result.output.strip() == "407"


def test_python_sandbox_rejects_dangerous_calls():
    result = run_python_calculation("print(open('/etc/passwd').read())")
    assert result.ok is False
    assert "allowed" in result.error


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


def test_agent_finalize_wraps_plain_content_as_send_message():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    seen = {}

    def fake_chat(messages, **kwargs):
        seen.update(kwargs)
        return {"message": {"content": "没拿到原链接，只能按标题猜搜索。"}}

    agent._reply_llm.chat = fake_chat
    result = agent.finalize_response(chat_title="群-测试", original_text="@助手 看看")
    assert seen["tool_choice"] is None
    assert seen["tools"] is None
    assert result.actions == [
        {"type": "send_message", "title": "群-测试", "message": "没拿到原链接，只能按标题猜搜索。"}
    ]


def test_agent_plain_content_is_composed_by_reply_llm():
    cfg = load_config("config.toml.example")
    agent = Agent(cfg)
    seen_reply = {}

    def fake_main_chat(messages, **kwargs):
        return {"message": {"content": "解释自我进化边界：能写memory和skill，不能改底层代码。"}}

    def fake_reply_chat(messages, **kwargs):
        seen_reply["messages"] = messages
        seen_reply["kwargs"] = kwargs
        return {"message": {"content": "能迭代，但不是自己改底层。能写 memory 和 skill，不能改代码。"}}

    agent._main_llm.chat = fake_main_chat
    agent._reply_llm.chat = fake_reply_chat
    msg = normalize_messages([{"chat": "real刘晓亮", "chat_type": "private", "content": "能不能自我进化？"}])[0]
    result = agent.handle_message(msg)
    assert seen_reply["kwargs"]["tool_choice"] is None
    assert "解释自我进化边界" in seen_reply["messages"][1]["content"]
    assert result.actions == [
        {"type": "send_message", "title": "real刘晓亮", "message": "能迭代，但不是自己改底层。能写 memory 和 skill，不能改代码。"}
    ]


def test_agent_uses_main_model_from_config():
    cfg = load_config("config.toml")
    agent = Agent(cfg)
    assert agent._main_llm.model == cfg.agent.main.model
    assert agent._reply_llm.model == cfg.agent.reply.model
    assert cfg.agent.main.model == "deepseek-v4-flash"
    assert cfg.agent.main.temperature == 0.0
    assert cfg.agent.reply.model == "deepseek-v4-flash"
    assert cfg.agent.reply.temperature == 0.7


def test_sanitize_wechat_message_removes_markdown_but_keeps_numbered_lists():
    raw = """### 结论
**华硕能源**不是 *ASUS*。

- 这是星号项目
* 这是另一条
> 引用也别发

1. 第一条
2. 第二条

```text
代码块内容
```
链接：[官网](https://example.com)
AI味：core_memory —— skill_update --- reply_llm
"""
    assert sanitize_wechat_message(raw) == (
        "结论\n"
        "华硕能源不是 ASUS。\n\n"
        "这是星号项目\n"
        "这是另一条\n"
        "引用也别发\n\n"
        "1. 第一条\n"
        "2. 第二条\n\n"
        "代码块内容\n"
        "链接：官网 https://example.com\n"
        "AI味：corememory，skillupdate，replyllm"
    )


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


def test_group_at_other_person_respects_cooldown():
    cfg = load_config("config.toml.example")
    cfg.group_reply_cooldown_sec = 999.0
    bot = AutoSpeakBot(cfg)
    bot._last_group_reply["群-测试"] = time.time()
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "B", "content": "@张三 查一下", "type": "文本"}]
    )[0]
    assert bot._reply_decision(msg)[1].startswith("group-cooldown")


def test_group_non_urgent_blocked_by_cooldown():
    cfg = load_config("config.toml.example")
    cfg.group_reply_cooldown_sec = 999.0
    bot = AutoSpeakBot(cfg)
    bot._last_group_reply["群-测试"] = time.time()
    msg = normalize_messages(
        [{"chat": "群-测试", "sender": "A", "content": "普通群消息", "type": "文本"}]
    )[0]
    assert bot._should_reply(msg) is False


def test_web_actions_read_chat_history_then_finalize(monkeypatch):
    cfg = load_config("config.toml.example")
    bot = AutoSpeakBot(cfg)
    calls = {}

    def fake_get_history(chat_title, limit=10):
        calls["history"] = (chat_title, limit)
        return "[12:00] 石山勇: [链接] 视频标题"

    def fake_handle_message(msg):
        calls["feed_text"] = msg.text
        return AgentResult(actions=[], raw_response='{"message":{"content":"普通 content"}}')

    def fake_finalize_response(**kwargs):
        calls["finalize"] = kwargs
        return AgentResult(
            actions=[{"type": "send_message", "title": kwargs["chat_title"], "message": "没拿到原链接。"}],
            raw_response="{}",
        )

    monkeypatch.setattr(bot.agent, "_get_history", fake_get_history)
    monkeypatch.setattr(bot.agent, "handle_message", fake_handle_message)
    monkeypatch.setattr(bot.agent, "finalize_response", fake_finalize_response)
    actions = bot._process_web_actions(
        [{"type": "read_chat_history", "limit": 50}],
        original_chat_title="群-测试",
        original_text="@助手 看看石山勇转发的视频",
        original_sender="A",
    )
    assert calls["history"] == ("群-测试", 50)
    assert "[chat_history] 群-测试 limit=50" in calls["feed_text"]
    assert "视频标题" in calls["finalize"]["tool_trace"]
    assert actions == [{"type": "send_message", "title": "群-测试", "message": "没拿到原链接。"}]


def test_web_actions_generate_image_becomes_send_image(monkeypatch):
    cfg = load_config("config.toml.example")
    bot = AutoSpeakBot(cfg)
    tmp = TemporaryDirectory()
    image_path = Path(tmp.name) / "generated.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = {}

    def fake_generate_file(*, prompt, size=""):
        calls["prompt"] = prompt
        calls["size"] = size
        return image_path

    monkeypatch.setattr(bot.image_generator, "generate_file", fake_generate_file)
    try:
        actions = bot._process_web_actions(
            [{"type": "generate_image", "prompt": "画一张猫咪海报", "size": "1024x1024"}],
            original_chat_title="群-测试",
            original_text="@助手 画一张猫咪海报",
            original_sender="A",
        )
        assert calls == {"prompt": "画一张猫咪海报", "size": "1024x1024"}
        assert actions == [
            {"type": "send_image", "title": "群-测试", "image_path": str(image_path)}
        ]
    finally:
        tmp.cleanup()


def test_web_actions_run_python_feeds_result(monkeypatch):
    cfg = load_config("config.toml.example")
    bot = AutoSpeakBot(cfg)
    calls = {}

    def fake_handle_message(msg):
        calls["feed_text"] = msg.text
        return AgentResult(
            actions=[{"type": "send_message", "title": "群-测试", "message": "结果是 76.56%。"}],
            raw_response="{}",
        )

    monkeypatch.setattr(bot.agent, "handle_message", fake_handle_message)
    actions = bot._process_web_actions(
        [{"type": "run_python", "code": "print(round((32*60 - (7*60+30))/(32*60)*100, 2))"}],
        original_chat_title="群-测试",
        original_text="算一下用了百分之多少",
        original_sender="A",
    )
    assert "[python_calculation] ok=true" in calls["feed_text"]
    assert "76.56" in calls["feed_text"]
    assert actions == [{"type": "send_message", "title": "群-测试", "message": "结果是 76.56%。"}]


def test_web_actions_filters_internal_actions_without_reply():
    cfg = load_config("config.toml.example")
    bot = AutoSpeakBot(cfg)
    actions = bot._process_web_actions([{"type": "write_memory", "name": "timeline", "content": "- x"}])
    assert actions == []


def test_web_actions_builds_wow_character_url_without_python():
    cfg = load_config("config.toml.example")
    bot = AutoSpeakBot(cfg)
    actions = bot._process_web_actions(
        [{"type": "build_wow_character_url", "title": "群-临沧", "player": "吴松竹", "class_name": "战士"}],
        original_chat_title="群-临沧",
    )
    assert len(actions) == 1
    assert actions[0]["type"] == "send_message"
    assert "体育老师（通灵学院）" in actions[0]["message"]
    assert "https://wow.blizzard.cn/character/#/scholomance/%E4%BD%93%E8%82%B2%E8%80%81%E5%B8%88" in actions[0]["message"]


def test_title_matching_tolerates_ocr_noise():
    assert title_matches("测试群", "测试群 (12)")
    assert title_matches("memberalpha", "member alpha")
    assert title_matches("群-临沧", "群－临沧")
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


def test_people_context_resolves_alphanumeric_wechat_id_alias():
    tmp = TemporaryDirectory()
    path = Path(tmp.name) / "PEOPLE_ALIASES.md"
    path.write_text("- 昭言 -> 舒总, c123528947\n- 巨奶 -> 茉芋莉🐧, 茉芋莉\n", encoding="utf-8")
    try:
        resolver = PersonAliasResolver(str(path))
        assert resolver.resolve("c123528947") == "昭言"
        assert resolver.resolve("茉芋莉🐧") == "巨奶"

        builder = PeopleContextBuilder(aliases_path=str(path), max_items=8)
        payload = builder.build(sender="c123528947", text="@茉芋莉🐧 屯来吗")
        assert payload["sender_identity"]["canonical_name"] == "昭言"
        assert payload["sender_identity"]["observed_name"] == "c123528947"
        assert payload["mentioned_people"][0]["canonical_name"] == "巨奶"
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
