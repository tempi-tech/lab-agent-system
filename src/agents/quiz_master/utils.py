\
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Optional, Tuple


_CMD_RE = re.compile(r"^!quiz\s+(\w+)(?:\s+(.*))?$", re.IGNORECASE)


def parse_quiz_command(text: str) -> Optional[Tuple[str, str]]:
    """Return (subcommand, args) or None."""
    m = _CMD_RE.match(text.strip())
    if not m:
        return None
    sub = m.group(1).lower()
    args = (m.group(2) or "").strip()
    return sub, args


def normalize_choice(s: str) -> str:
    """Normalize a participant answer into an option key like 'A' or free-text lower."""
    s = (s or "").strip()
    if not s:
        return ""
    # Accept '1' as A, etc
    if s[0].isdigit():
        mapping = {"1": "A", "2": "B", "3": "C", "4": "D", "5": "E"}
        return mapping.get(s[0], s[0]).upper()
    # Accept 'a', 'A', 'Ａ'
    s = s.replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C").replace("Ｄ", "D").replace("Ｅ", "E")
    # first token often enough
    token = re.split(r"\s+", s)[0]
    return token.strip().upper()


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def deterministic_draw(seed: str, key: str, candidates: List[str]) -> Optional[str]:
    """Deterministically choose 1 candidate using (seed, key)."""
    if not candidates:
        return None
    # stable order
    candidates_sorted = sorted(candidates, key=lambda x: int(x))
    h = sha256_hex(seed + "|" + key)
    idx = int(h, 16) % len(candidates_sorted)
    return candidates_sorted[idx]


def clip(text: str, max_len: int = 1200) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
