"""Shared MEGA crypto and size helpers."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta

HAS_CRYPTO = False
AES = None
try:
    from Crypto.Cipher import AES as _AES

    AES = _AES
    HAS_CRYPTO = True
except ImportError:
    pass


def base64urldecode(data: str) -> bytes:
    data += "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data)


def a32_to_str(a) -> bytes:
    return b"".join(x.to_bytes(4, "big") for x in a)


def str_to_a32(b: bytes):
    if len(b) % 4:
        b += b"\0" * (4 - len(b) % 4)
    return [int.from_bytes(b[i : i + 4], "big") for i in range(0, len(b), 4)]


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes = b"\0" * 16):
    if not HAS_CRYPTO:
        return None
    return AES.new(key, AES.MODE_CBC, iv=iv).decrypt(data)


def aes_ecb_decrypt(data: bytes, key: bytes):
    if not HAS_CRYPTO:
        return None
    return AES.new(key, AES.MODE_ECB).decrypt(data)


def decrypt_key(cipher_a32, key_a32):
    if not HAS_CRYPTO:
        return None
    decrypted = []
    key_bytes = a32_to_str(key_a32)
    for i in range(0, len(cipher_a32), 4):
        block = a32_to_str(cipher_a32[i : i + 4])
        dec_block = aes_ecb_decrypt(block, key_bytes)
        if dec_block is None:
            return None
        decrypted += str_to_a32(dec_block)
    return tuple(decrypted)


def decrypt_attr(attr_bytes: bytes, k):
    if not HAS_CRYPTO:
        return None
    dec_attr = aes_cbc_decrypt(attr_bytes, a32_to_str(k))
    if dec_attr is None:
        return None
    dec_attr = dec_attr.rstrip(b"\0")
    if dec_attr.startswith(b"MEGA"):
        try:
            attr_json = json.loads(dec_attr[4:].decode("utf-8"))
            return attr_json.get("n", "Unknown")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return None


def file_aes_parts(file_key_a32):
    """Return (aes_key_a32, iv_a32, meta_mac_a32) from an 8-int file key."""
    file_key_a32 = tuple(file_key_a32)
    k = (
        file_key_a32[0] ^ file_key_a32[4],
        file_key_a32[1] ^ file_key_a32[5],
        file_key_a32[2] ^ file_key_a32[6],
        file_key_a32[3] ^ file_key_a32[7],
    )
    iv = file_key_a32[4:6] + (0, 0)
    meta_mac = file_key_a32[6:8]
    return k, iv, meta_mac


def parse_size(s):
    if s is None:
        return None
    txt = s.strip().replace(" ", "").lower()
    m = re.match(r"^(\d+(\.\d+)?)([kmgtp]?b?)$", txt)
    if not m:
        raise ValueError(f"Invalid size: {s}")
    val = float(m.group(1))
    unit = m.group(3)
    mult = 1
    if unit in ("k", "kb"):
        mult = 1024
    elif unit in ("m", "mb"):
        mult = 1024**2
    elif unit in ("g", "gb"):
        mult = 1024**3
    elif unit in ("t", "tb"):
        mult = 1024**4
    elif unit in ("p", "pb"):
        mult = 1024**5
    return int(val * mult)


def parse_date_ymd_start(s):
    if s is None:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except Exception as exc:
        raise ValueError(f"Invalid date (use YYYY-MM-DD): {s}") from exc


def parse_date_ymd_end_inclusive(s):
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
        end = dt + timedelta(days=1) - timedelta(seconds=1)
        return int(end.timestamp())
    except Exception as exc:
        raise ValueError(f"Invalid date (use YYYY-MM-DD): {s}") from exc


def format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ["bytes", "KB", "MB", "GB", "TB"]:
        if value < 1024:
            if unit == "bytes":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


def format_duration(seconds) -> str:
    secs = int(round(seconds))
    if secs < 60:
        return f"{secs}s"
    mins, s = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m {s}s"
    hrs, m = divmod(mins, 60)
    if hrs < 24:
        return f"{hrs}h {m}m"
    days, h = divmod(hrs, 24)
    return f"{days}d {h}h"


def download_time_seconds(total_bytes, mbps):
    if not mbps or mbps <= 0:
        return None
    return (total_bytes * 8.0) / (mbps * 1_000_000.0)


def ext_of(name: str) -> str:
    i = name.rfind(".")
    if i == -1:
        return ""
    return name[i:].lower()


def categorize_ext(ext: str) -> str:
    video = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
    audio = {".mp3", ".flac", ".aac", ".m4a", ".wav", ".ogg", ".wma", ".alac"}
    image = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic"}
    archive = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"}
    docs = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".csv", ".md"}
    if ext in video:
        return "video"
    if ext in audio:
        return "audio"
    if ext in image:
        return "image"
    if ext in archive:
        return "archive"
    if ext in docs:
        return "docs"
    return "other"


def get_chunks(size: int):
    p = 0
    s = 0x20000
    while p + s < size:
        yield p, s
        p += s
        if s < 0x100000:
            s += 0x20000
    yield p, size - p


def sanitize_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad else ch for ch in name).strip(" .")
    return cleaned or "unnamed"


RATE_LIMIT_CODES = {-4, -6}
QUOTA_CODES = {-8, -16, -18, -24}

MEGA_ERROR_CODES = {
    -1: "Internal error",
    -2: "Invalid argument",
    -3: "Request failed (retry)",
    -4: "Rate limit exceeded",
    -5: "Failed",
    -6: "Too many requests",
    -7: "Operation not allowed",
    -8: "Transfer limit reached",
    -9: "Not found",
    -10: "Circular linkage",
    -11: "Access denied",
    -12: "Already exists",
    -13: "Incomplete",
    -14: "Invalid key/Decryption error",
    -15: "Bad session ID",
    -16: "Quota exceeded",
    -17: "Resource temporarily unavailable",
    -18: "Request over quota",
    -19: "Connection reset by peer",
    -20: "Upload token expired",
    -21: "Invalid fingerprint",
    -22: "Invalid token",
    -23: "File too large",
    -24: "Bandwidth over quota",
}


class MegaAPIError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
