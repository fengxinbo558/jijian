"""Small, deterministic redaction layer applied before optional model calls."""

from __future__ import annotations

import re
from typing import Dict, List, Pattern, Tuple


_REDACTION_RULES: List[Tuple[str, Pattern[str]]] = [
    (
        "credential",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd)\b"
            r"\s*[:=]\s*([^\s,;]+)"
        ),
    ),
    (
        "bearer",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]+=*"),
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
]


def redact_text(text: str) -> Tuple[str, Dict[str, int]]:
    """Redact likely credentials and emails while preserving device identity."""

    result = text
    counts: Dict[str, int] = {}
    for name, pattern in _REDACTION_RULES:
        if name == "credential":
            result, count = pattern.subn(lambda match: f"{match.group(1)}=[REDACTED]", result)
        elif name == "email":
            result, count = pattern.subn("[REDACTED_EMAIL]", result)
        else:
            result, count = pattern.subn("Bearer [REDACTED]", result)
        if count:
            counts[name] = count
    return result, counts

