"""通用数据脱敏；不依赖任何外部采集实现。"""
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SENSITIVE = re.compile(
    r"cookie|token|authorization|password|secret|credential|session|resume.*path|resume_source|resume_image", re.I,
)
URL = re.compile(r"https?://[^\s\"'<>]+")


def scrub(value: Any, path: str = "", redactions: list[str] | None = None) -> Any:
    redactions = redactions if redactions is not None else []
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if SENSITIVE.search(str(key)):
                result[key] = "[REDACTED]"
                redactions.append(child_path)
            else:
                result[key] = scrub(child, child_path, redactions)
        return result
    if isinstance(value, list):
        return [scrub(child, f"{path}[{i}]", redactions) for i, child in enumerate(value)]
    if isinstance(value, str):
        def clean_url(match):
            raw = match.group()
            try:
                u = urlsplit(raw)
                cleaned = urlunsplit((u.scheme, u.hostname or "", u.path, "", ""))
            except ValueError:
                cleaned = "[INVALID_URL]"
            if cleaned != raw:
                redactions.append(path)
            return cleaned
        value = URL.sub(clean_url, value)
        cleaned = re.sub(r"(?i)Bearer\s+[^\s]+|\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
        cleaned = re.sub(r"(?i)\b(?:access_token|refresh_token|cookie|password|session_id)\s*[:=]\s*[^\s,;]+", "[REDACTED]", cleaned)
        if cleaned != value:
            redactions.append(path)
        return cleaned
    return value
