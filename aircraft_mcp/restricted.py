"""Restricted-data patterns, shared by the Mode B refusal and the session logs.

The licensed dataset may only be used on this machine and must never be
copied anywhere else. The session logs under ~/aircraft-runs are such a
place: the index lists them and the dashboard serves them. So when a path or
a name matching these patterns appears in what a session would record, the
session log writes one ``restricted_not_recorded`` event and nothing more
for that session (see aircraft_mcp.runlog).

Deliberately conservative: the public example aircraft match none of the
patterns. The agency's acronym is checked only inside paths and file names,
because the public D150 example file names the agency in its header.
"""

from __future__ import annotations

import re
from typing import Any

#: Refused anywhere in an aircraft path (the Mode B refusal) and in any path
#: or file name a session would record.
PATTERNS = ("f25", "dlr")

#: Also refused as a word in free text (prompts, model replies, tool
#: arguments and results): the dataset's own name.
TEXT_PATTERNS = ("f25",)

#: Name of the marker file that keeps a session unrecorded across processes.
MARKER = "restricted.txt"

_TOKEN = re.compile(r"[a-z0-9]+")
_HEX_ID = re.compile(r"[0-9a-f]{6,}")
_ENCODED = re.compile(r"[A-Za-z0-9+/=\r\n]+")
_PATHLIKE = re.compile(
    r"[^\s\"'<>|,;()\[\]{}]*[/\\][^\s\"'<>|,;()\[\]{}]*"
    r"|[\w.-]+\.[a-z0-9]{1,5}\b"
)


def path_matches(path: Any) -> bool:
    """The Mode B rule: any pattern anywhere in the path, case-insensitive."""
    low = str(path).lower()
    return any(p in low for p in PATTERNS)


def text_matches(text: str) -> bool:
    """True when ``text`` names the dataset, or holds a path or file name
    that matches a pattern. Encoded payloads (base64) and hex identifiers
    (session ids, hashes) are not read as names."""
    low = text.lower()
    if not any(p in low for p in PATTERNS):
        return False
    if len(text) >= 64 and _ENCODED.fullmatch(text):
        return False
    if _has_name(low, TEXT_PATTERNS):
        return True
    return any(_has_name(m.group(0), PATTERNS) for m in _PATHLIKE.finditer(low))


def _has_name(low: str, patterns: tuple[str, ...]) -> bool:
    for tok in _TOKEN.findall(low):
        if any(p in tok for p in patterns) and not _HEX_ID.fullmatch(tok):
            return True
    return False


def find(obj: Any, where: str = "") -> str | None:
    """Where in ``obj`` (plain JSON values) a restricted pattern appears,
    as a dotted key path, or None. The matching text itself is never
    returned, so it can be reported without being copied."""
    if isinstance(obj, str):
        return (where or "value") if text_matches(obj) else None
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            sub = f"{where}.{key}" if where else key
            if text_matches(key):
                return sub
            hit = find(v, sub)
            if hit:
                return hit
        return None
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hit = find(v, f"{where}[{i}]")
            if hit:
                return hit
    return None
