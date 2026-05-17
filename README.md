# weauto-wx-cli

## Background

This project is for testing whether local WeChat automation on an Intel macOS machine can be built around `wx-cli` and, if needed, a small macOS UI automation layer.

Current date of the initial investigation: 2026-05-14.

## Current Conclusion

`wx-cli` looks sufficient for reading local WeChat data on macOS Intel, but not sufficient by itself for full WeChat client operation.

Expected working read-only capabilities:

- List recent sessions.
- List unread sessions.
- Read chat history.
- Search local messages.
- Read incremental new messages.
- Query contacts and group members.
- Export conversation context.

Known limitation:

- `wx-cli` is a local data query tool. It does not appear to provide stable commands for sending messages, clicking the WeChat UI, joining chats, or otherwise controlling the native macOS WeChat client.

OpenCLI currently does not change that much:

- `opencli weixin` targets WeChat Official Account / public article workflows, not the native WeChat chat client.
- OpenCLI can expose `wx` as an external CLI, so `opencli wx ...` is mostly a wrapper around `wx-cli`.
- OpenCLI desktop adapters mainly work well for Electron apps through CDP. WeChat for macOS is native Cocoa and does not expose CDP, so a WeChat desktop adapter would likely need AppleScript, Accessibility, clipboard, or another macOS automation path.

## Target

Build and verify a minimal local bridge for an AI agent to:

1. Read unread and recent WeChat messages from the local Mac client.
2. Search and retrieve chat history for context.
3. Detect new messages incrementally.
4. Optionally send replies through a separate macOS UI automation layer if read-only access proves reliable.

The first milestone should stay read-only. Sending messages should be treated as a separate, higher-risk milestone.

## Environment Assumptions

- Machine: macOS on Intel.
- WeChat app path: `/Applications/WeChat.app`.
- WeChat version likely around 4.1.x.
- `wx-cli` may require:
  - Full Disk Access for the terminal app.
  - ad-hoc re-signing of WeChat.
  - `sudo wx init` to scan WeChat process memory.
  - Re-signing again after WeChat updates.

## Candidate Tooling

Primary candidate:

- `jackwener/wx-cli`
  - GitHub: https://github.com/jackwener/wx-cli
  - Install: `npm install -g @jackwener/wx-cli`
  - Manual macOS Intel binary: `wx-macos-x86_64`

Secondary references:

- OpenCLI adapters: https://opencli.info/docs/adapters/
- OpenCLI Weixin adapter: https://opencli.info/docs/adapters/browser/weixin.html
- OpenCLI non-Electron desktop note: https://opencli.info/docs/advanced/electron.html
- Homebrew WeChat cask: https://formulae.brew.sh/cask/wechat

## Verification Checklist

Run these in a fresh session before writing integration code.

1. Confirm local platform and WeChat version.

```bash
uname -m
mdls -name kMDItemVersion /Applications/WeChat.app
```

2. Install or locate `wx`.

```bash
command -v wx
wx --version
```

If not installed:

```bash
npm install -g @jackwener/wx-cli
```

3. Prepare macOS permissions.

- Grant Full Disk Access to the terminal app.
- Be ready to grant Accessibility only if later testing UI automation.

4. Initialize `wx-cli`.

```bash
codesign --force --deep --sign - /Applications/WeChat.app
killall WeChat
open /Applications/WeChat.app
sudo wx init
```

If signing fails because a nested signature is in use, follow the workaround from the `wx-cli` README.

5. Smoke test read-only commands.

```bash
wx sessions
wx unread
wx new-messages
wx search "测试" -n 5
```

6. If read-only works, capture output shape.

```bash
wx sessions --json
wx unread --json
wx new-messages --json
```

Save examples in a local `fixtures/` directory only after redacting private content.

## Proposed Minimal Architecture

Read-only milestone:

- A small wrapper script or library calls `wx` commands.
- Normalize YAML/JSON output into a stable internal schema.
- Keep the wrapper read-only and avoid storing decrypted databases in the project.
- Add a `doctor` command that checks:
  - `wx` is installed.
  - WeChat is running.
  - `wx daemon status` succeeds.
  - recent sessions can be read.

Send-message milestone:

- Use macOS Accessibility, Screen Recording, OCR, clipboard, and AppleScript to
  activate WeChat, select a visible conversation row, paste text or an image,
  and press Enter.
- Keep `dry_run = true` by default until visible-row matching and title
  verification have been tested on the current WeChat layout.
- Let Hermes decide what action to take, but execute the actual WeChat send
  locally in this project.

## Implemented Skeleton

This repository now contains a minimal local auto-speaking bot that keeps the
read side and send side intentionally separate.

Pipeline:

1. `wx-cli` reads local WeChat data with `wx new-messages --json` and
   `wx unread --json`.
2. `weauto_wx_cli.wx_cli` normalizes the command output into `WxMessage`.
3. `weauto_wx_cli.bot` deduplicates messages with `data/state.json`, applies
   chat filters, and sends the normalized event to Hermes.
4. `weauto_wx_cli.bridge` expects Hermes to return structured actions:
   `send_message`, `send_image`, `focus_chat`, or `noop`.
5. `weauto_wx_cli.sender` activates WeChat, screenshots the visible window,
   uses OCR to find the target row in the left conversation list, verifies the
   focused title, clicks the input box, pastes the generated message, and
   presses Enter.

This is deliberately different from `weauto` / `weauto-bridge`:

- Message acquisition is not OCR-based. It comes from `wx-cli`.
- OCR is only used in the send path to identify the visible target chat row and
  verify focus before sending.
- Message handling follows the `weauto-bridge` model: Hermes receives events
  and returns actions. This project executes those actions locally.
- Sending is guarded by `dry_run = true` by default.

## Hermes Processing

The default processing mode is:

```toml
processing_mode = "hermes"

[hermes]
mode = "gateway"
gateway_base_url = "http://127.0.0.1:8642"
import_weauto_bridge_config = true
weauto_bridge_config_path = "/path/to/weauto-bridge/config.toml"
skills = ["weauto-wx-cli-wechat-reply", "wechat-person-memory"]
```

This mirrors `weauto-bridge`: Hermes receives a normalized event and returns
structured actions. This project then executes those actions by controlling the
local WeChat GUI.

`weauto-wx-cli-wechat-reply` only describes the event and action protocol to
Hermes. Reply frequency, group keyword gates, forced replies for mentions, and
other eligibility rules stay in this local project so Hermes does not control
when a reply should happen.

Because this project calls Hermes through the OpenAI-compatible gateway API, it
preloads the configured local Hermes skills into the gateway prompt itself. CLI
mode uses Hermes' native `-s` flags instead.

For memory updates, Hermes' `api_server` platform also needs local tools. On
this machine `/path/to/.hermes/config.yaml` enables `file`, `terminal`, and
`skills` for `platform_toolsets.api_server` so the memory skill can read/write
`/path/to/.hermes/people`.

Hermes receives a prompt containing this JSON shape:

```json
{
  "type": "wechat_message",
  "event": {
    "source": "wechat",
    "chat_title": "群-测试",
    "chat_type": "group",
    "sender": "张三",
    "message_type": "image",
    "text": "[图片]",
    "attachments": [
      {
        "type": "image",
        "path": "/local/path/image.jpg"
      }
    ],
    "raw": {}
  }
}
```

Hermes should return JSON like:

```json
{
  "actions": [
    {
      "type": "send_message",
      "title": "群-测试",
      "message": "我看到了"
    }
  ]
}
```

Supported actions:

- `send_message`: `{"type":"send_message","title":"会话名","message":"内容"}`
- `send_image`: `{"type":"send_image","title":"会话名","image_path":"/local/file.png"}`
- `focus_chat`: `{"type":"focus_chat","title":"会话名"}`
- `noop`: `{"type":"noop"}`

In gateway mode, this project automatically imports the gateway URL, API key,
model, command, and home directory from the existing `weauto-bridge` config when
those fields are not set locally. The API key is not copied into this repository.
CLI mode is still available for fallback; in that case, change `hermes.command`
and related CLI fields.

## People Aliases

Before a message is sent to Hermes, this project enriches the event with
deterministic people identity metadata from:

```text
data/PEOPLE_ALIASES.md
```

The original `sender` and `text` are not rewritten. Instead, the Hermes event
gets extra fields:

```json
{
  "sender": "memberalpha",
  "sender_identity": {
    "observed_name": "memberalpha",
    "canonical_name": "用户甲",
    "aliases": ["亮哥", "fake 用户甲"]
  },
  "text": "@巴音布鲁克之王 亮哥这个怎么打",
  "mentioned_people": [
    {
      "observed_name": "巴音布鲁克之王",
      "canonical_name": "张捷"
    }
  ],
  "people_context": [
    {
      "canonical_name": "用户甲",
      "matched_by": ["sender:memberalpha", "text:亮哥"]
    },
    {
      "canonical_name": "张捷",
      "matched_by": ["mention:巴音布鲁克之王"]
    }
  ]
}
```

This gives Hermes the real-person mapping without asking it to infer aliases
from the whole file on every turn. It also handles group messages where `wx-cli`
sender names, group nicknames, and `@` display names differ.

When a sender resolves to a canonical person, the event also includes a
`person_memory` target pointing at `/path/to/.hermes/people/*.md`. The local
Hermes `wechat-person-memory` skill is loaded so Hermes can read/update that
person's impression file before returning the final action JSON.

## Image Messages

On this machine, `wx-cli` image messages currently look like:

```json
{
  "content": "[图片] local_id=229",
  "local_id": 229,
  "type": "图片"
}
```

It does not currently expose a stable local file path for normal chat images.
The adapter therefore handles images conservatively:

- If the `wx-cli` JSON contains common fields like `image_path`, `media_path`,
  `file_path`, `local_path`, `thumb_path`, `path`, `url`, or `thumb_url`, they
  are converted into `attachments`.
- If the message type/content only says it is an image, the event is sent as
  `message_type = "image"` with `text = "[图片]"`, an image attachment whose
  `raw.local_id` is set, and the original `raw` JSON.
- Hermes is told that an image attachment without `path` or `url` is metadata
  only, so it should not claim to have seen the image content.
- If later we resolve WeChat's cached image file path from `local_id`, that
  resolver should be added in `weauto_wx_cli.wx_cli` before the event is sent to
  Hermes.

## Files Added

- `weauto_wx_cli/wx_cli.py`: wrapper around `wx` and tolerant output
  normalization.
- `weauto_wx_cli/bridge.py`: Hermes event payload and action parser.
- `weauto_wx_cli/sender.py`: macOS GUI sender based on the weauto/weauto-bridge
  OCR-click-paste-send pattern.
- `weauto_wx_cli/detector.py`: visible chat row OCR and title matching.
- `weauto_wx_cli/reply.py`: legacy template/command reply generation for
  `processing_mode = "template"` smoke tests.
- `weauto_wx_cli/bot.py`: polling, filtering, dedupe, and dispatch loop.
- `weauto_wx_cli/cli.py`: command line entrypoint.
- `config.toml.example`: default runtime configuration.

## Usage

Create a local config:

```bash
cp config.toml.example config.toml
```

Install dependencies:

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Check wx-cli and visible WeChat OCR:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml doctor
```

Inspect normalized incoming messages:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml unread
```

Print the exact JSON payload that will be sent to Hermes:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml events
```

Inspect visible OCR rows:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml visible
```

Dry-run a single send:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml send "会话名" "测试消息"
```

Run one polling cycle:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml tick
```

Run continuously:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml run
```

Or use the helper script, which creates `.venv` with `python3.12` by default,
checks Hermes gateway status, runs `doctor`, and writes a timestamped log:

```bash
./start_bot.sh
```

Useful one-command variants:

```bash
./start_bot.sh tick
./start_bot.sh visible
./start_bot.sh hermes-check
./start_bot.sh logs -n 200
```

Logs are written to `logs/YYYYMMDD_HHMMSS_<command>.log`. `logs/latest.log`
points at the latest non-`logs` command run. The bot logs structured JSON lines
for incoming messages, skip reasons, Hermes results, action execution, and send
errors so a failed turn can be debugged from one file.

Only set `dry_run = false` after:

- WeChat is visible and logged in.
- Terminal/Python has Accessibility and Screen Recording permissions.
- `visible` can correctly OCR the target conversation title.
- `send` dry-run reports the expected visible row.

Current local setup:

- `wx-cli` is installed as `wx 0.1.10`.
- WeChat is `/Applications/WeChat.app`, observed as version `4.1.7`.
- WeChat has been ad-hoc re-signed and `wx init` has extracted keys.
- `config.toml` has been created locally and is gitignored.
- Hermes gateway config is imported from `/path/to/weauto-bridge/config.toml`.
- `dry_run = true` is still enabled.

`hermes-check` directly probes the configured Hermes path without touching
WeChat. It checks that configured local skills are preloaded in gateway mode and
asks Hermes to write `data/hermes_gateway_probe.txt` using local tools.

Proxy handling is explicit. `config.toml` contains:

```toml
[proxy]
enabled = true
url = "http://127.0.0.1:7890"
no_proxy = "127.0.0.1,localhost,::1"
```

`start_bot.sh` exports these variables only for this project process. It
explicitly removes proxy variables from `launchctl` before restarting the
Hermes gateway, because launchd-managed Python can fail to route to the LAN
proxy even when `curl` can. Local gateway calls to `127.0.0.1:8642` still bypass
the proxy.

The startup script also sets `HERMES_MAX_ITERATIONS=8` for the launchd Hermes
gateway. That is an operational guard against long tool loops blocking the
single WeChat send queue; it does not hard-code what Hermes is allowed to say.

## Send Serialization

All GUI send operations are serialized. `run`, `tick`, and direct `send`
commands take the same file lock from `send_lock_path` before touching WeChat,
mouse, keyboard, or clipboard. Inside one polling cycle, returned Hermes actions
are executed in order, with `send_action_interval_sec` between send-like actions.

This matters because the send side is OCR/GUI automation. Multiple concurrent
senders would otherwise race for WeChat focus and the macOS clipboard.

## Send Logic Notes

The sender follows the weauto/weauto-bridge GUI pattern but trims it down to
the project's single responsibility:

1. Activate WeChat with AppleScript.
2. Read the largest visible WeChat window bounds through Quartz.
3. Screenshot the window.
4. OCR the left chat list only.
5. Match the target `chat_title` from `wx-cli` against visible OCR row titles.
6. Click the matched row.
7. OCR the chat title region and require it to match the intended target when
   `focus_verify_enabled = true`.
8. Click the configured input point.
9. Copy text to the clipboard, paste with Cmd+V, then send with Enter.

If the target row is not visible, the sender skips sending instead of guessing.
Manual row boxes can be enabled in `[manual_rows]` if automatic row bucketing
drifts on a fixed WeChat layout.

## Calibration

The sending side now accepts the same core calibration keys used by
`weauto-bridge`:

- `use_manual_row_boxes`
- `manual_row_boxes_path`
- `row_title_region_enabled`
- `row_title_region`
- `preview_region_enabled`
- `preview_text_region`
- `rows_max`
- `row_height_ratio`

This project also exposes wrappers for the existing `weauto-bridge` calibrators:

```bash
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml calibrate-rows
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml calibrate-row-title
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml calibrate-preview
./.venv/bin/python -m weauto_wx_cli.cli --config config.toml calibrate-title
```

The current local `config.toml` has `use_manual_row_boxes = true`, and
`data/manual_row_boxes.json` was copied from the existing `weauto-bridge`
calibration. That is why sending uses fixed row boxes instead of dynamic
new-message OCR scanning.

## Main Risks

- WeChat updates can break database parsing or key extraction.
- macOS security changes can block process memory scanning.
- Re-signing WeChat may affect auto-update behavior and needs to be repeated after updates.
- Local chat content is sensitive; logs and fixtures must be redacted.
- UI-based sending can focus the wrong chat or paste into the wrong window if not carefully guarded.

## Suggested Next Session Prompt

Use this in the next session:

> Work in `/path/to/weauto-wx-cli`. First read `README.md`, then run `python3.12 -m compileall weauto_wx_cli tests` and the manual tests in `tests/test_core.py`. Verify `wx sessions --json`, `wx new-messages --json`, and `hermes gateway status`. Keep `dry_run = true` unless I explicitly ask to send real messages. The architecture is: read with `wx-cli`, process with Hermes gateway like `weauto-bridge`, send locally through this project's OCR/click/paste sender.
