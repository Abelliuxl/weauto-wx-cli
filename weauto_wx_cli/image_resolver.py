from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES

from .models import Attachment, WxMessage

_IMG_DIR = "data/images"
_WECHAT_CONTAINER = (
    Path.home()
    / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
_WX_CLI_CACHE = Path.home() / ".wx-cli/cache"
_WX_CLI_CONFIG = Path.home() / ".wx-cli/config.json"


class ImageResolveError(RuntimeError):
    pass


class ImageResolver:
    def __init__(self, output_dir: str = _IMG_DIR) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cached_db: dict[str, sqlite3.Connection] = {}
        self._cached_attach_root: str | None = None
        self._cached_uin: int | None = None
        self._cached_wxid: str | None = None
        self._cached_aes_key: bytes | None = None
        self._cached_xor_key: int | None = None

    def resolve(self, msg: WxMessage) -> list[Attachment]:
        if msg.message_type != "image":
            return msg.attachments
        chat_title = msg.chat_title
        if not chat_title:
            return msg.attachments
        username = self._get_username(chat_title)
        if not username:
            return self._clean_attachments(msg.attachments)
        local_id = self._extract_local_id(msg)
        if local_id is None:
            return self._resolve_by_mtime(username, msg)

        chat_hash = self._chat_hash(username)
        db_path = self._find_decrypted_db(chat_hash)
        if db_path is None:
            return self._resolve_by_mtime(username, msg)

        file_md5 = self._query_file_md5(db_path, chat_hash, local_id)
        if not file_md5:
            return self._resolve_by_mtime(username, msg)

        return self._resolve_by_md5(username, chat_hash, file_md5, msg)

    @staticmethod
    def _clean_attachments(
        attachments: list[Attachment],
    ) -> list[Attachment]:
        return [a for a in attachments if a.path or a.url]

    def _get_username(self, chat_title: str) -> str:
        try:
            import json
            import subprocess

            BIN = os.environ.get(
                "WX_CLI_BINARY",
                str(
                    Path.home()
                    / ".npm-global/lib/node_modules/@jackwener/wx-cli/node_modules/@jackwener/wx-cli-darwin-x64/bin/wx"
                ),
            )
            if not os.path.exists(BIN):
                BIN = "wx"
            proc = subprocess.run(
                [BIN, "sessions", "--json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0:
                return ""
            sessions = json.loads(proc.stdout or "[]")
            for s in sessions:
                if s.get("chat") == chat_title:
                    return str(s.get("username", ""))
        except Exception:
            return ""
        return ""

    def _extract_local_id(self, msg: WxMessage) -> int | None:
        raw = msg.raw or {}
        local_id = raw.get("local_id")
        if local_id is not None:
            try:
                return int(local_id)
            except (ValueError, TypeError):
                pass
        text = msg.text or ""
        m = re.search(r"local_id=(\d+)", text)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _chat_hash(username: str) -> str:
        return hashlib.md5(username.encode()).hexdigest()

    def _find_decrypted_db(self, chat_hash: str) -> str | None:
        cache_dir = _WX_CLI_CACHE
        if not cache_dir.is_dir():
            return None
        db_table = f"Msg_{chat_hash}"
        for f in sorted(cache_dir.glob("*.db")):
            try:
                conn = sqlite3.connect(str(f))
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (db_table,),
                )
                if cur.fetchone():
                    self._cached_db[chat_hash] = conn
                    return str(f)
                conn.close()
            except Exception:
                continue
        return None

    def _query_file_md5(
        self, db_path: str, chat_hash: str, local_id: int
    ) -> str | None:
        try:
            conn = self._cached_db.get(chat_hash)
            if conn is None:
                conn = sqlite3.connect(db_path)
                self._cached_db[chat_hash] = conn
            cur = conn.execute(
                f"SELECT packed_info_data, create_time, local_type "
                f"FROM Msg_{chat_hash} WHERE local_id=?",
                (local_id,),
            )
            row = cur.fetchone()
            if row is None:
                cur = conn.execute(
                    f"SELECT packed_info_data, create_time, local_type "
                    f"FROM Msg_{chat_hash} WHERE local_id=? "
                    f"ORDER BY create_time DESC LIMIT 1",
                    (local_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            packed, create_time, local_type = row
            if not packed:
                return None
            return self._extract_md5_from_packed(packed)
        except Exception:
            return None

    @staticmethod
    def _extract_md5_from_packed(packed: bytes) -> str | None:
        for m in re.finditer(rb"[\da-f]{32}", packed, re.I):
            candidate = m.group().decode("ascii").lower()
            if all(c in "0123456789abcdef" for c in candidate):
                return candidate
        marker = b"\x22\x20"
        idx = packed.find(marker)
        if idx >= 0 and idx + 2 + 32 <= len(packed):
            candidate = packed[idx + 2 : idx + 2 + 32].decode("ascii", errors="replace")
            if all(c in "0123456789abcdef" for c in candidate.lower()):
                return candidate.lower()
        return None

    def _resolve_by_md5(
        self, username: str, chat_hash: str, file_md5: str, msg: WxMessage
    ) -> list[Attachment]:
        attach_root = self._find_attach_root()
        if not attach_root:
            return msg.attachments

        chat_attach_dir = attach_root / chat_hash
        if not chat_attach_dir.is_dir():
            return msg.attachments

        create_time = self._get_create_time(msg)
        candidates_ym = self._three_month_candidates(create_time)
        for ym in candidates_ym:
            img_dir = chat_attach_dir / ym / "Img"
            dat_path = self._pick_best_dat(img_dir, file_md5)
            if dat_path:
                return self._decrypt_and_return(dat_path, msg)

        for month_dir in sorted(chat_attach_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            img_dir = month_dir / "Img"
            dat_path = self._pick_best_dat(img_dir, file_md5)
            if dat_path:
                return self._decrypt_and_return(dat_path, msg)

        return msg.attachments

    def _resolve_by_mtime(
        self, username: str, msg: WxMessage
    ) -> list[Attachment]:
        create_time = self._get_create_time(msg)
        chat_hash = self._chat_hash(username)
        attach_root = self._find_attach_root()
        if not attach_root:
            return msg.attachments
        chat_attach_dir = attach_root / chat_hash
        if not chat_attach_dir.is_dir():
            return msg.attachments

        candidates_ym = self._three_month_candidates(create_time)
        for ym in candidates_ym:
            img_dir = chat_attach_dir / ym / "Img"
            if not img_dir.is_dir():
                continue
            dat_path = self._find_by_mtime(img_dir, create_time)
            if dat_path:
                return self._decrypt_and_return(dat_path, msg)
        return msg.attachments

    @staticmethod
    def _find_by_mtime(img_dir: Path, create_time: int) -> Path | None:
        margin = 300
        best: tuple[Path, float] | None = None
        for f in img_dir.iterdir():
            if not f.name.endswith(".dat"):
                continue
            try:
                ft = f.stat().st_mtime
            except OSError:
                continue
            diff = abs(ft - create_time)
            if diff <= margin:
                if best is None or diff < best[1]:
                    best = (f, diff)
        return best[0] if best else None

    def _find_attach_root(self) -> Path | None:
        if self._cached_attach_root:
            return Path(self._cached_attach_root)
        user = self._get_daemon_user_dir()
        if user:
            candidate = user / "msg" / "attach"
            if candidate.is_dir():
                self._cached_attach_root = str(candidate)
                return candidate
        for user_dir in _WECHAT_CONTAINER.iterdir():
            candidate = user_dir / "msg" / "attach"
            if candidate.is_dir():
                self._cached_attach_root = str(candidate)
                return candidate
        return None

    @staticmethod
    def _get_daemon_user_dir() -> Path | None:
        cfg = _WX_CLI_CONFIG
        if not cfg.is_file():
            return None
        try:
            import json
            data = json.loads(cfg.read_text(encoding="utf-8"))
            db_dir = Path(str(data.get("db_dir", "")))
            if db_dir.is_dir():
                return db_dir.parent
        except Exception:
            return None
        return None

    @staticmethod
    def _pick_best_dat(img_dir: Path, file_md5: str) -> Path | None:
        if not img_dir.is_dir():
            return None
        full = img_dir / f"{file_md5}.dat"
        if full.is_file():
            return full
        hd = img_dir / f"{file_md5}_h.dat"
        if hd.is_file():
            return hd
        thumb = img_dir / f"{file_md5}_t.dat"
        if thumb.is_file():
            return thumb
        return None

    def _get_create_time(self, msg: WxMessage) -> int:
        ts = msg.timestamp
        if ts:
            try:
                return int(ts)
            except (ValueError, TypeError):
                pass
        raw = msg.raw or {}
        for k in ("timestamp", "createTime", "time"):
            v = raw.get(k)
            if v is not None:
                try:
                    return int(v)
                except (ValueError, TypeError):
                    pass

        raw_ts = raw.get("timestamp", raw.get("create_time", 0))
        if raw_ts:
            try:
                return int(raw_ts)
            except (ValueError, TypeError):
                pass
        return int(time.time())

    @staticmethod
    def _three_month_candidates(unix_ts: int) -> list[str]:
        from datetime import datetime, timedelta

        dt = datetime.fromtimestamp(unix_ts)
        candidates = [dt - timedelta(days=31), dt, dt + timedelta(days=31)]
        return [f"{d.year:04d}-{d.month:02d}" for d in candidates]

    def _derive_key(self) -> tuple[bytes, int]:
        if self._cached_aes_key is not None and self._cached_xor_key is not None:
            return self._cached_aes_key, self._cached_xor_key

        uin, wxid = self._find_uin_and_wxid()
        if uin is None or not wxid:
            raise ImageResolveError("cannot derive image key: uin/wxid not found")

        xor_key = uin & 0xFF
        digest = hashlib.md5(f"{uin}{wxid}".encode()).hexdigest()
        aes_key = digest[:16].encode()

        self._cached_uin = uin
        self._cached_wxid = wxid
        self._cached_aes_key = aes_key
        self._cached_xor_key = xor_key
        return aes_key, xor_key

    def _find_uin_and_wxid(self) -> tuple[int | None, str]:
        kvcomm_dir = self._find_kvcomm_dir()
        if kvcomm_dir:
            uin = self._extract_uin_from_kvcomm(kvcomm_dir)
            if uin is not None:
                wxid = self._get_wxid_from_dbdir()
                if wxid:
                    return uin, self._normalize_wxid(wxid)
        return None, ""

    def _find_kvcomm_dir(self) -> Path | None:
        candidates = [
            _WECHAT_CONTAINER.parent
            / "app_data/net/kvcomm",
            Path.home()
            / "Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/net/kvcomm",
        ]
        for c in candidates:
            if c.is_dir():
                return c
        return None

    @staticmethod
    def _extract_uin_from_kvcomm(kvcomm_dir: Path) -> int | None:
        candidates: list[tuple[float, int]] = []
        for f in kvcomm_dir.iterdir():
            name = f.name
            if name.startswith("key_") and name.endswith(".statistic"):
                rest = name[len("key_"):]
                uin_str = rest.split("_")[0]
                try:
                    uin = int(uin_str)
                except (ValueError, IndexError):
                    continue
                if uin == 0:
                    continue
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    mtime = 0.0
                candidates.append((mtime, uin))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

        for f in kvcomm_dir.iterdir():
            name = f.name
            if name.startswith("key_") and name.endswith(".statistic"):
                rest = name[len("key_"):]
                uin_str = rest.split("_")[0]
                try:
                    return int(uin_str)
                except (ValueError, IndexError):
                    continue
        return None

    @staticmethod
    def _normalize_wxid(raw: str) -> str:
        idx = raw.rfind("_")
        if idx >= 0 and len(raw) - idx - 1 == 4:
            suffix = raw[idx + 1:]
            if all(c in "0123456789abcdefABCDEF" for c in suffix):
                return raw[:idx]
        return raw

    def _get_wxid_from_dbdir(self) -> str:
        user = self._get_daemon_user_dir()
        if user:
            return user.name
        for user_dir in _WECHAT_CONTAINER.iterdir():
            db_storage = user_dir / "db_storage"
            if db_storage.is_dir():
                return user_dir.name
        return ""

    def _decrypt_and_return(
        self, dat_path: Path, msg: WxMessage
    ) -> list[Attachment]:
        try:
            aes_key, xor_key = self._derive_key()
        except ImageResolveError:
            return self._clean_attachments(msg.attachments)

        img_dir = dat_path.parent
        file_md5 = dat_path.stem
        if file_md5.endswith("_h") or file_md5.endswith("_t"):
            file_md5 = file_md5[:-2]

        variants = [
            img_dir / f"{file_md5}_h.dat",
            img_dir / f"{file_md5}.dat",
            img_dir / f"{file_md5}_t.dat",
        ]
        preferred = ["jpg", "png", "webp"]
        best_out: tuple[bytes, str, str] | None = None

        for variant in variants:
            if not variant.is_file():
                continue
            try:
                data = variant.read_bytes()
            except OSError:
                continue
            decrypted = self._decrypt_v2(data, aes_key, xor_key)
            if decrypted is None:
                decrypted = self._decrypt_legacy_xor(data)
            if decrypted is None:
                decrypted = self._decrypt_v1_aes(data, aes_key, xor_key)
            if decrypted is None:
                continue
            fmt = self._detect_format(decrypted)
            if fmt == "bin":
                continue
            if fmt == "hevc":
                out_name = f"{file_md5}_png.png"
                out_path = self.output_dir / out_name
                if self._write_hevc_as_png(decrypted, out_path):
                    return [
                        Attachment(type="image", path=str(out_path.resolve()))
                    ]
                if best_out is None:
                    best_out = (decrypted, fmt, file_md5)
                continue
            if fmt in preferred:
                out_name = f"{file_md5}_{fmt}.{fmt}"
                out_path = self.output_dir / out_name
                out_path.write_bytes(decrypted)
                return [
                    Attachment(type="image", path=str(out_path.resolve()))
                ]
            if best_out is None:
                best_out = (decrypted, fmt, file_md5)

        if best_out is not None:
            decrypted, fmt, file_md5 = best_out
            out_name = f"{file_md5}_{fmt}.{fmt}"
            out_path = self.output_dir / out_name
            out_path.write_bytes(decrypted)
            return [Attachment(type="image", path=str(out_path.resolve()))]

        return self._clean_attachments(msg.attachments)

    @staticmethod
    def _write_hevc_as_png(data: bytes, output_path: Path) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False

        try:
            with tempfile.TemporaryDirectory() as tmp:
                in_path = Path(tmp) / "input.hevc"
                out_path = Path(tmp) / "output.png"
                in_path.write_bytes(data)
                proc = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "hevc",
                        "-i",
                        str(in_path),
                        "-frames:v",
                        "1",
                        str(out_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode != 0 or not out_path.is_file():
                    return False
                shutil.copyfile(out_path, output_path)
                return True
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _decrypt_v2(data: bytes, aes_key: bytes, xor_key: int) -> bytes | None:
        V2_MAGIC = b"\x07\x08V2\x08\x07"
        V1_MAGIC = b"\x07\x08V1\x08\x07"
        if len(data) < 15:
            return None
        if data[:6] != V2_MAGIC and data[:6] != V1_MAGIC:
            return None

        aes_size = struct.unpack("<I", data[6:10])[0]
        xor_sz = struct.unpack("<I", data[10:14])[0]
        aligned = aes_size + (16 - aes_size % 16)
        aes_end = 15 + aligned
        if aes_end > len(data):
            return None

        header_size = 15
        raw_end = len(data) - xor_sz
        if aes_end > raw_end:
            return None

        try:
            cipher = AES.new(aes_key, AES.MODE_ECB)
            dec_aes = cipher.decrypt(data[header_size:aes_end])
            pad_len = dec_aes[-1]
            if 1 <= pad_len <= 16:
                dec_aes = dec_aes[:-pad_len]
        except Exception:
            return None

        raw_data = data[aes_end:raw_end]
        xor_data = bytes(b ^ xor_key for b in data[raw_end:])
        return dec_aes + raw_data + xor_data

    @staticmethod
    def _decrypt_v1_aes(
        data: bytes, aes_key: bytes, xor_key: int
    ) -> bytes | None:
        V1_MAGIC = b"\x07\x08V1\x08\x07"
        if len(data) < 15 or data[:6] != V1_MAGIC:
            return None
        fixed_key = b"cfcd208495d565ef"
        return ImageResolver._decrypt_v2(data, fixed_key, xor_key)

    @staticmethod
    def _decrypt_legacy_xor(data: bytes) -> bytes | None:
        if len(data) < 4:
            return None

        png = bytes([0x89, 0x50, 0x4E, 0x47])
        gif = b"GIF8"
        tif = bytes([0x49, 0x49, 0x2A, 0x00])
        riff = b"RIFF"
        jpg = bytes([0xFF, 0xD8, 0xFF])
        bmp = b"BM"

        for magic in [png, gif, tif, riff, jpg]:
            if len(data) < len(magic):
                continue
            key = data[0] ^ magic[0]
            if all(data[i] ^ key == magic[i] for i in range(1, len(magic))):
                return bytes(b ^ key for b in data)

        if len(data) >= 14:
            key = data[0] ^ bmp[0]
            if data[1] ^ key == bmp[1]:
                dec = bytes(data[i] ^ key for i in range(14))
                from struct import unpack_from

                bmp_size = unpack_from("<I", dec, 2)[0]
                bmp_offset = unpack_from("<I", dec, 10)[0]
                if abs(bmp_size - len(data)) < 1024 and 14 <= bmp_offset <= 1078:
                    return bytes(b ^ key for b in data)
        return None

    @staticmethod
    def _detect_format(data: bytes) -> str:
        if len(data) >= 4 and data[:4] == b"wxgf":
            return "hevc"
        if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
            return "jpg"
        if len(data) >= 4 and data[:4] == b"\x89PNG":
            return "png"
        if len(data) >= 3 and data[:3] == b"GIF":
            return "gif"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        if len(data) >= 4 and data[:4] == bytes([0x49, 0x49, 0x2A, 0x00]):
            return "tif"
        if len(data) >= 2 and data[:2] == b"BM":
            return "bmp"
        return "bin"

    def cleanup_old(self, max_age_hours: int = 24) -> None:
        cutoff = time.time() - max_age_hours * 3600
        for f in self.output_dir.iterdir():
            if f.is_file():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass
