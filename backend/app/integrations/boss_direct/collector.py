"""读取搜索列表后，串行正常点击岗位卡片，匹配并获取JD。"""
import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from app.integrations.collector import BrowserCollectorAdapter, CollectorError
from app.integrations.boss_direct.cdp import ChromeDiscovery, PageCDP, public_url
from app.integrations.boss_direct.safety import BatchBudget, collector_lease
from app.integrations.boss_direct.identity import detail_matches
from app.integrations.boss_direct.filters import FILTER_DOM_JS, read_catalog, search_url, verify_effective, filter_plan
from contextlib import asynccontextmanager, nullcontext
import json

_SNAPSHOT_FUNCTION = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8").strip()
SNAPSHOT_JS = _SNAPSHOT_FUNCTION + '(false,' + FILTER_DOM_JS + ')'
FILTER_PROBE_JS = _SNAPSHOT_FUNCTION + '(true,' + FILTER_DOM_JS + ')'


class DirectBossCollector(BrowserCollectorAdapter):
    def __init__(self, cdp_url: str, state_dir: Path, *, discovery=None, connection=None, delay=3.0, initial_reads=12, detail_poll_interval=1.0, daily_limit=20):
        self.discovery = discovery or ChromeDiscovery(cdp_url)
        self.connection = connection or PageCDP
        self.state_dir, self.delay, self.initial_reads = state_dir, delay, initial_reads
        self.detail_poll_interval = detail_poll_interval
        self.budget = BatchBudget(state_dir, limit=daily_limit)

    async def filter_options(self, refresh=False, city=None):
        path=self.state_dir/(f'filter_options_{city}.json' if city else 'filter_options.json')
        if path.is_file() and not refresh:
            try:
                value=json.loads(path.read_text(encoding='utf-8'))
                if value.get('version')==2 and (not city or value.get('region_city')==city) and all(value.get(k) for k in ('cities','job_type','salary','experience')): return value
            except (ValueError,OSError): pass
        with collector_lease(self.state_dir):
            target=await self.discovery.select()
            async with self.connection(target['webSocketDebuggerUrl']) as cdp:
                page=await cdp.evaluate(FILTER_PROBE_JS)
                self.guard(page,None)
                value=await read_catalog(cdp,city)
            staged=path.with_suffix('.tmp')
            staged.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            staged.replace(path)
            return value

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
        actual_context=page.get('context',{})
        if context is not None and 'native_filters' not in context:
            actual_context={k:v for k,v in actual_context.items() if k!='native_filters'}
        elif context is not None and isinstance(context.get('native_filters'),dict) and isinstance(actual_context.get('native_filters'),dict):
            # 旧任务没有记录新增的区域回显；URL中原有条件仍按原规则严格比较。
            actual_context={**actual_context,'native_filters':{k:v for k,v in actual_context['native_filters'].items() if k in context['native_filters']}}
        if context is not None and actual_context != context:
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
        click_diagnostic=None

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
                        'ready_state': page.get('ready_state'),'visibility_state':page.get('visibility_state'),
                        'has_focus':page.get('has_focus'),'click':click_diagnostic}
            if status == 'captured':
                event['detail'] = page['detail']
            await on_page(event)

        current = await cdp.evaluate(SNAPSHOT_JS)
        self.guard(current, context)
        await report(current)
        for job in jobs:
            click_diagnostic=None
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
                    click_diagnostic=await cdp.select_job(job, context)
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

    @asynccontextmanager
    async def page_connection(self, target, shared=None):
        if shared is not None:
            yield shared[1]
        else:
            async with self.connection(target['webSocketDebuggerUrl']) as cdp:yield cdp

    async def collect(self, max_pages: int, stop: asyncio.Event, on_page, *, filters=None):
        if filters is None:
            return await self._collect_one(max_pages,stop,on_page)
        if stop.is_set():raise CollectorError('stopped','用户已停止。')
        with collector_lease(self.state_dir):
            if self.budget.status()['exhausted']:self.budget.reserve()
            target=await self.discovery.select()
            async with self.connection(target['webSocketDebuggerUrl']) as cdp:
                before=await cdp.evaluate(FILTER_PROBE_JS)
                self.guard(before,None)
                catalog=await read_catalog(cdp,filters['city'])
                requested,groups=filter_plan(filters,catalog)
                if len(groups)*max_pages>20:
                    raise CollectorError('unsupported_filter','本次组合的列表总批次不能超过20。')
                offset=0
                captured=set()
                context=None
                outcomes=[]
                for index,resolved in enumerate(groups,1):
                    if index>1:
                        await self.pause(stop,self.delay)
                        self.guard(await cdp.evaluate(FILTER_PROBE_JS),context)
                    local_pages=0
                    async def report(page):
                        nonlocal local_pages,context
                        event={**page,'group_index':index,'group_count':len(groups)}
                        if event.get('phase')=='filters':
                            event['filter_application']={**event['filter_application'],
                                'requested':requested,'active_group':resolved,'group_index':index,'group_count':len(groups)}
                        else:
                            if page.get('context') is not None:context=page['context']
                            if page.get('phase')!='details':local_pages=max(local_pages,page.get('batch',0))
                            event.update(batch=offset+page.get('batch',0),group_batch=page.get('batch',0))
                            result=page.get('detail_result') or {}
                            if result.get('status')=='captured':captured.add(result['url'])
                            for card in page.get('cards',[]):
                                if self.matches_detail(page,{'url':card['url']}):captured.add(card['url'])
                        await on_page(event)
                    try:
                        result=await self._collect_one(max_pages,stop,report,_session=(target,cdp),
                                                      _resolved=resolved,_captured=captured)
                    except CollectorError as exc:
                        await on_page({'phase':'group_result','group_index':index,'group_count':len(groups),
                                       'outcome':exc.code,'message':str(exc),'pages_read':local_pages})
                        raise
                    outcomes.append(result['outcome'])
                    await on_page({'phase':'group_result','group_index':index,'group_count':len(groups),
                                   **result,'pages_read':local_pages})
                    offset+=local_pages
                return {'outcome':'no_results' if all(o=='no_results' for o in outcomes) else 'collected',
                        'reason_code':'filter_groups_completed',
                        'message':f'已完成{len(groups)}组原生检索，共{offset}批列表；岗位取并集并按链接去重，JD沿用本次已读取内容。'}

    async def _collect_one(self, max_pages: int, stop: asyncio.Event, on_page, *, _session=None, _resolved=None, _captured=None):
        if stop.is_set():
            raise CollectorError("stopped", "用户已停止。")
        with (nullcontext() if _session is not None else collector_lease(self.state_dir)):
            budget = self.budget
            target = _session[0] if _session is not None else await self.discovery.select()
            if stop.is_set():
                raise CollectorError("stopped", "用户已停止。")
            budget.reserve()  # 先确认预算，再建立页面连接；失败也不自动退回额度反复尝试。
            async with self.page_connection(target,_session) as cdp:
                resolved=_resolved
                captured=_captured if _captured is not None else set()
                if resolved is not None:
                    before=await cdp.evaluate(FILTER_PROBE_JS)
                    self.guard(before,None)
                    await on_page({**before,'cards':[],'phase':'filters','batch':0,
                                   'filter_application':{'requested':resolved,'verified':False}})
                    destination=search_url(target['url'],resolved)
                    if stop.is_set():
                        raise CollectorError('stopped','用户已停止，未应用筛选或读取岗位。')
                    response=await cdp.call('Page.navigate',{'url':destination})
                    if response.get('errorText'):
                        raise CollectorError('load_failed','原生筛选搜索页导航失败，未开始读取岗位。')
                    await self.pause(stop,self.delay)
                page = None
                for _ in range(self.initial_reads):
                    if stop.is_set():
                        raise CollectorError("stopped", "用户已停止。")
                    page = await cdp.evaluate(SNAPSHOT_JS)
                    self.guard(page, None)
                    if resolved is not None and not verify_effective(page,resolved):
                        await self.pause(stop,1)
                        continue
                    if page.get("cards") or page.get("explicit_empty"):
                        break
                    await self.pause(stop, 1)
                if resolved is not None:
                    if not verify_effective(page,resolved):
                        await on_page({**page,'cards':[],'phase':'filters','batch':0,
                                       'filter_application':{'requested':resolved,'effective':page.get('native_filters'), 'verified':False}})
                        raise CollectorError('filter_not_applied','BOSS页面实际选中项未匹配本组条件，未读取岗位；请检查筛选选项或页面状态。')
                    await on_page({**page,'cards':[],'phase':'filters','batch':0,
                                   'filter_application':{'requested':resolved,'effective':page['native_filters'],'verified':True}})
                if not page.get("cards"):
                    if page.get("explicit_empty"):
                        await on_page({**page, "batch": 1})
                        return {"outcome": "no_results", "reason_code": "explicit_empty", "message": "页面明确显示没有匹配职位。"}
                    raise CollectorError("load_failed", "未确认岗位列表加载完成；不能把空壳或未识别结构算作正常零结果。")
                context = page.get("context", {})
                known = {card["url"] for card in page["cards"]}
                jobs = {card['url']: {'url': card['url'], 'title': card.get('observed_fields', {}).get('title'),
                         'captured': card['url'] in captured or self.matches_detail(page, {'url': card['url'], 'title': card.get('observed_fields', {}).get('title')})}
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
                        job['captured'] = job['captured'] or card['url'] in captured or self.matches_detail(page, job)
                    await on_page({**page, "batch": batch})
                return await finish('collected', 'requested_batches_and_details_read', f'已读取{pages_read}页/批次。')
