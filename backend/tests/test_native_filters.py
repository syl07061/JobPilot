import asyncio
from copy import deepcopy
import json
from urllib.parse import parse_qs,urlsplit

import httpx
import pytest
from app.api.search import get_service
from app.integrations.boss_direct.collector import DirectBossCollector,FILTER_PROBE_JS
from app.integrations.boss_direct.filters import FILTER_DOM_JS,resolve_filters,search_url,filter_plan,verify_effective
from app.main import app
from app.schemas.search import DirectSearchRequest,NativeFilters
from app.services.direct_search import DirectSearchService
from test_direct_search import Connection,Discovery,page

CATALOG={'cities':[{'value':'101020100','label':'上海'},{'value':'101010100','label':'北京'}],
 'job_type':[{'value':'0','label':'不限'},{'value':'1901','label':'全职'},{'value':'1903','label':'兼职'}],
 'salary':[{'value':'0','label':'不限'},{'value':'405','label':'10-20K'},{'value':'406','label':'20-50K'}],
 'experience':[{'value':'0','label':'不限'},{'value':'101','label':'经验不限'},{'value':'104','label':'1-3年'},{'value':'105','label':'3-5年'}],
 'regions':[{'value':'310115','label':'浦东新区'},{'value':'310110','label':'杨浦区'}],'region_city':'101020100'}
REQUEST={'city':'101020100','job_type':'1901','salary':'406','experience':'104'}
EFFECTIVE={'city':'上海','job_type':'全职','salary':'20-50K','experience':'1-3年','regions':'不限'}


class NativeConnection(Connection):
    def __init__(self,*,mismatch=False,empty=False):
        super().__init__([page('a')])
        self.navigations=[]
        self.job_reads_before_navigation=0
        self.mismatch,self.empty=mismatch,empty
    async def evaluate(self,expression):
        if expression==FILTER_DOM_JS:
            return {'available':True,'options':{k:CATALOG[k] for k in ('job_type','salary','experience')},'effective':EFFECTIVE}
        if expression!=FILTER_PROBE_JS and not self.navigations:
            self.job_reads_before_navigation+=1
        value=await super().evaluate(expression)
        if self.navigations:
            query=parse_qs(urlsplit(self.navigations[-1]).query)
            actual={'city':'上海'};codes={}
            for key,param in [('job_type','jobType'),('salary','salary'),('experience','experience'),('regions','multiBusinessDistrict')]:
                codes[key]=query.get(param,['' if key=='regions' else '0'])[0].split(',')
                if codes[key]==['']:codes[key]=[]
                actual[key]=' / '.join(next(o['label'] for o in CATALOG[key] if o['value']==v) for v in codes[key]) or '不限'
            if self.mismatch:actual['salary']='不限';codes['salary']=['0']
            value['native_filters']=actual;value['native_filter_values']=codes
            value['context']={**query,'native_filters':actual}
            if self.empty:
                value['cards'],value['explicit_empty']=[],True
        return value
    async def call(self,method,params):
        if method=='Runtime.evaluate':
            assert "credentials:'omit'" in params['expression']
            if 'businessDistrict.json' in params['expression']:
                return {'result':{'value':{'ok':True,'regions':CATALOG['regions']}}}
            return {'result':{'value':{'ok':True,'hot':CATALOG['cities'],'provinces':[]}}}
        if method=='Page.navigate':
            self.navigations.append(params['url'])
            return {'frameId':'fixture'}
        return await super().call(method,params)


def service_for(tmp_path,**options):
    connection=NativeConnection(**options)
    collector=DirectBossCollector('http://127.0.0.1:9222',tmp_path/'budget',discovery=Discovery(),
        connection=lambda _:connection,delay=.001,initial_reads=1,detail_poll_interval=.001)
    return DirectSearchService(collector,tmp_path/'tasks'),connection


def test_url_transmits_four_conditions_and_strips_old_filters_and_credentials():
    resolved=resolve_filters(REQUEST,CATALOG)
    url=search_url('https://www.zhipin.com/web/geek/jobs?query=AI%20Agent&token=private&degree=203&salary=405#private',resolved)
    assert parse_qs(urlsplit(url).query)=={'query':['AI Agent'],'city':['101020100'],'jobType':['1901'],'salary':['406'],'experience':['104']}
    assert 'private' not in url and 'degree' not in url
    unlimited=resolve_filters({**REQUEST,'job_type':'0','salary':'0','experience':'0'},CATALOG)
    assert parse_qs(urlsplit(search_url(url,unlimited)).query)=={'query':['AI Agent'],'city':['101020100']}


def test_filters_are_verified_before_collection_and_use_one_search_load(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**REQUEST)))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='collected' and len(result.jobs)==1
        assert result.applied_filters['verified'] and result.applied_filters['effective']==EFFECTIVE
        assert result.applied_filters['requested']['salary']['label']=='20-50K'
        assert len(c.navigations)==1 and result.counts['pages_read']==1
        assert c.job_reads_before_navigation==0
        assert json.loads((tmp_path/'budget/daily_batches.json').read_text())['count']==2  # 首批搜索+一个JD。
    asyncio.run(scenario())


@pytest.mark.parametrize('mismatch,empty,outcome',[(True,False,'filter_failed'),(False,True,'no_results')])
def test_filter_mismatch_never_becomes_success_zero(tmp_path,mismatch,empty,outcome):
    async def scenario():
        service,c=service_for(tmp_path,mismatch=mismatch,empty=empty)
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**REQUEST)))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome==outcome and not result.jobs and not c.selections
        assert len(c.navigations)==1
        assert result.applied_filters['verified']==(not mismatch)
    asyncio.run(scenario())


def test_unknown_option_does_not_navigate(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**{**REQUEST,'salary':'999'})))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='filter_failed' and not result.jobs and not c.navigations
    asyncio.run(scenario())


def test_http_options_and_filtered_task(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        app.dependency_overrides[get_service]=lambda:service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8200') as client:
                options=await client.get('/search/filter-options')
                assert options.status_code==200 and options.json()['cities']==CATALOG['cities']
                assert not (tmp_path/'budget/daily_batches.json').exists()
                bad=await client.post('/search/tasks',json={'max_pages':1,'filters':{**REQUEST,'city':'invalid'}})
                assert bad.status_code==422
                response=await client.post('/search/tasks',json={'max_pages':1,'filters':REQUEST})
                assert response.status_code==202
                await service.workers[response.json()['id']]
                result=(await client.get('/search/tasks/'+response.json()['id'])).json()
                assert result['applied_filters']['verified'] and result['request']['filters']=={**REQUEST,'regions':[]}
        finally:app.dependency_overrides.clear()
    asyncio.run(scenario())


def test_old_task_context_compatibility_and_new_task_filter_change_detection():
    legacy={'query':['Python']}
    state=page('a',context={**legacy,'native_filters':EFFECTIVE})
    DirectBossCollector.guard(state,legacy)
    from app.integrations.collector import CollectorError
    with pytest.raises(CollectorError) as exc:
        DirectBossCollector.guard(state,{**legacy,'native_filters':{**EFFECTIVE,'salary':'不限'}})
    assert exc.value.code=='page_changed'


def test_native_multi_experience_regions_and_single_select_combination_plan():
    requested={**REQUEST,'job_type':['1901','1903'],'salary':['405','406'],
               'experience':['101','104'],'regions':['310115','310110']}
    aggregate,groups=filter_plan(requested,CATALOG)
    assert len(groups)==4 and aggregate['experience']['label']=='经验不限 / 1-3年'
    assert [(g['job_type']['value'],g['salary']['value']) for g in groups]==[
        ('1901','405'),('1901','406'),('1903','405'),('1903','406')]
    for group in groups:
        params=parse_qs(urlsplit(search_url('https://www.zhipin.com/web/geek/jobs?query=AI&multiSubway=old',group)).query)
        assert params['experience']==['101,104'] and params['multiBusinessDistrict']==['310115,310110']
        assert 'multiSubway' not in params
        actual={'native_filters':{'city':'上海'},'native_filter_values':{
            'job_type':[group['job_type']['value']],'salary':[group['salary']['value']],
            'experience':['104','101'],'regions':['310110','310115']}}
        assert verify_effective(actual,group)  # DOM选项顺序与勾选顺序无关。
        actual['native_filter_values']['experience']=['104']
        assert not verify_effective(actual,group)  # 不能只应用多选的其中一项。


@pytest.mark.parametrize('change',[
    {'experience':[]},{'salary':['0','406']},{'experience':['101,104']},
    {'regions':['999']},{'regions':['310115']*10},
])
def test_invalid_multi_select_shape_is_rejected(change):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):NativeFilters(**{**REQUEST,**change})


def test_combination_budget_bound_and_region_city_membership():
    from pydantic import ValidationError
    from app.integrations.collector import CollectorError
    with pytest.raises(ValidationError):
        DirectSearchRequest(max_pages=6,filters=NativeFilters(**{**REQUEST,'job_type':['1901','1903'],'salary':['405','406']}))
    assert NativeFilters(**{**REQUEST,'experience':['101','104','101']}).experience==['101','104']
    with pytest.raises(CollectorError):
        resolve_filters({**REQUEST,'regions':['110101']},CATALOG)
    with pytest.raises(CollectorError):
        resolve_filters({**REQUEST,'city':'101010100','regions':['310115']},CATALOG)


def test_union_deduplicates_jobs_and_does_not_read_same_jd_again(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**{
            **REQUEST,'salary':['405','406'],'experience':['101','104'],'regions':['310115','310110']})))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='collected' and len(result.jobs)==1
        assert len(c.navigations)==2 and len(c.selections)==1
        assert result.counts['pages_read']==2 and result.counts['pages_requested']==2
        assert result.counts['groups_finished']==2 and len(result.search_groups)==2
        assert result.counts['duplicate_observations']==1 and result.counts['jd_captured']==1
        assert json.loads((tmp_path/'budget/daily_batches.json').read_text())['count']==3
    asyncio.run(scenario())


def test_quota_stops_remaining_groups_with_partial_results(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        service.adapter.budget.set_limit(2)  # 首组列表+JD恰好两次，次组不得导航。
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**{**REQUEST,'salary':['405','406']})))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='quota_exhausted' and len(result.jobs)==1
        assert result.jobs[0].fields['jd'] and len(c.navigations)==1
        assert result.search_groups[-1]['outcome']=='quota_exhausted'
    asyncio.run(scenario())


def test_filter_failure_stops_all_remaining_combinations(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path,mismatch=True)
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**{**REQUEST,'salary':['405','406']})))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='filter_failed' and not result.jobs and len(c.navigations)==1
        assert not c.selections and result.search_groups[0]['outcome']=='filter_not_applied'
    asyncio.run(scenario())


def test_region_options_endpoint_is_scoped_cached_and_free(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        app.dependency_overrides[get_service]=lambda:service
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8200') as client:
                response=await client.get('/search/filter-options?city=101020100')
                assert response.status_code==200 and response.json()['region_city']=='101020100'
                assert response.json()['regions']==CATALOG['regions']
                assert (await client.get('/search/filter-options?city=invalid')).status_code==422
                assert not (tmp_path/'budget/daily_batches.json').exists() and not c.navigations
                c.evaluate=lambda _:(_ for _ in ()).throw(AssertionError('should use cached metadata'))
                assert (await client.get('/search/filter-options?city=101020100')).status_code==200
        finally:app.dependency_overrides.clear()
    asyncio.run(scenario())


def test_all_combinations_explicitly_empty_is_no_results(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path,empty=True)
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**{**REQUEST,'salary':['405','406']})))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='no_results' and not result.jobs and len(c.navigations)==2
        assert [g['outcome'] for g in result.search_groups]==['no_results','no_results']
        assert not c.selections
    asyncio.run(scenario())


def test_platform_restriction_stops_all_remaining_groups(tmp_path):
    async def scenario():
        service,c=service_for(tmp_path)
        original=c.evaluate
        async def restricted(expression):
            value=await original(expression)
            if c.selections:value['restriction']='captcha'
            return value
        c.evaluate=restricted
        task=await service.create(DirectSearchRequest(max_pages=1,filters=NativeFilters(**{**REQUEST,'salary':['405','406']})))
        await service.workers[task.id]
        result=await service.get(task.id)
        assert result.outcome=='platform_restricted' and len(c.navigations)==1
        assert len(result.jobs)==1 and result.jobs[0].fields['jd'] is None
        assert result.search_groups[-1]['outcome']=='platform_restricted'
    asyncio.run(scenario())


def test_nationwide_does_not_require_inapplicable_district_control():
    catalog={**CATALOG,'cities':[{'label':'全国','value':'100010000'}],'regions':[],'region_city':'100010000'}
    resolved=resolve_filters({**REQUEST,'city':'100010000'},catalog)
    assert 'regions' not in resolved
    assert verify_effective({'native_filters':{**EFFECTIVE,'city':'全国'}},resolved)
