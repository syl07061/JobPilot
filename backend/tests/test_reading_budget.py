import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from pydantic import ValidationError

from app.api.search import get_budget
from app.core.config import Settings
from app.integrations.boss_direct.cdp import ChromeDiscovery
from app.integrations.boss_direct.safety import BatchBudget, collector_lease
from app.integrations.collector import CollectorError
from app.main import app


def ledger(root, used, date=None):
    root.mkdir(parents=True, exist_ok=True)
    path = root/'daily_batches.json'
    path.write_text(json.dumps({'date': date or BatchBudget.now().date().isoformat(), 'count':used}), encoding='utf-8')
    return path


def test_limit_change_keeps_usage_and_is_shared_by_existing_instances(tmp_path):
    path = ledger(tmp_path, 20)
    before = path.read_bytes()
    budget = BatchBudget(tmp_path)
    other = BatchBudget(tmp_path)
    assert budget.status()['remaining'] == 0
    status = budget.set_limit(80)
    assert status['daily_limit'] == 80 and status['used'] == 20 and status['remaining'] == 60
    assert path.read_bytes() == before
    with collector_lease(tmp_path):
        other.reserve()
    assert other.status()['used'] == 21 and other.status()['remaining'] == 59
    budget.set_limit(5)
    assert other.status()['used'] == 21 and other.status()['remaining'] == 0
    with pytest.raises(CollectorError) as exc:
        with collector_lease(tmp_path): other.reserve()
    assert exc.value.code == 'quota_exhausted' and '5' in str(exc.value) and '21' in str(exc.value)


def test_quota_query_is_read_only_and_rolls_over_at_china_midnight(tmp_path, monkeypatch):
    china = timezone(timedelta(hours=8))
    path = ledger(tmp_path, 12, '2026-11-17')
    before = path.read_bytes()
    monkeypatch.setattr(BatchBudget, 'now', staticmethod(lambda: datetime(2026,11,18,0,0,tzinfo=china)))
    status = BatchBudget(tmp_path, 90).status()
    assert status['used'] == 0 and status['remaining'] == 90
    assert status['reset_at'] == '2026-11-19T00:00:00+08:00'
    assert path.read_bytes() == before
    assert not (tmp_path/'reading_settings.json').exists()
    empty = tmp_path/'absent'
    assert BatchBudget(empty).status()['remaining'] == 20 and not empty.exists()


def test_cannot_change_limit_during_collection(tmp_path):
    budget = BatchBudget(tmp_path)
    with collector_lease(tmp_path):
        with pytest.raises(CollectorError) as exc:
            budget.set_limit(80)
        assert exc.value.code == 'busy'
    assert not budget.settings_path.exists()


@pytest.mark.parametrize('value', [[], {'count': -1}, {'date':'not-a-date', 'count':2}, {'date':'2026-11-18', 'count':True}])
def test_invalid_counter_is_not_hidden_or_reset(tmp_path, value):
    path = tmp_path/'daily_batches.json'
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(CollectorError) as exc: BatchBudget(tmp_path).status()
    assert exc.value.code == 'budget_unreadable' and path.read_bytes() == before


def test_corrupt_config_does_not_fall_back_to_twenty(tmp_path):
    (tmp_path/'reading_settings.json').write_text('{broken')
    with pytest.raises(CollectorError) as exc: BatchBudget(tmp_path, 80).status()
    assert exc.value.code == 'budget_unreadable'


def test_environment_default_and_saved_setting_priority(tmp_path):
    settings = Settings(_env_file=None, collector_daily_limit=75)
    budget = BatchBudget(tmp_path, settings.collector_daily_limit)
    assert budget.status()['daily_limit'] == 75
    budget.set_limit(90)
    assert BatchBudget(tmp_path, 15).status()['daily_limit'] == 90
    with pytest.raises(ValidationError): Settings(_env_file=None, collector_daily_limit=0)


def test_budget_api_validation_and_no_browser_access(tmp_path, monkeypatch):
    budget = BatchBudget(tmp_path, 45)
    path = ledger(tmp_path, 20)
    before = path.read_bytes()
    async def forbidden(*_): raise AssertionError('Budget API must not access Chrome')
    monkeypatch.setattr(ChromeDiscovery, 'select', forbidden)
    async def scenario():
        app.dependency_overrides[get_budget] = lambda: budget
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8200') as client:
                data = (await client.get('/search/budget')).json()
                assert data['remaining'] == 25 and data['used'] == 20
                for value in (0, -1, True, 1.5, '80'):
                    assert (await client.put('/search/budget', json={'daily_limit':value})).status_code == 422
                assert (await client.put('/search/budget', json={'daily_limit':80,'reset':True})).status_code == 422
                assert (await client.put('/search/budget', json={'daily_limit':80}, headers={'Origin':'https://evil.invalid'})).status_code == 403
                response = await client.put('/search/budget', json={'daily_limit':80})
                assert response.status_code == 200 and response.json()['remaining'] == 60
                assert path.read_bytes() == before
                with collector_lease(tmp_path):
                    assert (await client.put('/search/budget', json={'daily_limit':90})).status_code == 409
                path.write_text('[]')
                assert (await client.get('/search/budget')).status_code == 503
        finally: app.dependency_overrides.clear()
    asyncio.run(scenario())
