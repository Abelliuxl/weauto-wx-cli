from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


def _norm(text: str) -> str:
    raw = re.sub(r"\s+", "", text or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)


def _looks_like_noise_name(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return True
    if lower in {
        "unknown",
        "unknown user",
        "sender",
        "member",
        "someone",
        "other",
        "user",
        "system",
        "self",
        "assistant",
    }:
        return True
    return bool(re.fullmatch(r"[\d_ -]+", lower or ""))


def _looks_like_placeholder_person(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean or _looks_like_noise_name(clean):
        return True
    return clean in {
        "群成员",
        "其他群成员",
        "其他成员",
        "群聊成员",
        "其他人",
        "大家",
        "有人",
        "未知人物",
        "未知",
        "系统",
        "系统提示",
        "用户",
        "未知用户",
        "对方",
    }


def _normalize_name(text: str) -> str:
    clean = str(text or "")[:32]
    clean = clean.strip(" []【】()（）,，。.!！?？:：;；\"'“”‘’")
    clean = re.sub(r"^(real|test|tmp)[-_ ]*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"(也行|都行|就行|可以|呀|啊|呢|吧)$", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) < 2 or len(clean) > 24:
        return ""
    if re.search(r"[，,。.!！?？:：;；|/\\\[\]{}<>]+", clean):
        return ""
    if "、" in clean or _looks_like_noise_name(clean):
        return ""
    return clean


def _display_name(text: str) -> str:
    clean = str(text or "")[:32]
    clean = clean.strip(" []【】()（）,，。.!！?？:：;；\"'“”‘’")
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) < 2 or len(clean) > 24:
        return ""
    if re.search(r"[，,。.!！?？:：;；|/\\\[\]{}<>]+", clean):
        return ""
    if "、" in clean or _looks_like_placeholder_person(clean):
        return ""
    return clean


def _slug(text: str) -> str:
    base = _norm(text) or "person"
    return f"{base[:24]}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"


@dataclass
class PersonResolution:
    observed_name: str
    canonical_name: str
    aliases: list[str]
    aliases_path: str
    memory_file: str = ""

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "observed_name": self.observed_name,
            "canonical_name": self.canonical_name,
            "aliases": self.aliases,
        }
        if self.aliases_path:
            payload["aliases_path"] = self.aliases_path
        return payload


class PersonAliasResolver:
    def __init__(self, aliases_path: str = "") -> None:
        self.aliases_path = str(aliases_path or "").strip()
        self._cache_key = ""
        self._mapping: dict[str, str] = {}
        self._patterns: list[tuple[str, str, str]] = []
        self._reverse_aliases: dict[str, list[str]] = {}

    def _maybe_reload(self) -> None:
        path = Path(self.aliases_path).expanduser()
        if not self.aliases_path or not path.exists():
            self._cache_key = ""
            self._mapping = {}
            self._patterns = []
            self._reverse_aliases = {}
            return
        try:
            st = path.stat()
            key = f"{path.resolve()}:{int(st.st_mtime_ns)}:{st.st_size}"
        except Exception:
            key = str(path.resolve())
        if key == self._cache_key:
            return

        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            raw = ""
        mapping: dict[str, str] = {}
        patterns: list[tuple[str, str, str]] = []
        reverse_aliases: dict[str, list[str]] = {}
        seen_patterns: set[str] = set()
        for line in raw.splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            if clean.startswith("- "):
                clean = clean[2:].strip()
            sep = ""
            for token in ("->", "=>", "="):
                if token in clean:
                    sep = token
                    break
            if not sep:
                continue
            left, right = [part.strip() for part in clean.split(sep, 1)]
            canonical = _normalize_name(left)
            if not canonical or _looks_like_placeholder_person(canonical):
                continue
            reverse_aliases.setdefault(canonical, [])
            for alias in [x.strip() for x in re.split(r"[，,、；;|]", right) if x.strip()]:
                has_star = "*" in alias
                starts_star = alias.startswith("*")
                ends_star = alias.endswith("*")
                if has_star and (starts_star or ends_star):
                    core = _normalize_name(alias.strip("*"))
                    token_norm = _norm(core)
                    if not core or not token_norm:
                        continue
                    mode = "contains" if (starts_star and ends_star) else ("suffix" if starts_star else "prefix")
                    pattern_key = f"{mode}|{token_norm}|{_norm(canonical)}"
                    if pattern_key in seen_patterns:
                        continue
                    seen_patterns.add(pattern_key)
                    patterns.append((mode, token_norm, canonical))
                    reverse_aliases[canonical].append(alias)
                    continue

                clean_alias = _normalize_name(alias)
                if not clean_alias or _looks_like_placeholder_person(clean_alias):
                    continue
                if _norm(clean_alias) == _norm(canonical):
                    continue
                mapping[_norm(clean_alias)] = canonical
                if clean_alias not in reverse_aliases[canonical]:
                    reverse_aliases[canonical].append(clean_alias)
            mapping[_norm(canonical)] = canonical

        self._cache_key = key
        self._mapping = mapping
        self._patterns = patterns
        self._reverse_aliases = reverse_aliases

    def resolve(self, name: str) -> str:
        self._maybe_reload()
        clean = _normalize_name(name)
        if not clean or _looks_like_placeholder_person(clean):
            return ""
        norm_clean = _norm(clean)
        canonical = self._mapping.get(norm_clean, "")
        if not canonical:
            for mode, token, target in self._patterns:
                if mode == "suffix":
                    matched = norm_clean.endswith(token)
                elif mode == "prefix":
                    matched = norm_clean.startswith(token)
                else:
                    matched = token in norm_clean
                if matched:
                    canonical = target
                    break
        return _normalize_name(canonical) or clean

    def aliases_for(self, canonical_name: str) -> list[str]:
        self._maybe_reload()
        canonical = self.resolve(canonical_name)
        if not canonical:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in self._reverse_aliases.get(canonical, []):
            clean = str(item or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
        return out[:12]

    def build_resolution(self, observed_name: str, *, memory_dir: str = "") -> PersonResolution:
        observed = _display_name(observed_name)
        canonical = self.resolve(observed)
        aliases = self.aliases_for(canonical) if canonical else []
        memory_file = ""
        if memory_dir and canonical:
            memory_file = str((Path(memory_dir).expanduser() / f"{_slug(canonical)}.md").resolve())
        aliases_path = ""
        if self.aliases_path:
            aliases_path = str(Path(self.aliases_path).expanduser().resolve())
        return PersonResolution(
            observed_name=observed,
            canonical_name=canonical,
            aliases=aliases,
            aliases_path=aliases_path,
            memory_file=memory_file,
        )

    def iter_alias_entries(self) -> list[tuple[str, str]]:
        self._maybe_reload()
        entries: list[tuple[str, str]] = []
        for canonical, aliases in self._reverse_aliases.items():
            entries.append((canonical, canonical))
            for alias in aliases:
                clean = str(alias or "").strip()
                if clean:
                    entries.append((clean, canonical))
        return entries


class PeopleContextBuilder:
    def __init__(
        self,
        *,
        aliases_path: str,
        memory_dir: str = "",
        max_items: int = 8,
    ) -> None:
        self.resolver = PersonAliasResolver(aliases_path)
        self.memory_dir = memory_dir
        self.max_items = max(1, int(max_items or 8))

    @staticmethod
    def extract_mentions(text: str) -> list[str]:
        mentions: list[str] = []
        seen: set[str] = set()
        for match in re.finditer(r"@([^\s@,，。.!！?？:：;；()\[\]【】]{1,24})", text or ""):
            raw = match.group(1).strip()
            clean = _display_name(raw)
            if clean and clean not in seen:
                seen.add(clean)
                mentions.append(clean)
        return mentions

    def build(self, *, sender: str, text: str) -> dict[str, object]:
        people: dict[str, dict[str, object]] = {}

        def add(observed: str, reason: str) -> dict[str, object] | None:
            resolution = self.resolver.build_resolution(observed, memory_dir=self.memory_dir)
            if not resolution.observed_name or not resolution.canonical_name:
                return None
            key = resolution.canonical_name
            item = people.get(key)
            if item is None:
                item = resolution.to_payload()
                item["matched_by"] = []
                people[key] = item
            matched_by = item.setdefault("matched_by", [])
            if isinstance(matched_by, list) and reason not in matched_by:
                matched_by.append(reason)
            return item

        sender_identity = add(sender, f"sender:{sender}") if sender else None

        mentioned_people: list[dict[str, object]] = []
        for name in self.extract_mentions(text):
            item = add(name, f"mention:{name}")
            if item is not None:
                mentioned_people.append(dict(item))

        norm_text = _norm(text)
        if norm_text:
            for alias, canonical in self.resolver.iter_alias_entries():
                if len(people) >= self.max_items:
                    break
                clean_alias = _display_name(alias)
                if not clean_alias:
                    continue
                alias_norm = _norm(clean_alias)
                if len(alias_norm) < 2 or alias_norm not in norm_text:
                    continue
                add(clean_alias, f"text:{clean_alias}")

        context = list(people.values())[: self.max_items]
        payload: dict[str, object] = {
            "sender_identity": dict(sender_identity) if sender_identity else {},
            "mentioned_people": mentioned_people[: self.max_items],
            "people_context": context,
        }
        return payload
