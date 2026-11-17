"""在仓库根目录启动后端，统一 .env 与未来 SQLite 相对路径的基准。"""
import argparse
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the JobPilot backend")
    parser.add_argument("--reload", action="store_true", help="Enable development reload")
    args = parser.parse_args()
    settings = get_settings()
    os.chdir(PROJECT_ROOT)

    import uvicorn

    uvicorn.run(
        "app.main:app", host=settings.backend_host, port=settings.backend_port,
        reload=args.reload, reload_dirs=[str(PROJECT_ROOT / "backend")] if args.reload else None,
    )


if __name__ == "__main__":
    main()
