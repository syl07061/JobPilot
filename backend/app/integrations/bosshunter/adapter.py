from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.integrations.collector import CollectorAdapter, CollectorError
from app.schemas.search import SearchRequest


class BossHunterAdapter(CollectorAdapter):
    """只调用已核对的 HTTP 契约；不读取源码、配置或 SQLite。"""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        u = urlsplit(base_url)
        if u.scheme != "http" or u.hostname != "127.0.0.1" or u.username or u.password or u.query or u.fragment or u.path not in {"", "/"}:
            raise ValueError("BossHunter 地址必须是无凭证的 http://127.0.0.1 本地地址")
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=20, trust_env=False,
                                         transport=self.transport) as client:
                r = await client.request(method, path, **kwargs)
            if r.status_code == 409:
                raise CollectorError("busy", "BossHunter 正在执行其他任务，或来源配置已变化；请检查工作台。")
            if r.status_code >= 500 and method == "POST" and path == "/api/workbench/task":
                raise CollectorError("start_unconfirmed", "采集启动返回服务端错误，无法确认是否已经创建任务；请核对8687工作台状态，勿重复提交。")
            if r.status_code >= 400:
                # 不回显上游可能包含个人资料或凭证的异常正文。
                raise CollectorError("upstream_rejected", f"BossHunter 返回 HTTP {r.status_code}；未自动重试。")
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            code = "start_unconfirmed" if method == "POST" and path == "/api/workbench/task" else "source_unavailable"
            raise CollectorError(code, "BossHunter 未就绪或响应无效；若为任务启动请求，请先核对8687工作台状态，勿重复提交。") from exc

    async def status(self) -> dict[str, Any]:
        marker = await self._request("GET", "/jobpilot/capabilities")
        if not isinstance(marker, dict) or marker.get("config_isolated") is not True or marker.get("collect_only") is not True:
            raise CollectorError("compatibility_required", "请启动 JobPilot 的 BossHunter 配置隔离工作台（默认8687）；普通8686接口会改写用户配置，已拒绝启动任务。")
        if marker.get("source_config_unchanged") is not True:
            raise CollectorError("config_changed", "原 BossHunter 配置已变化，请停止任务并重启隔离工作台以重新继承安全配置。")
        return {"available": True, "config_isolated": True, "runtime_connected": marker.get("runtime_connected"),
                "message": "原版纯采集接口就绪；原有过滤、访问额度和风险停止仍生效。"}

    async def start(self, request: SearchRequest) -> dict[str, Any]:
        await self.status()
        cities = await self._request("GET", "/api/config/cities")
        code = cities.get(request.city) or cities.get(request.city.removesuffix("市")) if isinstance(cities, dict) else None
        if not code:
            raise CollectorError("invalid_city", "BossHunter 本地城市目录未识别该城市；未进行网络城市探测或启动采集。")
        body = {"mode": "collect", "options": {"platform_order": ["boss"], "auto_score": False,
                "platforms": {"boss": {"keywords": [request.keyword], "cities": [request.city],
                    "city_codes": {request.city: str(code)}, "max_pages": request.max_pages,
                    "sort": "default", "filters": {}, "city_yield_policy": {"enabled": False}}}}}
        data = await self._request("POST", "/api/workbench/task", json=body)
        if not isinstance(data, dict) or not data.get("id") or data.get("mode") != "collect":
            raise CollectorError("start_unconfirmed", "未得到可确认的纯采集任务编号；请核对原工作台，勿重复提交。")
        return data

    async def poll(self, task_id: str, run_id: str | None) -> dict[str, Any]:
        workbench = await self._request("GET", "/api/workbench")
        task = next((t for t in (workbench.get("task"), workbench.get("last_task"))
                     if isinstance(t, dict) and t.get("id") == task_id), None)
        if not run_id and task:
            run_id = task.get("progress", {}).get("run_id")
        if not run_id:
            runs = await self._request("GET", "/api/collection/runs?limit=100")
            run_id = next((r.get("id") for r in runs if r.get("task_id") == task_id), None)
        run = await self._request("GET", f"/api/collection/runs/{quote(run_id, safe='')}") if run_id else None
        if run and run.get("task_id") != task_id:
            raise CollectorError("task_mismatch", "采集记录不属于本次任务，已拒绝读取结果。")
        if task is None and run is None:
            raise CollectorError("task_lost", "无法找到本次任务；不会以历史岗位列表替代。")
        return {"task": task, "run": run}

    async def job(self, job_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/api/jobs/{quote(job_id, safe='')}")
        if not isinstance(data, dict) or data.get("id") != job_id:
            raise CollectorError("job_mismatch", "岗位响应编号与本次采集记录不一致。")
        return data

    async def stop(self, task_id: str) -> None:
        await self._request("POST", f"/api/workbench/task/{quote(task_id, safe='')}/stop")
