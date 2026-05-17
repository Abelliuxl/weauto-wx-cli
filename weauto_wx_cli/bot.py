from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .agent import Agent, AgentError
from .config import AppConfig
from .models import OutboundReply, WxMessage
from .reply import ReplyGenerator
from .sender import SendError, WeChatSender
from .state import SeenState
from .wx_cli import WxCliClient, WxCliError


class AutoSpeakBot:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.wx = WxCliClient(
            cfg.wx_binary,
            resolve_images=cfg.enable_image_resolver,
            image_output_dir=cfg.image_output_dir,
        )
        self.sender = WeChatSender(cfg)
        self.reply = ReplyGenerator(cfg)
        self.agent = Agent(cfg)
        self.state = SeenState(cfg.state_path)
        self._last_group_reply: dict[str, float] = {}
        self._own_wxid, self._own_display = self._detect_self_info()
        self.agent.add_self_identifiers(self._own_wxid, self._own_display)
        self._retry_queue: list[WxMessage] = []

    def doctor(self) -> int:
        ok = True
        self._log("doctor", wx_binary=self.cfg.wx_binary)
        if self.wx.is_installed():
            self._log("doctor", status="ok", check="wx-cli found")
        else:
            self._log("doctor", status="fail", check="wx-cli not found")
            ok = False
        try:
            rows = self.sender.list_visible_chats()
            self._log("doctor", status="ok", visible_rows=len(rows))
            for row in rows[:8]:
                self._log("visible-row", row=row.row_idx, title=row.title, preview=row.preview)
        except Exception as exc:
            self._log("doctor", status="fail", check="WeChat GUI/OCR", error=str(exc))
            ok = False
        return 0 if ok else 1

    def run_forever(self) -> None:
        self.state.load()
        if self.cfg.skip_existing_on_start:
            self._mark_current_messages_seen()
        self._log(
            "start",
            dry_run=self.cfg.dry_run,
            poll_interval_sec=self.cfg.poll_interval_sec,
            processing_mode=self.cfg.processing_mode,
        )
        last_heartbeat = 0.0
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self._log("error", error=str(exc))
            if self.cfg.agent.heartbeat_enabled:
                now = time.time()
                if now - last_heartbeat >= self.cfg.agent.heartbeat_interval_sec:
                    try:
                        self._heartbeat()
                    except Exception as exc:
                        self._log("heartbeat-error", error=str(exc))
                    last_heartbeat = now
            time.sleep(max(0.2, self.cfg.poll_interval_sec))

    def tick(self) -> list[dict[str, Any]]:
        self.state.load()
        messages: list[WxMessage] = list(self._retry_queue)
        self._retry_queue.clear()
        try:
            incoming = self.wx.collect_incoming()
        except WxCliError as exc:
            self._log("wx-cli-error", error=str(exc))
            return []
        messages.extend(incoming)
        self._log("tick", incoming=len(incoming), retry=len(messages) - len(incoming))

        pending: list[tuple[str, WxMessage]] = []
        for msg in messages:
            fp = msg.fingerprint()
            if self.state.contains(fp):
                self._log("message-skip", reason="seen", fingerprint=fp, chat=msg.chat_title)
                continue
            pending.append((fp, msg))

        all_final: list[dict[str, Any]] = []
        for fp, msg in pending:
            self._log(
                "message",
                fingerprint=fp,
                chat=msg.chat_title,
                sender=msg.sender,
                chat_type=msg.chat_type,
                message_type=msg.message_type,
                text=msg.text,
                attachments=len(msg.attachments),
                attachment_urls=[a.url for a in msg.attachments if a.url][:5],
            )
            raw_sender = msg.sender or (msg.chat_title if msg.chat_type == "private" else "")
            sender = self.agent._resolve_sender(raw_sender)
            self.agent.chat_history.append(msg.chat_title, sender, msg.text, msg.timestamp)
            should_reply, reason = self._reply_decision(msg)
            if not should_reply:
                self.state.add(fp)
                self._log("message-skip", reason=reason, fingerprint=fp, chat=msg.chat_title)
                continue
            if self._is_group(msg.chat_title) and not self._is_urgent_group_msg(msg):
                self._last_group_reply[msg.chat_title] = time.time()
                self._log("reply-cooldown-start", chat=msg.chat_title, cooldown_sec=self.cfg.group_reply_cooldown_sec)
            msg_actions: list[dict[str, Any]] = []
            if self.cfg.processing_mode == "agent":
                    try:
                        result = self.agent.handle_message(msg)
                        if not result.actions:
                            raw = result.raw_response
                            content = raw
                            try:
                                parsed = json.loads(raw)
                                msg_content = parsed.get("message", {}).get("content", "") or parsed.get("content", "")
                                if msg_content:
                                    content = msg_content
                            except Exception:
                                pass
                            if '"actions"' not in content:
                                self._log("agent-bad-response", fingerprint=fp, raw=raw[:300])
                                self._retry_queue.append(msg)
                                continue
                        self.state.add(fp)
                        self._log(
                            "agent-result",
                            fingerprint=fp,
                            action_count=len(result.actions),
                            actions=result.actions,
                            raw_response=result.raw_response[:1200],
                        )
                        msg_actions = result.actions
                    except AgentError as exc:
                        self._log("agent-skip", fingerprint=fp, error=str(exc))
                        self._retry_queue.append(msg)
                        continue
            else:
                self.state.add(fp)
                text = self.reply.generate(msg)
                if text:
                    msg_actions = [{"type": "send_message", "title": msg.chat_title, "message": text, "source_fingerprint": fp}]
                    self._log("template-result", fingerprint=fp, action_count=1)
            final = self._process_web_actions(msg_actions, original_chat_title=msg.chat_title, original_text=msg.text, original_sender=msg.sender)
            if self._is_group(msg.chat_title) and msg.sender:
                at_patterns = tuple(f"@{n}" for n in self.cfg.self_names)
                if msg.text.startswith(at_patterns) or any(p in msg.text for p in at_patterns):
                    for a in final:
                        if a.get("type") == "send_message":
                            a["message"] = f"@{msg.sender} {a['message']}"
            all_final.extend(final)
            send_like = [a for a in all_final if a.get("type") in {"send_message", "send_image"}]
            if len(send_like) >= max(1, self.cfg.max_replies_per_tick):
                self._log("tick-limit", send_like=len(send_like))
                break
        self.state.save()
        return self._execute_actions(all_final)

    def _mark_current_messages_seen(self) -> None:
        self.state.load()
        try:
            messages = self.wx.collect_incoming()
        except WxCliError as exc:
            self._log("startup", status="unable-to-read-existing", error=str(exc))
            return
        for msg in messages:
            self.state.add(msg.fingerprint())
        self.state.save()
        self._log("startup", marked_seen=len(messages))

    def _should_reply(self, msg: WxMessage) -> bool:
        return self._reply_decision(msg)[0]

    def _reply_decision(self, msg: WxMessage) -> tuple[bool, str]:
        if not msg.chat_title or (not msg.text and not msg.attachments):
            return False, "empty-chat-or-message"
        if msg.is_self:
            return False, "self-message"
        if msg.sender and msg.sender in set(self.cfg.self_names):
            return False, "sender-in-self-names"
        if self._own_wxid and msg.sender == self._own_wxid:
            return False, "sender-in-self-wxid"
        if self._own_display and msg.sender == self._own_display:
            return False, "sender-in-self-display"
        if self.cfg.allow_chats and msg.chat_title not in set(self.cfg.allow_chats):
            return False, "not-in-allow-chats"
        if msg.chat_title in set(self.cfg.deny_chats):
            return False, "in-deny-chats"

        if self._is_group(msg.chat_title):
            is_urgent = self._is_urgent_group_msg(msg)
            if is_urgent:
                return True, "selected-urgent"
            if self.cfg.group_reply_cooldown_sec > 0:
                last = self._last_group_reply.get(msg.chat_title, 0.0)
                elapsed = time.time() - last
                if elapsed < self.cfg.group_reply_cooldown_sec:
                    return False, f"group-cooldown-{elapsed:.1f}s"
            return True, "selected-cooldown"
        return True, "selected"

    def _detect_self_info(self) -> tuple[str, str]:
        import json
        from pathlib import Path
        wxid = ""
        display = ""
        try:
            wxcli_cfg = Path.home() / ".wx-cli" / "config.json"
            if wxcli_cfg.is_file():
                data = json.loads(wxcli_cfg.read_text(encoding="utf-8"))
                db_dir = str(data.get("db_dir", ""))
                if "xwechat_files" in db_dir:
                    parts = db_dir.split("xwechat_files/")
                    if len(parts) > 1:
                        user_dir = parts[1].split("/")[0]
                        suffix_idx = user_dir.rfind("_")
                        if suffix_idx > 0 and len(user_dir) - suffix_idx - 1 == 4:
                            wxid = user_dir[:suffix_idx]
                        else:
                            wxid = user_dir
        except Exception:
            pass
        if wxid:
            try:
                import subprocess
                BIN = self.cfg.wx_binary or "wx"
                proc = subprocess.run([BIN, "sessions", "--json"], capture_output=True, text=True, timeout=10)
                sessions = json.loads(proc.stdout or "[]")
                if isinstance(sessions, list):
                    for s in sessions:
                        if str(s.get("chat_type", "") or "") == "group":
                            title = s.get("chat", "")
                            if title:
                                try:
                                    m_proc = subprocess.run([BIN, "members", title, "--json"],
                                                            capture_output=True, text=True, timeout=10)
                                    members = json.loads(m_proc.stdout or "[]")
                                    if isinstance(members, list):
                                        for m in members:
                                            if str(m.get("username", "") or "") == wxid:
                                                d = str(m.get("display", "") or "").strip()
                                                if d:
                                                    display = d
                                                    break
                                except Exception:
                                    continue
                            if display:
                                break
            except Exception:
                pass
        return wxid, display

    def _is_group(self, title: str) -> bool:
        return any(title.startswith(prefix) for prefix in self.cfg.group_title_prefixes)

    def _is_urgent_group_msg(self, msg: WxMessage) -> bool:
        haystack = f"{msg.sender} {msg.text}"
        if any(keyword in haystack for keyword in self.cfg.group_reply_keywords):
            return True
        return "@" in msg.text

    def _heartbeat(self) -> None:
        self._log("heartbeat-tick")
        try:
            result = self.agent.heartbeat()
            self._log(
                "heartbeat-result",
                action_count=len(result.actions),
                actions=result.actions,
                raw_response=result.raw_response[:600],
            )
            web_actions = self._process_web_actions(result.actions)
            self._execute_actions(web_actions)
        except AgentError as exc:
            self._log("heartbeat-error", error=str(exc))

    def _process_web_actions(self, actions: list[dict[str, Any]], original_chat_title: str = "", original_text: str = "", original_sender: str = "") -> list[dict[str, Any]]:
        max_rounds = 20
        current = list(actions)
        tool_trace: list[tuple[str, str]] = []
        original_title = ""
        for a in current:
            t = str(a.get("title", "") or a.get("chat_title", "")).strip()
            if t:
                original_title = t
                break
        if not original_title:
            original_title = original_chat_title or ""
        self._log("web-rounds-start", action_count=len(current), original_title=original_title)
        for _round in range(max_rounds):
            web_results: list[tuple[str, str]] = []
            kept: list[dict[str, Any]] = []
            for action in current:
                kind = str(action.get("type") or "").strip()
                use_proxy = action.get("proxy")
                if use_proxy is None:
                    use_proxy = self.cfg.proxy.enabled
                else:
                    use_proxy = bool(use_proxy)
                if kind == "fetch_url":
                    url = str(action.get("url", ""))
                    if url:
                        raw_result = self._fetch_url(url, use_proxy)
                        self._log("web-fetch", round=_round + 1, method="curl", url=url, proxy=use_proxy, result_len=len(raw_result), result_preview=raw_result[:200])
                        web_results.append((f"[curl] {url}", raw_result[:4000]))
                elif kind == "search_web":
                    query = str(action.get("query", ""))
                    if query:
                        raw_result = self._search_web(query, use_proxy)
                        self._log("web-fetch", round=_round + 1, method="search", query=query, proxy=use_proxy, result_len=len(raw_result), result_preview=raw_result[:200])
                        web_results.append((f"[search] {query}", raw_result[:4000]))
                elif kind == "search_web_brave":
                    query = str(action.get("query", ""))
                    if query:
                        raw_result = self._search_web_brave(query, use_proxy)
                        self._log("web-fetch", round=_round + 1, method="brave", query=query, proxy=use_proxy, result_len=len(raw_result), result_preview=raw_result[:200])
                        web_results.append((f"[brave] {query}", raw_result[:4000]))
                elif kind == "search_web_volc":
                    query = str(action.get("query", ""))
                    if query:
                        raw_result = self._volc_web_search(query)
                        self._log("web-fetch", round=_round + 1, method="volc_search", query=query, result_len=len(raw_result), result_preview=raw_result[:200])
                        web_results.append((f"[volc_search] {query}", raw_result[:4000]))
                elif kind == "browse_url":
                    url = str(action.get("url", ""))
                    if url:
                        raw_result = self._browse_url(url, use_proxy)
                        self._log("web-fetch", round=_round + 1, method="playwright", url=url, proxy=use_proxy, result_len=len(raw_result), result_preview=raw_result[:200])
                        web_results.append((f"[browse] {url}", raw_result[:4000]))
                elif kind == "read_file":
                    path = str(action.get("path", ""))
                    if path:
                        raw_result = self._read_local_file(path)
                        self._log("web-fetch", round=_round + 1, method="read_file", path=path, result_len=len(raw_result))
                        web_results.append((f"[read] {path}", raw_result[:4000]))
                elif kind == "list_files":
                    pattern = str(action.get("pattern", "")) or "*"
                    raw_result = self._list_local_files(pattern)
                    self._log("web-fetch", round=_round + 1, method="list_files", pattern=pattern, result_len=len(raw_result))
                    web_results.append((f"[list] {pattern}", raw_result[:4000]))
                elif kind == "read_chat_history":
                    chat_title = str(action.get("chat_title", "") or action.get("title", "") or original_title).strip()
                    try:
                        limit = int(action.get("limit", 50))
                    except (TypeError, ValueError):
                        limit = 50
                    limit = max(1, min(100, limit))
                    if chat_title:
                        raw_result = self.agent._get_history(chat_title, limit=limit)
                        if not raw_result.strip():
                            raw_result = f"No chat history found for {chat_title}."
                        self._log("web-fetch", round=_round + 1, method="read_chat_history", chat_title=chat_title, limit=limit, result_len=len(raw_result))
                        web_results.append((f"[chat_history] {chat_title} limit={limit}", raw_result[:6000]))
                elif kind == "read_impression":
                    name = re.sub(r"_[0-9a-f]{8}$", "", str(action.get("name", "") or "").strip())
                    if name:
                        raw_result = self.agent.people.read(name)
                        if not raw_result.strip():
                            raw_result = f"No stored impression found for {name}."
                        self._log("web-fetch", round=_round + 1, method="read_impression", name=name, result_len=len(raw_result))
                        web_results.append((f"[impression] {name}", raw_result[:4000]))
                else:
                    kept.append(action)
            if not web_results:
                final = self._finalize_if_needed(kept, original_title, original_text, original_sender, tool_trace)
                self._log("web-rounds-end", total_rounds=_round, final_action_count=len(final))
                return final
            tool_trace.extend(web_results)
            combined = "\n\n".join(f"{label}\n{content}" for label, content in web_results)
            self._log("web-feed-prompt", round=_round + 1, prompt_len=len(combined))
            origin_hint = ""
            if original_text:
                origin_hint = f"\nOriginal sender: {original_sender}\nOriginal message: {original_text}"
            prompt_msg = WxMessage(
                chat_title=original_title,
                text=f"Tool action results (round {_round + 1}):\n{combined}\n\n"
                     f"Original chat: {original_title}\n"
                     f"When sending messages, use this exact chat title.\n"
                     f"Use read_chat_history to inspect recent WeChat context; do not guess local message file paths. "
                     f"If a web result shows failure or no data, try a DIFFERENT search method (search_web / search_web_volc / search_web_brave / fetch_url / browse_url / read_chat_history) before giving up. "
                     f"Based on these results, use the registered tools to take further actions, update memory/impressions, or reply."
                     f"{origin_hint}",
                sender="system",
            )
            try:
                result = self.agent.handle_message(prompt_msg)
                self._log("web-feed-result", round=_round + 1, action_count=len(result.actions), actions=result.actions, raw_preview=result.raw_response[:300])
                current = result.actions + kept
            except AgentError as exc:
                self._log("web-result-error", round=_round + 1, error=str(exc))
                final = self._finalize_if_needed(kept, original_title, original_text, original_sender, tool_trace)
                self._log("web-rounds-end", total_rounds=_round + 1, final_action_count=len(final))
                return final
        final = self._finalize_if_needed(current, original_title, original_text, original_sender, tool_trace)
        self._log("web-rounds-end", total_rounds=max_rounds, final_action_count=len(final))
        return final

    def _finalize_if_needed(
        self,
        actions: list[dict[str, Any]],
        original_title: str,
        original_text: str,
        original_sender: str,
        tool_trace: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        if any(a.get("type") in {"send_message", "send_image", "noop"} for a in actions):
            return actions
        if not self._requires_final_response(original_title, original_text, original_sender):
            return actions
        trace_text = "\n\n".join(f"{label}\n{content}" for label, content in tool_trace)[-12000:]
        self._log("web-finalize-start", original_title=original_title, trace_len=len(trace_text))
        try:
            result = self.agent.finalize_response(
                chat_title=original_title,
                original_sender=original_sender,
                original_text=original_text,
                tool_trace=trace_text,
            )
            self._log("web-finalize-result", action_count=len(result.actions), actions=result.actions, raw_preview=result.raw_response[:300])
            return result.actions
        except AgentError as exc:
            self._log("web-finalize-error", error=str(exc))
            return [{"type": "send_message", "title": original_title, "message": "我这边没拿到原链接或视频内容，刚刚查漏了。"}]

    def _requires_final_response(self, original_title: str, original_text: str, original_sender: str) -> bool:
        if not original_title:
            return False
        if not self._is_group(original_title):
            return True
        haystack = f"{original_sender} {original_text}"
        if any(keyword in haystack for keyword in self.cfg.group_reply_keywords):
            return True
        return "@" in original_text

    def _proxy_args(self, use_proxy: bool) -> tuple[list[str], dict[str, str]]:
        p = self.cfg.proxy
        if use_proxy and p.enabled and p.url:
            return ["--proxy", p.url, "--noproxy", p.no_proxy] if p.no_proxy else ["--proxy", p.url], {}
        return [], {}

    def _strip_html(self, text: str) -> str:
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:6000]

    def _fetch_url(self, url: str, use_proxy: bool = True) -> str:
        import subprocess
        proxy_args, _ = self._proxy_args(use_proxy)
        cmd = ["curl", "-sL", "--max-time", "15", *proxy_args, url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                return self._strip_html(proc.stdout)
            return f"curl error (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
        except Exception as e:
            return f"fetch failed: {e}"

    def _search_web(self, query: str, use_proxy: bool = True) -> str:
        import json, subprocess
        api_key = self.cfg.tavily_api_key or os.environ.get("TAVILY_API_KEY") or ""
        if not api_key:
            return "No Tavily API key configured"
        url = "https://api.tavily.com/search"
        payload = {"api_key": api_key, "query": query, "max_results": 5, "include_answer": True}
        try:
            proxy_args, _ = self._proxy_args(use_proxy)
            cmd = ["curl", "-sL", "--max-time", "15", *proxy_args, "-X", "POST", url, "-H", "Content-Type: application/json", "-d", json.dumps(payload)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                answer = data.get("answer", "")
                results = data.get("results", [])
                parts = [f"Answer: {answer}"] if answer else []
                for r in results[:5]:
                    parts.append(f"- {r.get('title', '')}: {r.get('content', '')[:200]} ({r.get('url', '')})")
                return "\n".join(parts) if parts else "No search results"
            return f"tavily error: {proc.stderr[:300]}"
        except Exception as e:
            return f"tavily failed: {e}"

    def _search_web_brave(self, query: str, use_proxy: bool = True) -> str:
        import json, subprocess
        brave_key = self.cfg.brave_search_api_key or os.environ.get("BRAVE_SEARCH_API_KEY") or ""
        if not brave_key:
            return "No Brave API key configured"
        try:
            url = f"https://api.search.brave.com/res/v1/web/search?q={__import__('urllib').parse.quote(query)}&count=5"
            proxy_args, _ = self._proxy_args(use_proxy)
            cmd = ["curl", "-sL", "--max-time", "15", *proxy_args, url, "-H", f"X-Subscription-Token: {brave_key}"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                results = data.get("web", {}).get("results", [])
                parts = []
                for r in results[:5]:
                    parts.append(f"- {r.get('title', '')}: {r.get('description', '')[:200]} ({r.get('url', '')})")
                return "\n".join(parts) if parts else "No search results"
            return f"brave error: {proc.stderr[:300]}"
        except Exception as e:
            return f"brave failed: {e}"

    def _volc_web_search(self, query: str) -> str:
        import json, ssl, urllib.request
        clean = query.strip()[:120]
        if not clean:
            return ""
        api_key = self.cfg.volc_ark_api_key or os.environ.get(self.cfg.volc_ark_api_key_env or "ARK_API_KEY", "")
        if not api_key:
            return "Volcengine Ark API key not configured"
        model = str(self.cfg.volc_ark_model or "").strip()
        if not model:
            return "Volcengine Ark model not configured"
        limit = max(1, min(20, int(self.cfg.volc_ark_limit)))
        max_keyword = max(1, min(50, int(self.cfg.volc_ark_max_keyword)))
        timeout_sec = max(1.0, float(self.cfg.volc_ark_timeout_sec))
        base = (self.cfg.volc_ark_base_url or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        url = base if base.endswith("/responses") else base + "/responses"
        payload = {
            "model": model,
            "max_tool_calls": 1,
            "tools": [{"type": "web_search", "max_keyword": max_keyword, "limit": limit}],
            "input": [{"role": "user", "content": [{"type": "input_text", "text": clean}]}],
        }
        req = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return f"volc_ark http error: {exc.code} {detail[:300]}"
        except Exception as e:
            return f"volc_ark error: {e}"
        data = json.loads(raw)
        output = data.get("output") if isinstance(data.get("output"), list) else []
        answer = ""
        rows: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for item in output:
            if not isinstance(item, dict) or str(item.get("type", "")).strip() != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if not answer:
                    answer = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()[:240]
                annotations = block.get("annotations")
                if not isinstance(annotations, list):
                    continue
                for ann in annotations:
                    if not isinstance(ann, dict):
                        continue
                    u = re.sub(r"\s+", " ", str(ann.get("url", ann.get("source_url", "")))).strip()[:160]
                    if not u or u in seen_urls:
                        continue
                    seen_urls.add(u)
                    t = re.sub(r"\s+", " ", str(ann.get("title", ann.get("source_title", "")))).strip()[:90]
                    rows.append((t, u))
        if not answer and not rows:
            return "volc_ark search returned no results"
        lines = []
        if answer:
            lines.append(f"摘要: {answer}")
        for title, link in rows[:limit]:
            row = f"{title} | {link}" if title else link
            lines.append(row)
        return "\n".join(lines)[:1200]

    def _browse_url(self, url: str, use_proxy: bool = True) -> str:
        from playwright.sync_api import sync_playwright
        proxy_settings = None
        if use_proxy:
            p = self.cfg.proxy
            if p.enabled and p.url:
                proxy_settings = {"server": p.url}
                if p.no_proxy:
                    proxy_settings["bypass"] = p.no_proxy
        try:
            with sync_playwright() as p:
                launch_kwargs = {"headless": True}
                if proxy_settings:
                    launch_kwargs["proxy"] = proxy_settings
                browser = p.chromium.launch(**launch_kwargs)
                page = browser.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                text = page.evaluate("() => document.body.innerText") or ""
                browser.close()
                return self._strip_html(text.strip())[:10000] if text.strip() else "page returned empty text"
        except Exception as e:
            return f"browse failed: {e}"

    def _read_local_file(self, path: str) -> str:
        import os
        allowed = os.path.abspath(".")
        full = os.path.abspath(os.path.join(allowed, path))
        if not full.startswith(allowed):
            return f"access denied: path outside project root"
        if not os.path.isfile(full):
            return f"file not found: {path}"
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read()[:10000]
        except Exception as e:
            return f"read error: {e}"

    def _list_local_files(self, pattern: str) -> str:
        import glob, os
        allowed = os.path.abspath(".")
        safe = pattern.lstrip("/").lstrip(".")
        full_pattern = os.path.join(allowed, safe)
        if not full_pattern.startswith(allowed):
            return "access denied"
        try:
            files = sorted(glob.glob(full_pattern, recursive=True))[:30]
            return "\n".join(f for f in files if os.path.isfile(f)) or "no files match"
        except Exception as e:
            return f"list error: {e}"

    def _execute_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        executed: list[dict[str, Any]] = []
        with self.sender.send_lock():
            for idx, action in enumerate(actions):
                kind = str(action.get("type") or "").strip().lower()
                did_send_like = False
                try:
                    if kind == "noop":
                        self._log("action", step=idx + 1, type="noop")
                        executed.append(action)
                        print(f"[noop] 模型选择不回复，跳过")
                        continue
                    if kind == "focus_chat":
                        title = str(action.get("title") or "").strip()
                        if not title:
                            continue
                        if self.cfg.dry_run:
                            self._log("dry-run", action="focus_chat", title=title)
                        else:
                            self.sender.focus_chat(title)
                            self._log("focused", title=title)
                        executed.append(action)
                        continue
                    if kind == "send_message":
                        title = str(action.get("title") or "").strip()
                        message = str(action.get("message") or "").strip()
                        if self.sender.send_message(title, message):
                            executed.append(action)
                            did_send_like = True
                        continue
                    if kind == "send_image":
                        title = str(action.get("title") or "").strip()
                        image_path = str(action.get("image_path") or "").strip()
                        if self.sender.send_image(title, image_path):
                            executed.append(action)
                            did_send_like = True
                        continue
                    self._log("action-skip", reason="unsupported", action=kind)
                except SendError as exc:
                    self._log("send-skip", action=kind, error=str(exc))
                finally:
                    if did_send_like and self.cfg.send_action_interval_sec > 0:
                        time.sleep(self.cfg.send_action_interval_sec)
        return executed

    @staticmethod
    def _log(event: str, **fields: Any) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            **fields,
        }
        print(json.dumps(record, ensure_ascii=False, default=str), flush=True)
