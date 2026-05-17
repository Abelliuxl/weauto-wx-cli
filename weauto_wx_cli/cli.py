from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .agent import Agent
from .bot import AutoSpeakBot
from .config import load_config
from .sender import WeChatSender
from .wx_cli import WxCliClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="wx-cli read side plus Hermes action processing plus guarded macOS WeChat GUI sender."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config TOML. Missing file uses built-in defaults.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check wx-cli and GUI/OCR visibility.")
    sub.add_parser("run", help="Run the auto-speaking loop.")
    sub.add_parser("tick", help="Run one poll/generate/send cycle.")
    sub.add_parser("unread", help="Print normalized wx-cli unread/new messages.")
    sub.add_parser("events", help="Print Hermes event payloads for current wx-cli messages.")
    sub.add_parser("visible", help="Print visible WeChat chat rows from OCR.")
    sub.add_parser("hermes-check", help="Check Hermes gateway, preloaded skills, web, and local file tools.")
    logs = sub.add_parser("logs", help="Print the latest bot log file.")
    logs.add_argument("-n", "--lines", type=int, default=120)
    sub.add_parser("calibrate-rows", help="Run weauto-bridge row-box calibrator.")
    sub.add_parser("calibrate-row-title", help="Run weauto-bridge row-title region calibrator.")
    sub.add_parser("calibrate-preview", help="Run weauto-bridge preview region calibrator.")
    sub.add_parser("calibrate-title", help="Run weauto-bridge chat-title region calibrator.")
    send = sub.add_parser("send", help="Send one message through the GUI sender.")
    send.add_argument("title")
    send.add_argument("message")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(Path(args.config))
    bot = AutoSpeakBot(cfg)

    if args.command == "doctor":
        raise SystemExit(bot.doctor())
    if args.command == "run":
        bot.run_forever()
        return
    if args.command == "tick":
        bot.tick()
        return
    if args.command == "unread":
        wx = WxCliClient(cfg.wx_binary)
        for msg in wx.collect_incoming():
            print(f"{msg.chat_title}\t{msg.sender}\t{msg.timestamp}\t{msg.text}")
        return
    if args.command == "events":
        wx = WxCliClient(cfg.wx_binary)
        agent = Agent(cfg)
        for msg in wx.collect_incoming():
            print(json.dumps(agent._build_payload(msg), ensure_ascii=False, indent=2))
        return
    if args.command == "visible":
        sender = WeChatSender(cfg)
        for row in sender.list_visible_chats():
            print(f"{row.row_idx}\t{row.title}\t{row.preview}")
        return
    if args.command == "hermes-check":
        raise SystemExit(_agent_diag(cfg))
    if args.command == "logs":
        raise SystemExit(_print_logs(args.lines))
    if args.command.startswith("calibrate-"):
        raise SystemExit(_run_weauto_bridge_calibrator(args.command, Path(args.config)))
    if args.command == "send":
        sender = WeChatSender(cfg)
        sender.send_message(args.title, args.message)
        return


def _run_weauto_bridge_calibrator(command: str, config_path: Path) -> int:
    bridge_root = Path(os.environ.get("WEAUTO_BRIDGE_ROOT", "../weauto-bridge"))
    script_map = {
        "calibrate-rows": ["carlibrate_rows_ui.py"],
        "calibrate-row-title": ["carlibrate_row_title_ui.py"],
        "calibrate-preview": ["carlibrate_preview_region_ui.py"],
        "calibrate-title": ["carlibrate_title_ui.py"],
    }
    extra_args = {
        "calibrate-title": [
            "--section",
            "chat_title_region",
            "--ui-title",
            "WeChat 标题栏区域校准",
            "--label",
            "TITLE",
        ],
    }
    script = bridge_root / script_map[command][0]
    if not script.exists():
        print(f"[fail] weauto-bridge calibrator not found: {script}", file=sys.stderr)
        return 1
    cfg = config_path.expanduser()
    if not cfg.is_absolute():
        cfg = (Path.cwd() / cfg).resolve()
    cmd = [sys.executable, str(script), "--config", str(cfg), *extra_args.get(command, [])]
    print(f"[run] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=Path.cwd())
    return int(proc.returncode)


def _agent_diag(cfg) -> int:
    print("[check] agent mode")
    print(f"[check] main model={cfg.agent.main.model}")
    print(f"[check] vision model={cfg.agent.vision.model}")
    if cfg.agent.main.api_key:
        print("[check] main api key: set")
    elif os.environ.get(cfg.agent.main.api_key_env or ""):
        print(f"[check] main api key: from env {cfg.agent.main.api_key_env}")
    else:
        print(f"[warn] main api key not set (check {cfg.agent.main.api_key_env})")
    return 0


def _print_logs(lines: int) -> int:
    log_path = Path("logs/latest.log")
    if not log_path.exists():
        print("[fail] logs/latest.log does not exist yet")
        return 1
    count = max(1, int(lines or 120))
    proc = subprocess.run(["tail", "-n", str(count), str(log_path)])
    return int(proc.returncode)


if __name__ == "__main__":
    main()
