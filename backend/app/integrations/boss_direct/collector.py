"""读取搜索列表后，串行正常点击岗位卡片，匹配并获取JD。"""
import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from app.integrations.collector import BrowserCollectorAdapter, CollectorError
from app.integrations.boss_direct.cdp import ChromeDiscovery, PageCDP, public_url
from app.integrations.boss_direct.safety import BatchBudget, collector_lease
from app.integrations.boss_direct.identity import detail_matches

SNAPSHOT_JS = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")


class DirectBossCollector(BrowserCollectorAdapter):
    def __init__(self, cdp_url: str, state_dir: Path, *, discovery=None, connection=None, delay=3.0, initial_reads=12, detail_poll_interval=1.0, daily_limit=20):
        self.discovery = discovery or ChromeDiscovery(cdp_url)
        self.connection = connection or PageCDP
        self.state_dir, self.delay, self.initial_reads = state_dir, delay, initial_reads
        self.detail_poll_interval = detail_poll_interval
        self.budget = BatchBudget(state_dir, limit=daily_limit)

    async def status(self):
        try:
            target = await self.discovery.select()
            return {"available": True, "chrome_available": True, "collector": "jobpilot_direct_cdp",
                    "source_page": {"url": public_url(target["url"]), "title": target.get("title", "")},
                    "message": "已找到手动打开的 BOSS 搜索页。点击后由 JobPilot 直接读取，不调用 BossHunter。"}
        except CollectorError as exc:
            return {"available": False, "collector": "jobpilot_direct_cdp", "code": exc.code, "message": str(exc)}

    async def read_current(self, context: dict | None = None):
        """用户显式刷新一次；不滚动、不点击，不进入其他岗位详情。"""
        with collector_lease(self.state_dir):
            target = await self.discovery.select()
            self.budget.reserve()
            async with self.connection(target['webSocketDebuggerUrl']) as cdp:
                page = await cdp.evaluate(SNAPSHOT_JS)
                self.guard(page, context)
                return page

    @staticmethod
    def guard(page: dict, context: dict | None):
        u = urlsplit(page.get("url", ""))
        if page.get("url") == "about:blank":
            raise CollectorError("page_changed", "选定标签页变成 about:blank，已停止，不尝试恢复或绕过。")
        if page.get("restriction"):
            raise CollectorError("platform_restricted", "当前页面出现登录、验证码或访问限制；已停止，不重复尝试。")
        if (u.scheme != "https" or not (u.hostname == "zhipin.com" or (u.hostname or "").endswith(".zhipin.com"))
                or u.path.rstrip("/") not in {"/web/geek/jobs", "/web/geek/job"}):
            raise CollectorError("page_changed", "页面离开了 BOSS 搜索结果，已停止，未切换到其他标签页。")
        if context is not None and page.get("context", {}) != context:
            raise CollectorError("page_changed", "手动搜索条件发生变化，已停止，避免混入其他检索结果。")
        if page.get("truncated"):
            raise CollectorError("page_too_large", "当前已加载超过300条岗位，请手动打开较少结果；不静默截断数据。")

    async def pause(self, stop: asyncio.Event, seconds: float):
        if stop.is_set():
            raise CollectorError("stopped", "用户已停止，保留已读取的结果。")
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            return
        raise CollectorError("stopped", "用户已停止，保留已读取的结果。")

    @staticmethod
    def matches_detail(page: dict, job: dict) -> bool:
        return detail_matches(page.get('detail'), job['url'])

    async def details_on_connection(self, cdp, jobs: list[dict], context: dict, stop: asyncio.Event,
                                    on_page, budget: BatchBudget, batch: int):
        """每个未获取JD的岗位最多点击一次；超时/访问限制立即结束整项任务。"""
        processed = 0
        total = len(jobs)

        async def report(page, job=None, status=None, code='', message=''):
            event = {key: page.get(key) for key in ('url', 'title', 'context')}
            event.update(phase='details', batch=batch, cards=[],
                         detail_progress={'total': total, 'processed': processed})
            if job:
                event['detail_result'] = {'url': job['url'], 'status': status, 'code': code, 'message': message}
                if status == 'failed':
                    detail = page.get('detail') or {}
                    event['detail_diagnostic'] = {'expected_url': job['url'], 'list_title': job.get('title'),
                        'observed_url': detail.get('url'), 'detail_title': detail.get('title'),
                        'description_chars': len(detail.get('jd') or ''),
                        'active_job_urls': detail.get('active_job_urls'), 'probe': page.get('detail_probe'),
                        'ready_state': page.get('ready_state')}
            if status == 'captured':
                event['detail'] = page['detail']
            await on_page(event)

        current = await cdp.evaluate(SNAPSHOT_JS)
        self.guard(current, context)
        await report(current)
        for job in jobs:
            if stop.is_set():
                raise CollectorError('stopped', '用户已停止自动JD读取，保留列表和已获取的描述。')
            if job.get('captured'):
                processed += 1
                await report(current, job, 'already_captured')
                continue
            try:
                await self.pause(stop, self.delay)
                current = await cdp.evaluate(SNAPSHOT_JS)
                self.guard(current, context)
                if not self.matches_detail(current, job):
                    budget.reserve()  # 列表、刷新、JD共用同一可配置额度和账本。
                    await report(current, job, 'reading')
                    await cdp.select_job(job, context)
                    # 点击后只等待同一面板更新；不会重复点击，也不复用旧岗位的JD。
                    for _ in range(self.initial_reads):
                        await self.pause(stop, self.detail_poll_interval)
                        current = await cdp.evaluate(SNAPSHOT_JS)
                        self.guard(current, context)
                        if self.matches_detail(current, job):
                            break
                    else:
                        raise CollectorError('load_failed', '该岗位点击后未确认匹配的JD加载完成，已停止，不重复点击或重试。')
                processed += 1
                await report(current, job, 'captured')
            except CollectorError as exc:
                await report(current, job, 'failed', exc.code, str(exc))
                raise
        return {'outcome': 'collected', 'reason_code': 'details_collected',
                'message': f'已完成{total}个岗位的JD读取；仅使用搜索页详情面板，没有沟通或投递操作。'}

    async def collect_details(self, jobs: list[dict], context: dict, stop: asyncio.Event, on_page, batch: int):
        if stop.is_set():
            raise CollectorError('stopped', '用户已停止自动JD读取。')
        with collector_lease(self.state_dir):
            target = await self.discovery.select()
            async with self.connection(target['webSocketDebuggerUrl']) as cdp:
                return await self.details_on_connection(cdp, jobs, context, stop, on_page,
                                                        self.budget, batch)

    async def collect(self, max_pages: int, stop: asyncio.Event, on_page):
        if stop.is_set():
            raise CollectorError("stopped", "用户已停止。")
        with collector_lease(self.state_dir):
            budget = self.budget
            target = await self.discovery.select()
            if stop.is_set():
                raise CollectorError("stopped", "用户已停止。")
            budget.reserve()  # 先确认预算，再建立页面连接；失败也不自动退回额度反复尝试。
            async with self.connection(target["webSocketDebuggerUrl"]) as cdp:
                page = None
                for _ in range(self.initial_reads):
                    if stop.is_set():
                        raise CollectorError("stopped", "用户已停止。")
                    page = await cdp.evaluate(SNAPSHOT_JS)
                    self.guard(page, None)
                    if page.get("cards") or page.get("explicit_empty"):
                        break
                    await self.pause(stop, 1)
                if not page.get("cards"):
                    if page.get("explicit_empty"):
                        await on_page({**page, "batch": 1})
                        return {"outcome": "no_results", "reason_code": "explicit_empty", "message": "页面明确显示没有匹配职位。"}
                    raise CollectorError("load_failed", "未确认岗位列表加载完成；不能把空壳或未识别结构算作正常零结果。")
                context = page.get("context", {})
                known = {card["url"] for card in page["cards"]}
                jobs = {card['url']: {'url': card['url'], 'title': card.get('observed_fields', {}).get('title'),
                         'captured': self.matches_detail(page, {'url': card['url'], 'title': card.get('observed_fields', {}).get('title')})}
                        for card in page['cards']}
                await on_page({**page, "batch": 1})
                pages_read = 1

                async def finish(outcome, reason, message):
                    await self.details_on_connection(cdp, list(jobs.values()), context, stop, on_page, budget, pages_read)
                    return {'outcome': outcome, 'reason_code': reason,
                            'message': message + f' 已完成{len(jobs)}个岗位的JD读取。'}

                for batch in range(2, max_pages + 1):
                    if stop.is_set():
                        raise CollectorError("stopped", "用户已停止，保留已读取的结果。")
                    if page.get("explicit_end"):
                        return await finish('exhausted', 'explicit_end', '页面明确没有更多职位，列表提前结束。')
                    budget.reserve()
                    loaded = None
                    # 最多三次普通滚动，绝不靠修改page参数、合成网站事件或重复导航推进。
                    for _ in range(3):
                        await self.pause(stop, self.delay)
                        current = await cdp.evaluate(SNAPSHOT_JS)
                        self.guard(current, context)
                        scroll = current.get("scroll")
                        if not scroll:
                            raise CollectorError("load_failed", "未找到可安全滚动的岗位区域，已停止。")
                        await cdp.call("Input.synthesizeScrollGesture", {"x": scroll["x"], "y": scroll["y"],
                            "yDistance": -min(1600, scroll["distance"]), "speed": 600})
                        await self.pause(stop, self.delay)
                        current = await cdp.evaluate(SNAPSHOT_JS)
                        self.guard(current, context)
                        if {card["url"] for card in current.get("cards", [])} - known:
                            loaded = current
                            break
                        if current.get("explicit_end"):
                            return await finish('exhausted', 'explicit_end', '页面明确没有更多职位，列表提前结束。')
                    if loaded is None:
                        raise CollectorError("load_failed", "滚动后未确认新增一批岗位；保留已读取结果，不将重复列表算作下一页。")
                    page = loaded
                    pages_read = batch
                    known.update(card["url"] for card in page["cards"])
                    for card in page['cards']:
                        job = jobs.setdefault(card['url'], {'url': card['url'], 'title': card.get('observed_fields', {}).get('title'), 'captured': False})
                        job['captured'] = job['captured'] or self.matches_detail(page, job)
                    await on_page({**page, "batch": batch})
                return await finish('collected', 'requested_batches_and_details_read', f'已读取{pages_read}页/批次。')
