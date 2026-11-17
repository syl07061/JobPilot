import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT
from app.integrations.bosshunter.adapter import BossHunterAdapter
from app.integrations.bosshunter.mapping import map_job, snapshot
from app.integrations.collector import CollectorError
from app.schemas.search import SearchRequest
from app.services.search import SearchService, outcome_for


def native_run(reason="", **counts):
    return {"id": "run-1", "task_id": "native-1", "status": "completed",
            "stop_reason": reason, "collected_job_ids": [],
            "options": {"platform_order": ["boss"], "auto_score": False,
                        "platforms": {"boss": {"keywords": ["Python"], "cities": ["上海"], "max_pages": 1}}},
            "platform_states": {"boss": {"status": "completed", "reason_code": reason, **counts}}}


@pytest.mark.parametrize("reason,counts,expected", [
    ("no_jobs_for_search", {}, "no_results"),
    ("", {"seen": 2, "duplicate": 2}, "duplicates_only"),
    ("", {"seen": 2, "duplicate": 1}, "unknown"),
    ("", {"seen": 2, "filtered": 2}, "filtered_only"),
    ("list_load_unconfirmed", {}, "load_failed"),
    ("cdp_empty_shell", {}, "load_failed"),
    ("consecutive_page_failures", {}, "load_failed"),
    ("daily_platform_page_limit", {}, "quota_exhausted"),
    ("daily_detail_page_limit", {}, "quota_exhausted"),
    ("captcha", {}, "platform_restricted"),
    ("login_required", {}, "platform_restricted"),
    ("rate_limit", {}, "platform_restricted"),
    ("", {"parse_failed": 1}, "collector_error"),
    ("no_jobs_extracted", {}, "unknown"),
])
def test_outcomes_are_distinct(reason, counts, expected):
    assert outcome_for(native_run(reason, **counts), None)[0] == expected


def test_partial_jobs_do_not_hide_quota_or_risk():
    run = native_run("daily_platform_page_limit")
    run["collected_job_ids"] = ["job-1"]
    assert outcome_for(run, None)[0] == "quota_exhausted"
    run["platform_states"]["boss"]["reason_code"] = ""
    run["stop_reason"] = ""
    assert outcome_for(run, None)[0] == "new_jobs"


@pytest.mark.parametrize("body", [
    {"keyword": "  ", "city": "上海"},
    {"keyword": "Python", "city": "上海", "max_pages": 0},
    {"keyword": "Python", "city": "上海", "max_pages": 11},
    {"keyword": "Python", "city": "上海", "max_pages": True},
    {"keyword": "Python", "city": "上海", "auto_score": True},
])
def test_input_validation(body):
    with pytest.raises(ValidationError):
        SearchRequest(**body)


def test_raw_preserves_nested_values_and_masks_credentials():
    raw = snapshot({"id": "a", "nested": {"labels": ["Python", {"token": "private"}]},
                    "url": "https://u:p@www.zhipin.com/job_detail/a.html?token=private#private",
                    "resume_path": "C:/private/resume.pdf", "jd": "Bearer private"}, "/api/jobs/a")
    assert raw.payload["nested"]["labels"][0] == "Python"
    assert raw.payload["nested"]["labels"][1]["token"] == "[REDACTED]"
    assert raw.payload["url"] == "https://www.zhipin.com/job_detail/a.html"
    assert "private" not in raw.model_dump_json()
    job = map_job({"id": "a", "title": "Engineer", "city": "上海", "jd": "Responsibilities and requirements", "company_size": ""}, "/api/jobs/a")
    assert job.fields["company_size"] is None and job.fields["district"] is None
    assert job.fields["requirements"] is None
    assert job.raw.source_layer == "local_api"


def mock_adapter(run, calls):
    def handler(request):
        path = request.url.path
        calls.append((request.method, path))
        if path == "/jobpilot/capabilities":
            return httpx.Response(200, json={"config_isolated": True, "collect_only": True, "source_config_unchanged": True, "runtime_connected": True})
        if path == "/api/config/cities":
            return httpx.Response(200, json={"上海": "101020100"})
        if path == "/api/workbench/task":
            body = json.loads(request.content)
            assert body["mode"] == "collect"
            opts = body["options"]
            assert opts["auto_score"] is False and opts["platform_order"] == ["boss"]
            search = opts["platforms"]["boss"]
            assert search["keywords"] == ["Python"] and search["cities"] == ["上海"]
            assert search["max_pages"] == 1 and search["filters"] == {}
            return httpx.Response(200, json={"id": "native-1", "mode": "collect", "status": "running"})
        if path == "/api/workbench":
            return httpx.Response(200, json={"task": None, "last_task": {"id": "native-1", "status": "completed", "progress": {"run_id": "run-1"}}})
        if path == "/api/collection/runs/run-1":
            return httpx.Response(200, json=run)
        if path == "/api/jobs/current-job":
            return httpx.Response(200, json={"id": "current-job", "title": "Offline test fixture", "jd": "JD", "url": "https://www.zhipin.com/job_detail/current.html"})
        raise AssertionError(f"Unexpected upstream request: {request.method} {path}")
    return BossHunterAdapter("http://127.0.0.1:8687", httpx.MockTransport(handler))


def test_legacy_http_adapter_pipeline_and_exact_membership(tmp_path):
    async def scenario():
        run = native_run(new=1, seen=1)
        run["collected_job_ids"] = ["current-job"]
        calls = []
        service = SearchService(mock_adapter(run, calls), tmp_path, poll_seconds=.001)
        # 旧协议仅作为回归保留，不再绑定当前只接受页数的HTTP入口。
        task = await service.create(SearchRequest(keyword="Python", city="上海"))
        await service.workers[task.id]
        task = (await service.get(task.id)).model_dump()
        assert task["outcome"] == "new_jobs" and task["native_run_id"] == "run-1"
        assert [j["id"] for j in task["jobs"]] == ["current-job"]
        assert task["jobs"][0]["raw"]["payload"]["jd"] == "JD"
        assert sum(method == "POST" for method, _ in calls) == 1
        assert ("GET", "/api/jobs") not in calls
    asyncio.run(scenario())


def test_duplicates_never_return_historical_jobs(tmp_path):
    async def scenario():
        calls = []
        service = SearchService(mock_adapter(native_run(seen=5, duplicate=5), calls), tmp_path)
        task = await service.create(SearchRequest(keyword="Python", city="上海"))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == "duplicates_only" and result.jobs == []
        assert not any(path.startswith('/api/jobs') for _, path in calls)
    asyncio.run(scenario())


def test_reject_mismatched_run_and_do_not_read_history(tmp_path):
    async def scenario():
        calls = []
        run = native_run()
        run["task_id"] = "different-task"
        service = SearchService(mock_adapter(run, calls), tmp_path)
        task = await service.create(SearchRequest(keyword="Python", city="上海"))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == "collector_error" and result.reason_code == "task_mismatch"
        assert result.jobs == []
    asyncio.run(scenario())


def test_plain_workbench_is_not_allowed_to_modify_user_config():
    async def scenario():
        calls = []
        def handler(req):
            calls.append(req.method)
            return httpx.Response(200, json={"tools": []})
        adapter = BossHunterAdapter('http://127.0.0.1:8686', httpx.MockTransport(handler))
        with pytest.raises(CollectorError):
            await adapter.start(SearchRequest(keyword="Python", city="上海"))
        assert calls == ['GET']
    asyncio.run(scenario())


def test_real_saved_sample_mapping():
    path = PROJECT_ROOT / 'data/samples/bosshunter_jobs.json'
    if not path.exists():
        pytest.skip('Run the historical local API sample capture; no real platform calls are made by tests.')
    sample = json.loads(path.read_text(encoding='utf-8'))
    assert sample['historical_sample'] is True
    for row in sample['jobs']:
        job = map_job(row, f"/api/jobs/{row['id']}")
        assert job.raw.payload['jd'] == row['jd'] and job.fields['jd'] == row['jd']
        assert job.raw.payload['company_size'] == row['company_size']
        assert job.fields['district'] is None


def test_compat_configuration_keeps_safety_and_drops_credentials():
    spec = importlib.util.spec_from_file_location('compat', PROJECT_ROOT / 'scripts/start_bosshunter_compat.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = {"ai": {"api_key": "private"}, "safety": {"daily_platform_page_limit": 6},
                "browser": {"enable_port_guard": False}, "collection": {"daily_detail_page_limit": 5},
                "profile": {"resume_path": "private", "salary_min": 15, "deal_breakers": ['996']}}
    config = module.public_config(original)
    assert config['safety'] == original['safety'] and config['collection']['daily_detail_page_limit'] == 5
    assert config['profile']['salary_min'] == 15 and 'ai' not in config
    assert 'resume_path' not in config['profile'] and original['profile']['resume_path'] == 'private'


def test_start_timeout_is_not_retried(tmp_path):
    async def scenario():
        posts = []
        def handler(req):
            if req.url.path == '/jobpilot/capabilities':
                return httpx.Response(200, json={'config_isolated': True, 'collect_only': True, 'source_config_unchanged': True, 'runtime_connected': True})
            if req.url.path == '/api/config/cities':
                return httpx.Response(200, json={'上海': '101020100'})
            if req.method == 'POST':
                posts.append(req.url.path)
                raise httpx.ReadTimeout('private upstream debug text')
            raise AssertionError(req.url)
        service = SearchService(BossHunterAdapter('http://127.0.0.1:8687', httpx.MockTransport(handler)), tmp_path)
        result = await service.create(SearchRequest(keyword='Python', city='上海'))
        assert result.reason_code == 'start_unconfirmed' and result.jobs == []
        assert 'private' not in result.model_dump_json()
        with pytest.raises(CollectorError):
            await service.create(SearchRequest(keyword='Python', city='上海'))
        assert posts == ['/api/workbench/task']
    asyncio.run(scenario())


def test_unknown_city_does_not_start_collection():
    async def scenario():
        calls = []
        adapter = mock_adapter(native_run(), calls)
        with pytest.raises(CollectorError) as exc:
            await adapter.start(SearchRequest(keyword='Python', city='unknown city'))
        assert exc.value.code == 'invalid_city'
        assert not any(method == 'POST' for method, _ in calls)
    asyncio.run(scenario())


def test_partial_results_preserved_when_quota_stops(tmp_path):
    async def scenario():
        run = native_run('daily_detail_page_limit', new=1, seen=2)
        run['collected_job_ids'] = ['current-job']
        service = SearchService(mock_adapter(run, []), tmp_path)
        task = await service.create(SearchRequest(keyword='Python', city='上海'))
        await service.workers[task.id]
        result = await service.get(task.id)
        assert result.outcome == 'quota_exhausted' and len(result.jobs) == 1
    asyncio.run(scenario())


def test_completed_snapshot_reloads_without_starting_task(tmp_path):
    async def scenario():
        service = SearchService(mock_adapter(native_run('no_jobs_for_search'), []), tmp_path)
        task = await service.create(SearchRequest(keyword='Python', city='上海'))
        await service.workers[task.id]
        calls = []
        restarted = SearchService(mock_adapter(native_run(), calls), tmp_path)
        result = await restarted.get(task.id)
        assert result.outcome == 'no_results' and calls == []
    asyncio.run(scenario())
