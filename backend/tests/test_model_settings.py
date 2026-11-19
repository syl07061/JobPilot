import asyncio
import json

import httpx
import pytest

from app.api.settings import get_model_store
from app.core.config import Settings
from app.main import app
from app.services.model_settings import ModelSettingsStore,ModelSettingsUpdate


def store(tmp_path,**defaults):
    return ModelSettingsStore(tmp_path/'model.json',Settings(_env_file=None,ai_base_url='',ai_api_key='',ai_model='',**defaults))


def test_key_preserved_replaced_cleared_and_survives_restart(tmp_path):
    s=store(tmp_path)
    value=s.update(ModelSettingsUpdate(base_url='https://models.example/v1/',model=' test-model ',api_key='test-only-value'))
    assert value['configured'] and value['base_url']=='https://models.example/v1'
    assert 'test-only-value' not in json.dumps(value) and 'api_key' not in value
    restarted=ModelSettingsStore(s.path,s.defaults)
    assert restarted.get()==value
    restarted.update(ModelSettingsUpdate(base_url=value['base_url'],model='other-model',api_key=''))
    assert json.loads(s.path.read_text())['api_key']=='test-only-value'
    restarted.update(ModelSettingsUpdate(base_url=value['base_url'],api_key='replacement-fixture'))
    assert json.loads(s.path.read_text())['api_key']=='replacement-fixture'
    value=restarted.update(ModelSettingsUpdate(base_url=value['base_url'],clear_api_key=True))
    assert not value['has_api_key'] and not value['configured']


def test_environment_fallback_and_explicit_local_clear(tmp_path):
    defaults=Settings(_env_file=None,ai_base_url='https://models.example/v1',ai_model='fixture-model',ai_api_key='env-fixture')
    s=ModelSettingsStore(tmp_path/'model.json',defaults)
    assert s.get()['source']=='environment' and s.get()['has_api_key']
    s.update(ModelSettingsUpdate(base_url='https://models.example/v1',model='fixture-model',clear_api_key=True))
    assert not ModelSettingsStore(s.path,defaults).get()['has_api_key']


def test_corrupt_configuration_is_not_silently_overwritten(tmp_path):
    s=store(tmp_path);s.path.write_text('{broken',encoding='utf-8')
    with pytest.raises(RuntimeError):s.update(ModelSettingsUpdate(base_url=''))
    assert s.path.read_text()=='{broken'


@pytest.mark.parametrize('url',['ftp://example.com','https://user:private@example.com','https://example.com?key=private','https://example.com#private','not-a-url'])
def test_bad_urls_rejected(url):
    with pytest.raises(ValueError):ModelSettingsUpdate(base_url=url)


def test_api_redacts_key_even_in_validation_errors_and_blocks_cross_origin(tmp_path):
    async def scenario():
        s=store(tmp_path)
        app.dependency_overrides[get_model_store]=lambda:s
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8200') as client:
                assert (await client.get('/settings/model')).json()['has_api_key'] is False
                response=await client.put('/settings/model',json={'base_url':'https://models.example/v1','model':'fixture','api_key':'private-test-value'})
                assert response.status_code==200 and response.json()['configured']
                assert 'private-test-value' not in response.text
                assert 'private-test-value' not in (await client.get('/settings/model')).text
                for change in ({'api_key':{'nested':'private-test-value'}},{'base_url':'https://private-test-value@example.com'}, {'api_key':'private-test-value','clear_api_key':True}):
                    response=await client.put('/settings/model',json={'base_url':'',**change})
                    assert response.status_code==422 and 'private-test-value' not in response.text
                response=await client.put('/settings/model',headers={'Origin':'https://unrelated.example'},json={'base_url':'','clear_api_key':True})
                assert response.status_code==403 and s.get()['has_api_key']
                assert not list(tmp_path.glob('*.tmp'))
        finally:app.dependency_overrides.clear()
    asyncio.run(scenario())
