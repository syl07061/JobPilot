import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from app.api.search import get_service
from app.integrations.boss_direct.collector import DirectBossCollector
from app.integrations.boss_direct.cdp import ChromeDiscovery, PageCDP
from app.integrations.boss_direct.safety import BatchBudget, collector_lease
from app.integrations.boss_direct.salary import normalize, MIX_FONT, REGULAR_FONT
from app.integrations.boss_direct.identity import detail_matches
from app.integrations.collector import CollectorError
from app.main import app
from app.schemas.search import DirectSearchRequest
from app.services.direct_search import DirectSearchService


def page(*ids, **overrides):
    value = {"url": "https://www.zhipin.com/web/geek/jobs", "title": "Offline fixture",
             "context": {"query": ["fixture"]}, "restriction": None, "explicit_empty": False,
             "explicit_end": False, "ready_state": "complete", "scroll": {"x": 400, "y": 300, "distance": 900},
             "cards": [{"url": f"https://www.zhipin.com/job_detail/{i}.html",
                        "observed_fields": {"title": i, "salary": "\ue031-\ue032K", "tags": ["本科"],
                                            "url": f"https://www.zhipin.com/job_detail/{i}.html"},
                        "raw": {"card_text": f"{i} 原文", "fragments": [{"text": "本科"}], "links": []}} for i in ids]}
    return {**value, **overrides}


class Discovery:
    async def select(self):
        return {"url": "https://www.zhipin.com/web/geek/jobs", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"}


class Connection:
    def __init__(self, snapshots):
        self.snapshots, self.methods, self.closed = snapshots, [], False
        self.selected, self.selections = None, []

    async def __aenter__(self): return self
    async def __aexit__(self, *_): self.closed = True
    async def evaluate(self, _):
        value = deepcopy(self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0])
        if self.selected is not None and 'detail' not in value:
            value['detail'] = {**self.selected, 'jd': 'Offline JD ' + self.selected['title'], 'tags': []}
        return value
    async def select_job(self, job, context):
        self.selected = {'url': job['url'], 'title': job['title']}
        self.selections.append(job['url'])
    async def call(self, method, params):
        self.methods.append(method)
        assert method == "Input.synthesizeScrollGesture" and params["yDistance"] < 0
        return {}


def make_service(tmp_path, snapshots):
    connection = Connection(snapshots)
    collector = DirectBossCollector('http://127.0.0.1:9222', tmp_path/'budget', discovery=Discovery(),
                                    connection=lambda _: connection, delay=.001, initial_reads=1, detail_poll_interval=.001)
    return DirectSearchService(collector, tmp_path/'tasks'), connection


def test_single_batch_is_raw_and_does_not_navigate(tmp_path):
    async def scenario():
        service, c = make_service(tmp_path, [page('a', 'b')])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'collected' and len(result.jobs) == 2
        assert result.jobs[0].raw.source_layer == 'search_list_dom'
        assert result.jobs[0].raw.payload['card']['card_text'] == 'a 原文'
        assert result.jobs[0].fields['salary'] is None  # 未验证字体时不能猜测数字。
        assert result.jobs[0].fields['salary_raw'] == '\ue031-\ue032K'
        assert result.jobs[0].fields['jd'] == 'Offline JD a' and result.jobs[0].fields['salary_warning']
        assert result.counts['jd_captured'] == 2 and len(c.selections) == 2
        assert c.methods == [] and c.closed
    asyncio.run(scenario())


def test_new_batch_deduplicates_only_within_task(tmp_path):
    async def scenario():
        service, c = make_service(tmp_path, [page('a'), page('a'), page('a', 'b')])
        task = await service.create(DirectSearchRequest(max_pages=2))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'collected' and len(result.jobs) == 2
        assert result.counts['pages_read'] == 2 and result.counts['duplicate_observations'] == 1
        assert c.methods == ['Input.synthesizeScrollGesture']
        # 下一次任务仍会返回同一个岗位，不按历史排除。
        second = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[second.id]
        assert len((await service.get(second.id)).jobs) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('state,outcome', [
    (page(restriction='captcha'), 'platform_restricted'),
    (page(url='about:blank'), 'page_changed'),
    (page(explicit_empty=True), 'no_results'),
    (page(), 'load_failed'),
])
def test_failure_states_do_not_become_success_zero(tmp_path, state, outcome):
    async def scenario():
        service, c = make_service(tmp_path, [state])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == outcome and result.jobs == [] and c.methods == []
    asyncio.run(scenario())


def test_repeat_dom_is_not_a_second_page(tmp_path):
    async def scenario():
        service, c = make_service(tmp_path, [page('a')])
        task = await service.create(DirectSearchRequest(max_pages=2))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'load_failed' and len(result.jobs) == 1 and result.counts['pages_read'] == 1
        assert len(c.methods) == 3
    asyncio.run(scenario())


def test_conditions_change_stops_before_scroll(tmp_path):
    async def scenario():
        service, c = make_service(tmp_path, [page('a'), page('b', context={'query':['different']})])
        task = await service.create(DirectSearchRequest(max_pages=2))
        await service.workers[task.id]
        assert (await service.get(task.id)).outcome == 'page_changed' and c.methods == []
    asyncio.run(scenario())


def test_endpoint_accepts_only_pages(tmp_path):
    async def scenario():
        service, c = make_service(tmp_path, [page('a')])
        app.dependency_overrides[get_service] = lambda: service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8200') as client:
                for body in ({'max_pages':0}, {'max_pages':21}, {'max_pages':True}, {'max_pages':1, 'keyword':'ignored'}):
                    assert (await client.post('/search/tasks',json=body)).status_code == 422
                denied = await client.post('/search/tasks',json={'max_pages':1},headers={'Origin':'https://evil.invalid'})
                assert denied.status_code == 403
                response = await client.post('/search/tasks',json={'max_pages':1})
                assert response.status_code == 202
                task_id = response.json()['id']
                await service.workers[task_id]
                data = (await client.get('/search/tasks/'+task_id)).json()
                assert data['outcome'] == 'collected' and data['request'] == {'max_pages':1}
                assert 'native_task_id' not in data
        finally: app.dependency_overrides.clear()
    asyncio.run(scenario())


def test_local_budget_and_exclusive_lease(tmp_path):
    with collector_lease(tmp_path):
        ledger = BatchBudget(tmp_path, limit=1)
        ledger.reserve()
        with pytest.raises(CollectorError) as exc: ledger.reserve()
        assert exc.value.code == 'quota_exhausted'
        with pytest.raises(CollectorError):
            with collector_lease(tmp_path): pass


def test_websocket_protocol_does_not_enable_domains():
    class Socket:
        sent = []
        async def send(self, text): self.sent.append(json.loads(text))
        async def recv(self): return json.dumps({'id':self.sent[-1]['id'],'result':{'result':{'value':{'cards':[]}}}})
    async def scenario():
        c = PageCDP('unused')
        c.socket = Socket()
        assert await c.evaluate('read only expression') == {'cards':[]}
        assert [command['method'] for command in c.socket.sent] == ['Runtime.evaluate']
        assert 'contextId' not in c.socket.sent[0]['params']
    asyncio.run(scenario())


@pytest.mark.parametrize('targets,code', [
    ([], 'no_search_tab'),
    ([{'type': 'page', 'url': 'https://example.com'}], 'no_search_tab'),
    ([{'type': 'page', 'url': 'https://www.zhipin.com/web/geek/jobs'}] * 2, 'ambiguous_tab'),
    ([{'type': 'page', 'url': 'https://www.zhipin.com/web/geek/jobs',
       'webSocketDebuggerUrl': 'ws://other.invalid:9222/devtools/page/1'}], 'unsafe_endpoint'),
])
def test_discovery_does_not_choose_arbitrary_tabs(monkeypatch, targets, code):
    real_client = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append((request.method, str(request.url)))
        return httpx.Response(200, json=targets)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    async def scenario():
        with pytest.raises(CollectorError) as exc:
            await ChromeDiscovery('http://127.0.0.1:9222').select()
        assert exc.value.code == code
        assert requests == [('GET', 'http://127.0.0.1:9222/json/list')]
    asyncio.run(scenario())


def test_stopped_before_start_never_connects(tmp_path):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a')])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.stop(task.id)
        await service.workers[task.id]
        assert (await service.get(task.id)).outcome == 'stopped'
        assert not connection.closed and connection.methods == []
        assert not (tmp_path/'budget'/'daily_batches.json').exists()
    asyncio.run(scenario())


def test_explicit_end_preserves_partial_results_without_scroll(tmp_path):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a', explicit_end=True)])
        task = await service.create(DirectSearchRequest(max_pages=3))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'exhausted' and result.counts['pages_read'] == 1
        assert len(result.jobs) == 1 and connection.methods == []
    asyncio.run(scenario())


def test_corrupt_budget_is_not_reset(tmp_path):
    path = tmp_path/'daily_batches.json'
    path.write_text('[]', encoding='utf-8')
    with pytest.raises(CollectorError) as exc:
        BatchBudget(tmp_path).reserve()
    assert exc.value.code == 'budget_unreadable' and path.read_text() == '[]'


FONT_EVIDENCE = {'family': 'kanzhun-mix, kanzhun-Regular', 'resources': [REGULAR_FONT, MIX_FONT]}


def test_salary_mapping_is_verified_and_keeps_unknown_values_out():
    raw = '\ue033\ue031-\ue035\ue031K·\ue032\ue036薪'
    value, source = normalize(raw, FONT_EVIDENCE)
    assert value == '20-40K·15薪' and source['status'] == 'verified_font'
    assert normalize(raw, {})[0] is None
    assert normalize('\ue050K', FONT_EVIDENCE)[0] is None
    assert normalize('面议', {})[0] == '面议'
    assert normalize(None, {})[0] is None


def test_refresh_existing_task_salary_and_matching_jd(tmp_path):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a')])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        job = result.jobs[0]
        before_raw = job.raw.model_dump()
        detail = {'url': job.fields['url'], 'title': 'a', 'jd': '职责\n1. 开发 Python 服务\n要求\n本科', 'tags': ['Python']}
        connection.snapshots = [page('a', salary_font=FONT_EVIDENCE, detail=detail)]
        app.dependency_overrides[get_service] = lambda: service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8200') as client:
                response = await client.post(f'/search/tasks/{task.id}/jobs/{job.id}/detail')
                assert response.status_code == 200
                assert response.json()['jobs'][0]['detail_raw']['source_layer'] == 'detail_dom'
        finally:
            app.dependency_overrides.clear()
        refreshed = await service.get(task.id)
        current = refreshed.jobs[0]
        assert current.fields['salary'] == '0-1K' and current.fields['salary_raw'] == '\ue031-\ue032K'
        assert current.fields['jd'] == detail['jd'] and current.detail_raw.source_layer == 'detail_dom'
        assert current.raw.model_dump() == before_raw and refreshed.counts['jd_captured'] == 1
        assert connection.methods == []  # 刷新不导航、不点击、不滚动。
        # 新服务只读磁盘也能恢复增补字段，不依赖浏览器重新采集。
        loaded = await DirectSearchService(service.adapter, service.root).get(task.id)
        assert loaded.jobs[0].detail_raw.payload['jd'] == detail['jd']
    asyncio.run(scenario())


@pytest.mark.parametrize('overrides', [
    {'url': 'https://www.zhipin.com/job_detail/other.html'},
    {'active_job_urls': ['https://www.zhipin.com/job_detail/other.html']}, {'jd': ''},
])
def test_wrong_or_empty_detail_does_not_pollute_job(tmp_path, overrides):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a')])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        job = (await service.get(task.id)).jobs[0]
        detail = {'url': job.fields['url'], 'title': 'a', 'jd': 'JD', **overrides}
        connection.snapshots = [page('a', detail=detail)]
        with pytest.raises(CollectorError) as exc:
            await service.refresh_current(task.id, job.id)
        assert exc.value.code == 'detail_not_selected'
        assert (await service.get(task.id)).jobs[0].fields['jd'] == job.fields['jd']
    asyncio.run(scenario())


def test_same_url_and_active_card_allow_different_list_and_detail_titles(tmp_path):
    async def scenario():
        value = page('a')
        value['cards'][0]['observed_fields']['title'] = 'FDE/AI现场应用工程师-出差'
        url = value['cards'][0]['url']
        value['detail'] = {'url':url, 'title':'FDE/AI现场应用工程师', 'jd':'真实DOM中的职位职责和要求',
                           'active_job_urls':[url]}
        service, connection = make_service(tmp_path, [value])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        job = result.jobs[0]
        assert result.outcome == 'collected' and result.counts['jd_captured'] == 1
        assert job.fields['title'].endswith('-出差')
        assert job.fields['detail_title'] == 'FDE/AI现场应用工程师'
        assert job.fields['jd_title_differs'] is True
        assert job.detail_raw.payload['title'] == value['detail']['title']
        assert job.fields['jd_identity']['method'] == 'detail_url_and_active_card'
        assert connection.selections == []  # 当前面板已经是同一岗位，不再次点击。
        # 手动刷新、自动读取和任务事件使用同一身份判断规则。
        refreshed = await service.refresh_current(task.id, job.id)
        assert refreshed.jobs[0].fields['jd'] == value['detail']['jd']
    asyncio.run(scenario())


@pytest.mark.parametrize('active', [[], ['wrong'], ['same','wrong']])
def test_ambiguous_or_other_active_card_rejects_jd(active):
    assert not detail_matches({'url':'same','title':'Same title','jd':'JD','active_job_urls':active}, 'same')


def test_stale_detail_stops_after_one_click_without_wrong_assignment(tmp_path):
    async def scenario():
        stale = {'url': 'https://www.zhipin.com/job_detail/stale.html', 'title': 'Old', 'jd': 'Old JD'}
        service, connection = make_service(tmp_path, [page('a', 'b', detail=stale)])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'load_failed' and result.reason_code == 'load_failed'
        assert connection.selections == ['https://www.zhipin.com/job_detail/a.html']
        assert all(not job.fields['jd'] for job in result.jobs)
        assert result.jobs[0].fields['jd_status'] == 'failed'
    asyncio.run(scenario())


def test_captcha_after_click_stops_all_further_details(tmp_path):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a', 'b')])
        original = connection.select_job
        async def select(job, context):
            await original(job, context)
            connection.snapshots = [page('a', 'b', restriction='captcha')]
        connection.select_job = select
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'platform_restricted' and len(connection.selections) == 1
        assert len(result.jobs) == 2 and result.counts['jd_captured'] == 0
    asyncio.run(scenario())


def test_details_share_existing_daily_budget(tmp_path):
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    budget = tmp_path/'budget'
    budget.mkdir()
    (budget/'daily_batches.json').write_text(json.dumps({'date':today,'count':19}))
    async def scenario():
        service, connection = make_service(tmp_path, [page('a', 'b')])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'quota_exhausted' and len(result.jobs) == 2
        assert connection.selections == [] and result.counts['jd_captured'] == 0
        assert json.loads((budget/'daily_batches.json').read_text())['count'] == 20
    asyncio.run(scenario())


def test_stop_during_detail_wait_does_not_click_next_job(tmp_path):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a', 'b')])
        original = connection.select_job
        async def select(job, context):
            await original(job, context)
            next(iter(service.stops.values())).set()
        connection.select_job = select
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'stopped' and len(connection.selections) == 1
        assert len(result.jobs) == 2
    asyncio.run(scenario())


def test_auto_detail_endpoint_uses_existing_list_and_skips_captured_jd(tmp_path):
    async def scenario():
        service, connection = make_service(tmp_path, [page('a', 'b')])
        task = await service.create(DirectSearchRequest(max_pages=1))
        await service.workers[task.id]
        stored = service.tasks[task.id]
        original_raw = [j.raw.model_dump() for j in stored.jobs]
        stored.jobs[1].fields['jd'], stored.jobs[1].detail_raw = None, None
        stored.counts['jd_captured'] = 1
        connection.selected, connection.selections = None, []
        app.dependency_overrides[get_service] = lambda: service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8200') as client:
                response = await client.post(f'/search/tasks/{task.id}/auto-details')
                assert response.status_code == 202 and response.json()['phase'] == 'details'
                await service.workers[task.id]
                data = (await client.get(f'/search/tasks/{task.id}')).json()
                assert data['state'] == 'completed' and data['counts']['jd_captured'] == 2
        finally:
            app.dependency_overrides.clear()
        assert connection.selections == ['https://www.zhipin.com/job_detail/b.html']
        assert [j.raw.model_dump() for j in service.tasks[task.id].jobs] == original_raw
        assert connection.methods == []  # 没有再做列表翻页或重新检索。
    asyncio.run(scenario())
