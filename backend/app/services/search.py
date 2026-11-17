"""一次用户提交对应一个原生任务；只按 run/task 精确归属读取新增 ID。"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.integrations.collector import CollectorAdapter, CollectorError
from app.integrations.bosshunter.mapping import map_job, snapshot
from app.schemas.search import SearchRequest, SearchTask

TERMINAL = {"completed", "completed_with_shortage", "completed_with_errors", "failed", "stopped"}


def outcome_for(run: dict, task: dict | None):
    state = run.get("platform_states", {}).get("boss", {})
    reason = state.get("reason_code") or run.get("stop_reason") or ""
    if reason in {"captcha", "blocked", "rate_limit", "login_required", "persistent_risk_lock"}:
        return "platform_restricted", reason, "平台验证、访问限制或风险冷却，已停止；不要规避或自动重试。"
    if reason in {"daily_search_page_limit", "daily_detail_page_limit", "daily_platform_page_limit"}:
        return "quota_exhausted", reason, "原版本地访问额度耗尽；已停止，未清空记录或提高额度。"
    if reason in {"list_load_unconfirmed", "consecutive_page_failures", "list_parse_failed", "search_page_not_advanced", "cdp_empty_shell"}:
        return "load_failed", reason, "搜索页加载、解析或推进未确认；不能解释为正常无结果。"
    if run.get("status") == "stopped" or reason == "user_stopped":
        return "stopped", reason, "检索已停止；已获得的数据仅限本次 run。"
    if state.get("parse_failed", 0) or state.get("save_failed", 0) or state.get("status") in {"failed", "blocked"} or (task and task.get("status") == "failed") or run.get("error"):
        return "collector_error", reason or "collection_error", "采集器报告异常；不作为正常零结果处理。"
    if run.get("collected_job_ids"):
        return "new_jobs", reason, "本次采集产生了新岗位；仍受原版预筛与去重规则影响。"
    seen, duplicate, filtered = (int(state.get(k) or 0) for k in ("seen", "duplicate", "filtered"))
    if seen > 0 and duplicate == seen and filtered == 0:
        return "duplicates_only", reason, "本次读取的岗位全是 BossHunter 历史重复岗位，没有新增。"
    if filtered > 0:
        return "filtered_only", reason, "本次没有新增，存在原版规则过滤；不能当作 BOSS 搜索无岗位。"
    if duplicate > 0:
        return "unknown", reason, "读到了历史重复岗位，但计数不足以证明全部重复；未返回历史岗位充当新结果。"
    if reason == "no_jobs_for_search" and seen == 0:
        return "no_results", reason, "原版报告搜索页就绪但没有岗位；本接口没有原始 DOM，仍受原版判定能力限制。"
    return "unknown", reason or "unconfirmed_empty", "没有得到可确认的新岗位或明确空结果证据，请核对原工作台；不会自动再采集。"


class SearchService:
    def __init__(self, adapter: CollectorAdapter, root: Path, poll_seconds: float = 2):
        self.adapter, self.root, self.poll_seconds = adapter, root, poll_seconds
        self.tasks: dict[str, SearchTask] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.uncertain_start = False

    def _save(self, task: SearchTask):
        task.updated_at = datetime.now(timezone.utc)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{task.id}.json"
        staged = path.with_suffix(".tmp")
        staged.write_text(task.model_dump_json(indent=2), encoding="utf-8")
        staged.replace(path)

    async def get(self, task_id: str) -> SearchTask:
        try:
            UUID(task_id)
        except ValueError:
            raise KeyError(task_id) from None
        if task_id not in self.tasks:
            path = self.root / f"{task_id}.json"
            if not path.exists():
                raise KeyError(task_id)
            self.tasks[task_id] = SearchTask.model_validate_json(path.read_text(encoding="utf-8"))
        task = self.tasks[task_id]
        if task.state != "completed" and task_id not in self.workers:
            if task.native_task_id:
                # 重启后仅恢复轮询，不重新发起采集。
                self.workers[task.id] = asyncio.create_task(self._watch(task))
            else:
                task.state, task.outcome = "completed", "unknown"
                task.message = "此前启动中断，无法确认原生任务是否创建；请核对 BossHunter，不自动重试。"
                self.uncertain_start = True
                self._save(task)
        return task.model_copy(deep=True)

    async def create(self, request: SearchRequest) -> SearchTask:
        async with self.lock:
            if self.uncertain_start or any(t.state != "completed" for t in self.tasks.values()):
                raise CollectorError("busy", "已有任务未结束，或上次启动结果不确定；请先核对/停止原工作台任务。")
            now = datetime.now(timezone.utc)
            task = SearchTask(id=str(uuid4()), request=request, created_at=now, updated_at=now)
            self.tasks[task.id] = task
            self._save(task)
            try:
                status = await self.adapter.status()
                if status.get("runtime_connected") is False:
                    raise CollectorError("source_unavailable", "原版 Runtime 未连接专用 Chrome，请先在 BossHunter 完成连接与手动登录。")
                native = await self.adapter.start(request)
                task.native_task_id = str(native["id"])
                task.state = "running"
                task.message = "原版纯采集已启动，正在等待本次 run 的数据。"
                self.workers[task.id] = asyncio.create_task(self._watch(task))
            except CollectorError as exc:
                task.state = "completed"
                task.outcome = "source_unavailable" if exc.code in {"source_unavailable", "compatibility_required", "config_changed"} else "collector_error"
                task.reason_code, task.message = exc.code, str(exc)
                # POST 超时可能已创建任务，绝不重发；保守阻止同进程再次启动。
                if exc.code == "start_unconfirmed":
                    self.uncertain_start = True
            self._save(task)
            return task.model_copy(deep=True)

    async def _watch(self, task: SearchTask):
        deadline = asyncio.get_running_loop().time() + 1800
        try:
            while asyncio.get_running_loop().time() < deadline:
                value = await self.adapter.poll(task.native_task_id, task.native_run_id)
                run, native = value.get("run"), value.get("task")
                if run:
                    task.native_run_id = str(run["id"])
                    options = run.get("options", {})
                    search = options.get("platforms", {}).get("boss", {})
                    if (options.get("platform_order") != ["boss"] or options.get("auto_score") is not False
                            or search.get("keywords") != [task.request.keyword]
                            or search.get("cities") != [task.request.city]
                            or search.get("max_pages") != task.request.max_pages):
                        raise CollectorError("task_mismatch", "原生 run 的参数与本次请求不一致，拒绝混入其他任务结果。")
                    state = run.get("platform_states", {}).get("boss", {})
                    task.counts = {key: int(state.get(key) or 0) for key in ("seen", "new", "duplicate", "filtered", "parse_failed", "save_failed")}
                    task.raw_run = snapshot(run, f"/api/collection/runs/{task.native_run_id}")
                    known = {job.id for job in task.jobs}
                    # 唯一来源是原版该 run 的新 ID；不遍历全局岗位池猜测本次结果。
                    for job_id in dict.fromkeys(run.get("collected_job_ids", [])):
                        if job_id not in known:
                            payload = await self.adapter.job(job_id)
                            task.jobs.append(map_job(payload, f"/api/jobs/{job_id}"))
                            known.add(job_id)
                    if run.get("status") in TERMINAL:
                        task.state = "completed"
                        task.outcome, task.reason_code, task.message = outcome_for(run, native)
                        self.uncertain_start = False
                        self._save(task)
                        return
                elif native and native.get("status") in {"failed", "stopped", "completed"}:
                    raise CollectorError("missing_run", "原生任务已结束但没有可归属的 run；未返回历史数据。")
                self._save(task)
                await asyncio.sleep(self.poll_seconds)
            task.state, task.outcome = "completed", "unknown"
            task.message = "轮询达到30分钟上限；原生任务可能仍运行，请在工作台检查或停止，未自动重试。"
            self.uncertain_start = True
        except asyncio.CancelledError:
            # 服务退出不撤销或重启原生任务，下次 GET 可恢复只读轮询。
            return
        except CollectorError as exc:
            task.state = "completed"
            task.outcome = "source_unavailable" if exc.code == "source_unavailable" else "collector_error"
            task.reason_code, task.message = exc.code, str(exc)
            self.uncertain_start = True
        except Exception:
            task.state, task.outcome = "completed", "collector_error"
            task.reason_code, task.message = "unexpected_error", "JobPilot 任务处理异常，原生任务可能仍在运行；请核对工作台。"
            self.uncertain_start = True
        self._save(task)

    async def stop(self, task_id: str) -> SearchTask:
        await self.get(task_id)
        task = self.tasks[task_id]
        if task.native_task_id and (task.state != "completed" or task.outcome in {"unknown", "collector_error", "source_unavailable"}):
            await self.adapter.stop(task.native_task_id)
            task.state, task.outcome, task.message = "stopping", None, "已请求原版安全停止，等待采集器退出。"
            if task.id not in self.workers or self.workers[task.id].done():
                self.workers[task.id] = asyncio.create_task(self._watch(task))
            self._save(task)
        return task.model_copy(deep=True)
