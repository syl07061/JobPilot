"""一键准备Chrome、前后端；不会创建采集任务，不关闭用户Chrome。"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


class StartupError(Exception):
    pass


def listening(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.4):
            return True
    except OSError:
        return False


def get_json(url: str):
    import httpx
    with httpx.Client(timeout=1, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def healthy_backend(url: str) -> bool:
    try:
        return (get_json(url + '/health').get('status') == 'ok'
                and get_json(url + '/openapi.json').get('info', {}).get('title') == 'JobPilot API')
    except Exception:
        return False


def healthy_frontend(url: str) -> bool:
    import httpx
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            response = client.get(url)
            return (response.status_code == 200 and '<title>JobPilot' in response.text
                    and get_json(url + '/api/health').get('status') == 'ok')
    except Exception:
        return False


def chrome_info(url: str) -> dict | None:
    try:
        data = get_json(url + '/json/version')
        endpoint = urlsplit(data.get('webSocketDebuggerUrl', ''))
        if (not data.get('Browser', '').startswith(('Chrome/', 'Chromium/', 'HeadlessChrome/'))
                or endpoint.scheme != 'ws' or endpoint.hostname != '127.0.0.1'
                or endpoint.port != urlsplit(url).port or not endpoint.path.startswith('/devtools/browser/')
                or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
            return None
        return data
    except Exception:
        return None


def node_path(root: Path) -> str:
    candidates = [root / '.tools/node/node.exe', shutil.which('node.exe') or shutil.which('node')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            result = subprocess.run([str(candidate), '--version'], capture_output=True, text=True,
                                    timeout=5, creationflags=flags)
            match = re.fullmatch(r'v(\d+)\.(\d+)\.\d+\s*', result.stdout)
            if match and tuple(map(int, match.groups())) >= (22, 12):
                return str(candidate)
    raise StartupError('找不到Node.js 22.12+：请安装Node，或放在项目 .tools/node 目录。')


def chrome_path() -> str:
    candidates = [Path(os.environ.get(key, '')) / suffix for key, suffix in (
        ('PROGRAMFILES', 'Google/Chrome/Application/chrome.exe'),
        ('PROGRAMFILES(X86)', 'Google/Chrome/Application/chrome.exe'),
        ('LOCALAPPDATA', 'Google/Chrome/Application/chrome.exe'))]
    candidate = next((p for p in candidates if p.is_file()), None)
    if candidate:
        return str(candidate)
    raise StartupError('未找到Google Chrome，请先安装Chrome或手动启动9222专用实例。')


def ensure_service(name, port, probe, command, spawn):
    """先验证服务身份；无关进程占用端口时只报错，绝不杀进程。"""
    if listening(port):
        if not probe():
            raise StartupError(f'{name}端口 {port} 已被其他服务占用，未终止该进程。')
        print(f'[复用] {name}：127.0.0.1:{port}', flush=True)
        return
    process = spawn(name, command())
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if probe():
            print(f'[就绪] {name}：127.0.0.1:{port}', flush=True)
            return
        if name != 'Chrome' and process.poll() is not None:
            raise StartupError(f'{name}启动失败，请查看 output/startup/{name}.log。')
        time.sleep(.25)
    raise StartupError(f'{name}启动超时，请查看 output/startup/{name}.log。')


def open_pages(cdp_url: str, frontend_url: str):
    """只补开缺少的首页/工作台，不导航或关闭已有BOSS搜索标签。"""
    from websockets.sync.client import connect
    info = chrome_info(cdp_url)
    pages = get_json(cdp_url + '/json/list')
    if not info or not isinstance(pages, list):
        raise StartupError('无法确认Chrome标签页，未修改已有页面。')
    urls = [urlsplit(p.get('url', '')) for p in pages if p.get('type') == 'page']
    wanted = []
    if not any(u.scheme == 'https' and (u.hostname == 'zhipin.com' or (u.hostname or '').endswith('.zhipin.com')) for u in urls):
        wanted.append('https://www.zhipin.com/')
    if not any(u.scheme == 'http' and u.hostname == '127.0.0.1' and u.port == urlsplit(frontend_url).port for u in urls):
        wanted.append(frontend_url)
    if not wanted:
        return
    with connect(info['webSocketDebuggerUrl'], proxy=None, open_timeout=3, close_timeout=1) as channel:
        for sequence, url in enumerate(wanted, 1):
            channel.send(json.dumps({'id':sequence, 'method':'Target.createTarget', 'params':{'url':url}}))
            while True:
                result = json.loads(channel.recv(timeout=5))
                if result.get('id') == sequence:
                    if result.get('error'):
                        raise StartupError('Chrome拒绝打开新标签，已有页面保持不变。')
                    break


class Launcher:
    def __init__(self, settings, root=ROOT):
        self.settings, self.root, self.owned = settings, root, []
        self.backend_url = f'http://127.0.0.1:{settings.backend_port}'
        self.frontend_url = 'http://127.0.0.1:5173'
        self.cdp_url = str(settings.chrome_cdp_url).rstrip('/')
        u = urlsplit(self.cdp_url)
        if (u.scheme != 'http' or u.hostname != '127.0.0.1' or u.path not in ('', '/')
                or u.query or u.fragment or u.username or u.password or not u.port):
            raise StartupError('CHROME_CDP_URL必须是无凭证的本机HTTP地址，并指定端口。')
        self.cdp_port = u.port
        if len({self.cdp_port, settings.backend_port, 5173}) != 3:
            raise StartupError('Chrome、后端和前端必须使用不同端口。')

    def spawn(self, name, command, *, own=True):
        log_dir = self.root / 'output/startup'
        log_dir.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (log_dir / (name + '.log')).open('ab') as log:
            process = subprocess.Popen(command, cwd=self.root, stdout=log, stderr=log, creationflags=flags)
        if own:
            self.owned.append((name, process))
        return process

    def start(self, *, check_only=False, no_open=False):
        services = [('Chrome', self.cdp_port, lambda: bool(chrome_info(self.cdp_url))),
                    ('backend', self.settings.backend_port, lambda: healthy_backend(self.backend_url)),
                    ('frontend', 5173, lambda: healthy_frontend(self.frontend_url))]
        if check_only:
            ready = True
            for name, port, probe in services:
                available = probe()
                print(f'{name} / {port}: {"就绪" if available else "未就绪或端口冲突"}', flush=True)
                ready = ready and available
            return ready

        # 在产生任何进程前，先发现已有的无关端口占用，避免半启动。
        for name, port, probe in services:
            if listening(port) and not probe():
                raise StartupError(f'{name}端口 {port} 被其他服务占用，未终止或替换该服务。')
        chrome_command = lambda: [chrome_path(), f'--remote-debugging-port={self.cdp_port}',
            '--remote-debugging-address=127.0.0.1', f'--user-data-dir={self.root / ".chrome-profile"}', 'about:blank']
        ensure_service('Chrome', self.cdp_port, services[0][2], chrome_command,
                       lambda name, command: self.spawn(name, command, own=False))
        ensure_service('backend', self.settings.backend_port, services[1][2],
                       lambda: [sys.executable, str(self.root / 'scripts/start_backend.py')], self.spawn)

        def frontend_command():
            vite = self.root / 'frontend/node_modules/vite/bin/vite.js'
            if not vite.is_file():
                raise StartupError('前端依赖未安装，请先执行 npm.cmd --prefix frontend ci。')
            return [node_path(self.root), str(vite), str(self.root / 'frontend'),
                    '--config', str(self.root / 'frontend/vite.config.ts')]
        ensure_service('frontend', 5173, services[2][2], frontend_command, self.spawn)
        if not no_open:
            open_pages(self.cdp_url, self.frontend_url)
        print(f'JobPilot 已就绪：{self.frontend_url}\n请在专用Chrome中手动登录BOSS并打开搜索结果页；启动不会自动采集。', flush=True)
        return True

    def wait(self):
        if not self.owned:
            print('服务均已复用，原终端保持运行即可。', flush=True)
            return
        print('保持此终端运行；Ctrl+C停止本次启动的前后端，保留Chrome和复用服务。', flush=True)
        while True:
            for name, process in self.owned:
                if process.poll() is not None:
                    raise StartupError(f'{name}已退出，请查看 output/startup/{name}.log。')
            time.sleep(.5)

    def close(self):
        for _, process in reversed(self.owned):
            if process.poll() is None:
                # 默认启动的后端无reload子进程，前端直接运行Node，不经过npm/cmd进程树。
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description='Start JobPilot locally without duplicate services')
    parser.add_argument('--check-only', action='store_true', help='Only inspect local readiness; never start processes or open pages')
    parser.add_argument('--no-open', action='store_true', help='Start services without opening BOSS or JobPilot tabs')
    args = parser.parse_args()
    launcher = None
    try:
        if sys.version_info < (3, 11):
            raise StartupError('需要Python 3.11+，请使用JobPilot Conda环境。')
        from app.core.config import get_settings
        launcher = Launcher(get_settings())
        if not launcher.start(check_only=args.check_only, no_open=args.no_open):
            return 1
        if not args.check_only:
            launcher.wait()
        return 0
    except KeyboardInterrupt:
        print('\n正在停止本次启动的服务，保留Chrome。', flush=True)
        return 0
    except StartupError as exc:
        print(f'启动失败：{exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'启动失败（{type(exc).__name__}），请确认后端依赖已安装并查看 output/startup/ 日志。', file=sys.stderr)
        return 1
    finally:
        if launcher:
            launcher.close()


if __name__ == '__main__':
    raise SystemExit(main())
