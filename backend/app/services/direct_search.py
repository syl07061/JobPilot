"""JobPilot 独立列表读取任务；没有 BossHunter API、预筛或历史去重依赖。"""
import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from app.core.redaction import scrub
from app.integrations.collector import BrowserCollectorAdapter, CollectorError
from app.integrations.boss_direct.salary import normalize
from app.integrations.boss_direct.identity import detail_matches
from app.schemas.search import DirectSearchRequest, DirectSearchTask, JobView, RawSnapshot


class DirectSearchService:
    def __init__(self, collector: BrowserCollectorAdapter, root: Path):
        self.adapter, self.root = collector, root
        self.tasks: dict[str, DirectSearchTask] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()

    def save(self, task: DirectSearchTask):
        task.updated_at = datetime.now(timezone.utc)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{task.id}.json"
        staged = target.with_suffix(".tmp")
        staged.write_text(task.model_dump_json(indent=2), encoding="utf-8")
        staged.replace(target)

    @staticmethod
    def enrich(job: JobView, page: dict, captured: datetime, redactions: list[str]):
        """展示字段与原始数据分开保存，不能把面板 JD 赋给其他岗位。"""
        card = next((c for c in page.get('cards', []) if c['url'] == job.fields.get('url')), None)
        if card is not None:
            raw_salary = card.get('observed_fields', {}).get('salary')
            salary, provenance = normalize(raw_salary, page.get('salary_font'))
            job.fields.update(salary=salary, salary_raw=raw_salary, salary_decoding=provenance,
                salary_warning='薪资字形尚未验证，请保留原值，不猜测数字。' if provenance['status']=='unverified_font' else None)
            original_text = job.fields.get('list_text')
            if isinstance(original_text, str):
                job.fields['list_text_display'] = normalize(original_text, page.get('salary_font'))[0]
        detail = page.get('detail')
        if detail_matches(detail, job.fields.get('url')):
            # 列表可能带“-出差”等后缀，不用标题完全相同作为硬条件。
            job.fields.update(jd=detail['jd'], jd_status='captured', jd_tags=detail.get('tags', []), jd_error=None, jd_error_code=None)
            job.fields.update(detail_title=detail.get('title'),
                jd_title_differs=bool(detail.get('title') and job.fields.get('title') and detail['title'] != job.fields['title']),
                jd_identity={'method': 'detail_url_and_active_card' if 'active_job_urls' in detail else 'detail_url',
                             'url': detail['url']}, jd_diagnostic=None)
            job.detail_raw = RawSnapshot(source_layer='detail_dom', source='JobPilot / BOSS visible detail panel',
                endpoint='DOM:' + page['url'], captured_at=captured, payload=detail, redactions=redactions,
                note='搜索页职位描述原文，通过详情链接与已记录的选中卡片核对岗位；列表/详情标题可不同，分别保留，不读取招聘者联系方式或聊天内容。')

    def record_detail(self, task: DirectSearchTask, page: dict):
        captured = datetime.now(timezone.utc)
        redactions: list[str] = []
        safe = scrub(page, redactions=redactions)
        task.phase = 'details'
        progress = safe['detail_progress']
        task.counts.update(detail_total=progress['total'], detail_processed=progress['processed'])
        result = safe.get('detail_result')
        if result:
            job = next((j for j in task.jobs if j.fields.get('url') == result['url']), None)
            if job:
                if result['status'] == 'captured':
                    detail = safe.get('detail') or {}
                    if not detail_matches(detail, job.fields.get('url')):
                        raise CollectorError('detail_mismatch', 'JD身份校验失败，已停止，不填充其他岗位描述。')
                    self.enrich(job, safe, captured, redactions)
                    if not job.fields.get('jd'):
                        raise CollectorError('detail_mismatch', 'JD身份校验失败，已停止，不填充其他岗位描述。')
                elif result['status'] == 'already_captured':
                    job.fields.update(jd_status='captured', jd_error=None, jd_error_code=None)
                else:
                    job.fields.update(jd_status=result['status'], jd_error=result.get('message'), jd_error_code=result.get('code'))
                    if result['status'] == 'failed':
                        job.fields['jd_diagnostic'] = safe.get('detail_diagnostic')
        task.counts['jd_captured'] = sum(bool(j.fields.get('jd')) for j in task.jobs)
        task.message = f"自动读取JD：已处理{progress['processed']}/{progress['total']}个岗位，已获得{task.counts['jd_captured']}份描述。"
        task.page_snapshots.append({'source_layer': 'detail_dom', 'captured_at': captured.isoformat(),
                                    'redactions': redactions, 'purpose': 'automatic_detail', 'payload': safe})
        self.save(task)

    async def start_details(self, task_id: str) -> DirectSearchTask:
        async with self.lock:
            await self.get(task_id)
            task = self.tasks[task_id]
            if any(t.state != 'completed' for t in self.tasks.values()):
                raise CollectorError('busy', '已有读取任务，请等待结束或停止。')
            if not task.jobs:
                raise CollectorError('no_jobs', '该任务没有岗位，无法自动读取JD。')
            if task.outcome == 'platform_restricted':
                raise CollectorError('platform_restricted', '本任务已遇到平台访问限制，停止自动补齐，不重复尝试。')
            task.state, task.phase, task.outcome = 'running', 'details', None
            task.reason_code, task.message = '', '准备自动补齐本次已有岗位的JD，不重新检索列表。'
            task.counts.update(detail_total=len(task.jobs), detail_processed=0)
            self.stops[task.id] = asyncio.Event()
            self.save(task)
            self.workers[task.id] = asyncio.create_task(self.run_details(task))
            return task.model_copy(deep=True)

    async def run_details(self, task: DirectSearchTask):
        async def on_page(page):
            self.record_detail(task, page)
        jobs = [{'url': j.fields['url'], 'title': j.fields.get('title'), 'captured': bool(j.fields.get('jd'))}
                for j in task.jobs]
        await self.execute(task, self.adapter.collect_details(jobs, (task.source_page or {}).get('context', {}),
                           self.stops[task.id], on_page, task.counts.get('pages_read', 0)))

    async def refresh_current(self, task_id: str, job_id: str | None = None) -> DirectSearchTask:
        async with self.lock:
            await self.get(task_id)
            task = self.tasks[task_id]
            if task.state != 'completed' or any(t.state != 'completed' for t in self.tasks.values()):
                raise CollectorError('busy', '请等待当前列表读取结束，再刷新薪资或读取 JD。')
            job = next((j for j in task.jobs if j.id == job_id), None) if job_id else None
            if job_id and job is None:
                raise KeyError(job_id)
            context = (task.source_page or {}).get('context')
            page = await self.adapter.read_current(context)
            captured = datetime.now(timezone.utc)
            redactions: list[str] = []
            safe = scrub(page, redactions=redactions)
            detail = safe.get('detail')
            if job and not detail_matches(detail, job.fields.get('url')):
                raise CollectorError('detail_not_selected', '请先在专用 Chrome 搜索页中手动点开这个岗位，待职位描述显示后再读取；不会把其他岗位的 JD 混入。')
            if job:
                self.enrich(job, safe, captured, redactions)
            else:
                for item in task.jobs:
                    self.enrich(item, safe, captured, redactions)
            # 保留此次展示字段的字体依据与详情来源，不覆盖此前原始快照。
            task.page_snapshots.append({'source_layer': 'search_list_dom', 'captured_at': captured.isoformat(),
                'redactions': redactions, 'purpose': 'refresh_current', 'payload': safe})
            task.counts['jd_captured'] = sum(bool(j.fields.get('jd')) for j in task.jobs)
            task.message = f"已刷新当前页面中可匹配的薪资与职位描述；本任务共{len(task.jobs)}个岗位，已读取{task.counts['jd_captured']}个JD。未重新检索或遍历详情。"
            self.save(task)
            return task.model_copy(deep=True)

    async def get(self, task_id: str) -> DirectSearchTask:
        try:
            UUID(task_id)
        except ValueError:
            raise KeyError(task_id) from None
        if task_id not in self.tasks:
            path = self.root / f"{task_id}.json"
            if not path.is_file():
                raise KeyError(task_id)
            task = DirectSearchTask.model_validate_json(path.read_text(encoding="utf-8"))
            if task.state != "completed":
                task.state, task.outcome, task.reason_code = "completed", "unknown", "backend_restarted"
                task.message = "上次读取被服务重启中断，保留已有快照；不会自动继续滚动或重新采集。"
                self.save(task)
            if task.state == 'completed':
                task.phase = 'completed'
            self.tasks[task_id] = task
        return self.tasks[task_id].model_copy(deep=True)

    async def create(self, request: DirectSearchRequest) -> DirectSearchTask:
        async with self.lock:
            if any(t.state != "completed" for t in self.tasks.values()):
                raise CollectorError("busy", "已有 JobPilot 读取任务，请先等待结束或停止。")
            now = datetime.now(timezone.utc)
            task = DirectSearchTask(id=str(uuid4()), request=request, created_at=now, updated_at=now,
                                    counts={"pages_requested": request.max_pages, "pages_read": 0,
                                            "unique_jobs": 0, "duplicate_observations": 0})
            self.tasks[task.id], self.stops[task.id] = task, asyncio.Event()
            self.save(task)
            self.workers[task.id] = asyncio.create_task(self.run(task))
            return task.model_copy(deep=True)

    async def run(self, task: DirectSearchTask):
        known: set[str] = set()

        async def on_page(page: dict):
            if page.get('phase') == 'details':
                self.record_detail(task, page)
                return
            captured = datetime.now(timezone.utc)
            changes: list[str] = []
            safe_page = scrub(page, redactions=changes)
            task.source_page = {key: safe_page.get(key) for key in ("url", "title", "context")}
            task.page_snapshots.append({"source_layer": "search_list_dom", "captured_at": captured.isoformat(),
                                        "redactions": changes, "payload": safe_page})
            for card in safe_page.get("cards", []):
                if card["url"] in known:
                    task.counts["duplicate_observations"] += 1
                    continue
                known.add(card["url"])
                fields = {key: card.get("observed_fields", {}).get(key) for key in
                          ("title", "company", "salary", "location", "experience", "education", "tags", "url")}
                fields.update({key: None for key in ("city", "district", "company_size", "company_industry",
                                                    "hr_name", "hr_title", "hr_active", "jd", "responsibilities", "requirements")})
                fields["list_text"] = card.get("raw", {}).get("card_text")
                fields['jd_status'] = 'not_captured'
                raw = RawSnapshot(source_layer="search_list_dom", source="JobPilot / BOSS visible DOM",
                    endpoint="DOM:" + safe_page["url"], captured_at=captured,
                    payload={"batch": page["batch"], "card": card["raw"], "observed_fields": card.get("observed_fields", {})},
                    redactions=changes,
                    note="JobPilot 独立读取的搜索列表 DOM 原文，不是 BOSS 网络原始 JSON；这里的薪资仍为原值，展示字段可按已验证字体另行还原。JD 来源单独保存在 detail_raw。")
                job = JobView(id=hashlib.sha256(card["url"].encode()).hexdigest()[:24], fields=fields, raw=raw)
                self.enrich(job, safe_page, captured, changes)
                task.jobs.append(job)
            # 重复列表观察也可能包含用户当前打开的 JD。
            for job in task.jobs:
                self.enrich(job, safe_page, captured, changes)
            task.counts.update(pages_read=page["batch"], unique_jobs=len(task.jobs))
            task.counts['jd_captured'] = sum(bool(j.fields.get('jd')) for j in task.jobs)
            task.message = f"已读取{page['batch']}页/批次，{len(task.jobs)}个本次唯一岗位。"
            self.save(task)

        await self.execute(task, self.adapter.collect(task.request.max_pages, self.stops[task.id], on_page))

    async def execute(self, task: DirectSearchTask, operation):
        try:
            result = await operation
            task.outcome, task.reason_code, task.message = result["outcome"], result["reason_code"], result["message"]
        except asyncio.CancelledError:
            task.outcome, task.reason_code, task.message = "unknown", "backend_interrupted", "服务退出，读取中断；不会自动重启或继续滚动。"
            raise
        except CollectorError as exc:
            task.outcome = exc.code if exc.code in {"load_failed", "quota_exhausted", "platform_restricted", "page_changed", "stopped"} else "collector_error"
            if exc.code in {"source_unavailable", "no_search_tab", "ambiguous_tab"}:
                task.outcome = "source_unavailable"
            task.reason_code, task.message = exc.code, str(exc)
        except Exception:
            task.outcome, task.reason_code, task.message = "collector_error", "unexpected_error", "独立读取发生异常，已停止；没有自动重试或切换页面。"
        finally:
            task.state, task.phase = 'completed', 'completed'
            self.save(task)

    async def stop(self, task_id: str) -> DirectSearchTask:
        await self.get(task_id)
        task = self.tasks[task_id]
        if task.state != "completed":
            self.stops[task_id].set()
            task.state, task.message = "stopping", "已请求停止，等待当前DOM读取退出；不会关闭浏览器。"
            self.save(task)
        return task.model_copy(deep=True)
