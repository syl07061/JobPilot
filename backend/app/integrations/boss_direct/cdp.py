"""最小公开 CDP 协议客户端：只连选定页面，不启用自动附加或注入脚本。"""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect

from app.integrations.collector import CollectorError

DETAIL_TARGET_JS = Path(__file__).with_name('detail_target.js').read_text(encoding='utf-8')


def search_target(target: dict) -> bool:
    u = urlsplit(target.get("url", ""))
    return (target.get("type") == "page" and u.scheme == "https"
            and (u.hostname == "zhipin.com" or (u.hostname or "").endswith(".zhipin.com"))
            and u.path.rstrip("/") in {"/web/geek/jobs", "/web/geek/job"})


def public_url(url: str) -> str:
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.hostname or "", u.path, "", ""))


class ChromeDiscovery:
    def __init__(self, base_url: str):
        u = urlsplit(base_url)
        if u.scheme != "http" or u.hostname != "127.0.0.1" or u.path not in ("", "/") or u.query or u.fragment or u.username or u.password:
            raise ValueError("Chrome CDP 必须使用无凭证的 http://127.0.0.1 本机地址")
        self.base_url = base_url.rstrip("/")
        self.port = u.port or 80

    async def select(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(self.base_url + "/json/list")
                response.raise_for_status()
                targets = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CollectorError("source_unavailable", "无法读取专用 Chrome 调试端口，请先手动启动 Chrome。") from exc
        if not isinstance(targets, list):
            raise CollectorError("source_unavailable", "Chrome 调试端口返回了无法识别的数据。")
        matches = [t for t in targets if isinstance(t, dict) and search_target(t)]
        if not matches:
            raise CollectorError("no_search_tab", "没有找到 BOSS 搜索结果页。请手动登录、选好搜索条件并打开结果页。")
        if len(matches) != 1:
            raise CollectorError("ambiguous_tab", "存在多个 BOSS 搜索结果页，请只保留一个，避免读取错误的标签页。")
        target = matches[0]
        w = urlsplit(target.get("webSocketDebuggerUrl", ""))
        if (w.scheme != "ws" or w.hostname != "127.0.0.1" or w.port != self.port
                or not w.path.startswith("/devtools/page/") or w.query or w.fragment or w.username or w.password):
            raise CollectorError("unsafe_endpoint", "拒绝使用调试端口返回的异常 WebSocket 地址。")
        return target


class PageCDP:
    def __init__(self, uri: str):
        self.uri, self.sequence, self.socket = uri, 0, None

    async def __aenter__(self):
        try:
            self.socket = await connect(self.uri, proxy=None, open_timeout=5, close_timeout=2,
                                        max_size=8 * 1024 * 1024)
        except Exception as exc:
            raise CollectorError("source_unavailable", "无法连接选定的 Chrome 标签页；未创建或导航任何页面。") from exc
        return self

    async def __aexit__(self, *_):
        # 仅断开 WebSocket，绝不关闭用户标签页或浏览器。
        try:
            await self.socket.close()
        except Exception:
            pass  # 保留原始的页面限制/读取异常，不被断开连接错误覆盖。

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        command_id = self.sequence
        try:
            await self.socket.send(json.dumps({"id": command_id, "method": method, "params": params or {}}))
            async with asyncio.timeout(12):
                while True:
                    message = json.loads(await self.socket.recv())
                    if message.get("method") == "Inspector.detached":
                        raise CollectorError("page_unavailable", "选定标签页已断开，可能被关闭或崩溃；已停止。")
                    if message.get("id") == command_id:
                        if "error" in message:
                            raise CollectorError("cdp_error", "Chrome 拒绝了本次页面操作；没有自动重试。")
                        return message.get("result", {})
        except CollectorError:
            raise
        except TimeoutError as exc:
            raise CollectorError("load_failed", "Chrome 页面操作超时，已停止，不自动重复检索。") from exc
        except Exception as exc:
            raise CollectorError("page_unavailable", "标签页连接异常，已停止；不会切换到其他页面。") from exc

    async def evaluate(self, expression: str) -> dict:
        result = await self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                       "timeout": 4000})
        if result.get("exceptionDetails"):
            raise CollectorError("dom_error", "DOM 读取表达式执行失败；没有记录可能包含敏感内容的浏览器异常正文。")
        value = result.get("result", {}).get("value")
        if not isinstance(value, dict):
            raise CollectorError("dom_error", "当前页面未返回可识别的 DOM 结果。")
        return value

    async def select_job(self, job: dict, context: dict):
        """正常滚动并单击列表卡片空白处，不操作任何沟通/投递控件。"""
        payload = {'url': job['url'], 'title': job['title'], 'context': context}
        result = await self.evaluate(DETAIL_TARGET_JS + '(' + json.dumps({**payload, 'scroll': True}) + ')')
        if result.get('status') != 'scrolled':
            raise CollectorError('page_changed' if result.get('status') == 'page_changed' else 'detail_not_found',
                                 '岗位卡片已变化或未找到，已停止自动详情读取；没有点击其他元素。')
        result = await self.evaluate(DETAIL_TARGET_JS + '(' + json.dumps({**payload, 'scroll': False}) + ')')
        if result.get('status') != 'ready':
            raise CollectorError('page_changed' if result.get('status') == 'page_changed' else 'detail_not_found',
                                 '未找到该岗位安全的卡片点击位置，已停止；不会点击链接、收藏或沟通按钮。')
        point = {'x': result['x'], 'y': result['y'], 'button': 'left', 'clickCount': 1}
        await self.call('Input.dispatchMouseEvent', {**point, 'type': 'mousePressed', 'buttons': 1})
        await self.call('Input.dispatchMouseEvent', {**point, 'type': 'mouseReleased', 'buttons': 0})
