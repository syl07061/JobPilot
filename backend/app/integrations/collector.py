"""业务只依赖此抽象，不导入 BossHunter 包或数据库。"""
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Awaitable, Callable
from asyncio import Event

from app.schemas.search import SearchRequest


class CollectorError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CollectorAdapter(ABC):
    @abstractmethod
    async def status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def start(self, request: SearchRequest) -> dict[str, Any]: ...

    @abstractmethod
    async def poll(self, task_id: str, run_id: str | None) -> dict[str, Any]: ...

    @abstractmethod
    async def job(self, job_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def stop(self, task_id: str) -> None: ...


class BrowserCollectorAdapter(ABC):
    """独立的当前网页读取接口；不依赖旧 HTTP 任务协议。"""
    @abstractmethod
    async def status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def collect(self, max_pages: int, stop: Event,
                      on_page: Callable[[dict], Awaitable[None]]) -> dict: ...

    async def read_current(self, context: dict | None = None) -> dict:
        raise CollectorError('unsupported', '当前采集适配器不支持读取已打开的详情面板。')

    async def collect_details(self, jobs: list[dict], context: dict, stop: Event,
                              on_page: Callable[[dict], Awaitable[None]], batch: int) -> dict:
        raise CollectorError('unsupported', '当前采集适配器不支持自动读取JD。')
