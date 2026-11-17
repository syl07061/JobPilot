"""只读取现有本地岗位，不创建平台任务。数据和公开招聘者字段不提交 Git。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from app.integrations.bosshunter.mapping import scrub


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8686")
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname != "127.0.0.1":
        raise SystemExit("Only loopback API allowed")
    with httpx.Client(base_url=args.base_url, timeout=10, trust_env=False) as client:
        response = client.get("/api/jobs", params={"limit": 5})
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise SystemExit("Unexpected native API shape; sample not written")
    stats = {key: {"types": sorted({type(row.get(key)).__name__ for row in rows[:5]}),
                   "nonempty": sum(row.get(key) not in (None, "", []) for row in rows[:5])}
             for key in sorted({key for row in rows[:5] for key in row})}
    sample = {"source_layer": "local_api", "historical_sample": True, "field_stats_before_redaction": stats,
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "endpoint": "/api/jobs?limit=5", "jobs": scrub(rows[:5])}
    dest = ROOT / "data" / "samples" / "bosshunter_jobs.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(sample['jobs'])} historical local API records to {dest}; no collection started.")


if __name__ == "__main__":
    main()
