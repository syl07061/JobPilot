"""以 BossHunter 自己的 Python 运行：原版 HTTP 处理器 + JobPilot 私有配置。

不复制采集实现、不修改 BossHunter 文件；原 data/、Runtime、访问账本和锁保持共用。
不能直接用原 CLI --config 实现此隔离：原版 web 会重新指定 BASE_DIR/config.yaml。
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import socket


def public_config(source: dict) -> dict:
    """不读取私有凭据文件，不复制 AI Key、简历路径或个人亮点。"""
    private = re.compile(r"cookie|token|password|secret|credential|api_key|authorization", re.I)

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if not private.search(str(k))}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    config = {k: clean(v) for k, v in source.items()
              if k in {"profile", "search", "collection", "platforms", "scoring", "throttle", "safety", "browser", "dedup", "outsourcing"}}
    profile = config.get("profile", {})
    for key in ("resume_path", "resume_output_dir", "extra_highlights", "portfolio_url", "fixed_greeting", "greeting_preference"):
        profile.pop(key, None)
    config["collection"] = {**config.get("collection", {}), "auto_score_default": False}
    return config


def main():
    parser = argparse.ArgumentParser(description="Start isolated-config native BossHunter HTTP handlers")
    parser.add_argument("--boss-root", type=Path, default=Path(r"D:\Projects\BossHunter"))
    parser.add_argument("--port", type=int, default=8687)
    parser.add_argument("--original-url", default="http://127.0.0.1:8686")
    args = parser.parse_args()
    import httpx
    import yaml
    from urllib.parse import urlsplit

    root = args.boss_root.resolve()
    original = root / "config.yaml"
    if not original.is_file():
        raise SystemExit("BossHunter config.yaml not found; no files changed.")
    if urlsplit(args.original_url).hostname != "127.0.0.1":
        raise SystemExit("Original workbench must be loopback.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))  # 已占用则退出，不结束任何已有进程。
    try:
        response = httpx.get(args.original_url + "/api/workbench", timeout=3, trust_env=False)
        response.raise_for_status()
        if response.json().get("task"):
            raise SystemExit("Original workbench has an active task; stop it before starting compatibility mode.")
    except httpx.ConnectError:
        pass  # 原工作台不必运行；后端引擎仍通过原版互斥锁串行使用浏览器。
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    source = yaml.safe_load(original.read_text(encoding="utf-8")) or {}
    config = public_config(source)
    browser = config.get("browser", {})
    if browser.get("proxy_host", "127.0.0.1") != "127.0.0.1":
        raise SystemExit("Runtime must use loopback.")
    if browser.get("enable_port_guard", True):
        raise SystemExit("Source enables port-probe interception; disabled guard is required for this non-bypass experiment. Source config was not changed.")
    state_dir = Path(__file__).resolve().parents[1] / "data" / "bosshunter_compat"
    state_dir.mkdir(parents=True, exist_ok=True)
    isolated = state_dir / "config.yaml"
    isolated.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.chdir(root)
    # 以下仅为启动兼容层；JobPilot 业务代码不导入 BossHunter。
    from bosshunter.browser import configure
    from bosshunter.browser.runtime import runtime_health
    from bosshunter.web import server
    from bottle import abort, request
    configure(config)
    server.CONFIG_PATH = isolated
    # BASE_DIR/DATA_DIR 保持原版默认的 BossHunter 根目录；不调用会标记旧任务为孤儿的 set_base_dir。
    if server.DATA_DIR.resolve() != (root / "data").resolve():
        raise SystemExit("Unexpected native data directory; refusing to use a separate quota ledger.")

    def unchanged():
        return hashlib.sha256(original.read_bytes()).hexdigest() == original_hash

    @server.app.get("/jobpilot/capabilities")
    def capabilities():
        health = runtime_health(config) or {}
        return {"config_isolated": True, "collect_only": True, "shared_native_data_and_quotas": True,
                "source_config_unchanged": unchanged(), "runtime_connected": health.get("connected", False)}

    @server.app.hook("before_request")
    def local_collect_only():
        if request.remote_addr != "127.0.0.1" or request.urlparts.hostname != "127.0.0.1":
            abort(403, "Loopback only")
        if request.get_header("Origin") not in (None, f"http://127.0.0.1:{args.port}"):
            abort(403, "Cross-origin requests rejected")
        path = request.path
        if request.method == "GET":
            if path in {"/jobpilot/capabilities", "/api/workbench", "/api/config/cities", "/api/jobs", "/api/collection/runs"} or re.fullmatch(r"/api/(?:jobs|collection/runs)/[A-Za-z0-9_:\-]+", path):
                return
        if request.method == "POST" and re.fullmatch(r"/api/workbench/task/[A-Za-z0-9-]+/stop", path):
            return
        if request.method == "POST" and path == "/api/workbench/task":
            if not unchanged():
                abort(409, "Source settings changed; restart to inherit latest safety configuration")
            body = request.json or {}
            options = body.get("options", {})
            if (set(body) != {"mode", "options"} or body.get("mode") != "collect"
                    or set(options) != {"platform_order", "auto_score", "platforms"}
                    or options.get("auto_score") is not False or options.get("platform_order") != ["boss"]
                    or set(options.get("platforms", {})) != {"boss"}):
                abort(403, "Only explicit BOSS collect without AI is allowed")
            try:
                other = httpx.get(args.original_url + "/api/workbench", timeout=3, trust_env=False)
                other.raise_for_status()
                if other.json().get("task"):
                    abort(409, "Original workbench busy")
            except httpx.ConnectError:
                pass
            return
        abort(403, "Endpoint is not part of the read-only collection integration")

    print(f"Native BossHunter handlers: http://127.0.0.1:{args.port}; private settings: {isolated}", flush=True)
    print("No task is started automatically. Original config/data are not cleared; quota and browser locks remain native.", flush=True)
    # 原 run_server 含持续任务自动恢复；此处仅启动其原生 Bottle app，不恢复或启动任何任务。
    server.app.run(host="127.0.0.1", port=args.port, server_class=server.ThreadingWSGIServer,
                   quiet=True, reloader=False)


if __name__ == "__main__":
    main()
