"""本地 API 来源标记及脱敏；不声称拥有网页原始列表/详情对象。"""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.schemas.search import JobView, RawSnapshot
from app.core.redaction import scrub


def snapshot(payload: dict, endpoint: str) -> RawSnapshot:
    changes: list[str] = []
    clean = scrub(payload, redactions=changes)
    return RawSnapshot(endpoint=endpoint, captured_at=datetime.now(timezone.utc),
                       payload=clean, redactions=sorted(set(changes)))


def map_job(payload: dict, endpoint: str) -> JobView:
    raw = snapshot(payload, endpoint)
    fields = {key: raw.payload.get(key) if raw.payload.get(key) not in ("", None) else None
              for key in ("title", "company", "city", "salary", "experience", "education",
                          "company_size", "company_industry", "jd", "hr_name", "hr_title", "hr_active", "url")}
    # 当前 API 未单独提供；若上游新增同名字段则沿用原值，不从 JD 猜测或拆分。
    for key in ("district", "tags", "responsibilities", "requirements"):
        fields[key] = raw.payload.get(key)
    u = urlsplit(str(fields.get("url") or ""))
    if u.scheme != "https" or not (u.hostname == "zhipin.com" or (u.hostname or "").endswith(".zhipin.com")):
        fields["url"] = None
    return JobView(id=str(payload["id"]), fields=fields, raw=raw)
