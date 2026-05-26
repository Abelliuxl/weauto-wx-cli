from __future__ import annotations

import json
import os
import sys
from pathlib import Path


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

COLORS = {
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "magenta": "\033[35m",
    "red": "\033[31m",
    "yellow": "\033[33m",
}

PREFIX_COLORS = {
    "[start]": ("green", True),
    "[start-bot]": ("green", True),
    "[init]": ("green", False),
    "[doctor]": ("cyan", False),
    "[row]": ("cyan", False),
    "[startup]": ("cyan", False),
    "[tick]": ("cyan", False),
    "[msg]": ("cyan", True),
    "[skip]": ("yellow", False),
    "[cooldown]": ("yellow", False),
    "[agent]": ("magenta", True),
    "[llm]": ("magenta", False),
    "[tools]": ("blue", True),
    "[tool]": ("blue", False),
    "[finalize]": ("magenta", False),
    "[image]": ("green", False),
    "[action]": ("green", False),
    "[focus]": ("green", False),
    "[send]": ("green", True),
    "[sent]": ("green", True),
    "[sent-image]": ("green", True),
    "[dry-run]": ("yellow", True),
    "[noop]": ("yellow", False),
    "[warn]": ("yellow", True),
    "[fail]": ("red", True),
    "[error]": ("red", True),
    "[heartbeat]": ("cyan", False),
    "[check]": ("cyan", False),
    "[run]": ("cyan", False),
}


def is_json_event_line(line: str) -> bool:
    text = line.strip()
    if not text.startswith("{"):
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("event"), str)


def should_use_color(stream: object = sys.stdout) -> bool:
    forced = os.environ.get("WEAUTO_LOG_COLOR", "").strip().lower()
    if forced in {"1", "true", "yes", "always"}:
        return True
    if forced in {"0", "false", "no", "never"} or os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def colorize_line(line: str, *, color: bool = True) -> str:
    if not color:
        return line
    stripped = line.lstrip()
    leading = line[: len(line) - len(stripped)]
    for prefix, (color_name, bold) in PREFIX_COLORS.items():
        if stripped.startswith(prefix):
            style = COLORS[color_name]
            if bold:
                style = BOLD + style
            return f"{leading}{style}{stripped}{RESET}"
    return f"{DIM}{line}{RESET}"


def render_terminal_line(line: str, *, color: bool = True, hide_json: bool = True) -> str | None:
    if hide_json and is_json_event_line(line):
        return None
    return colorize_line(line, color=color)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m weauto_wx_cli.log_view LOG_FILE", file=sys.stderr)
        return 2
    log_path = Path(args[0])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    use_color = should_use_color(sys.stdout)
    hide_json = os.environ.get("WEAUTO_TERMINAL_JSON", "").strip().lower() not in {"1", "true", "yes"}

    with log_path.open("a", encoding="utf-8") as log_file:
        for raw in sys.stdin:
            log_file.write(raw)
            log_file.flush()
            line = raw.rstrip("\n")
            rendered = render_terminal_line(line, color=use_color, hide_json=hide_json)
            if rendered is None:
                continue
            print(rendered, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
