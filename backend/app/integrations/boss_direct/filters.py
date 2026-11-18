"""BOSS原生条件：选项来自页面DOM和公开城市目录，不使用BossHunter实现。"""
from datetime import datetime, timezone
from itertools import product
import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.integrations.collector import CollectorError

FILTER_DOM_JS=Path(__file__).with_name('filter_dom.js').read_text(encoding='utf-8')
CITY_JS="""(async()=>{const r=await fetch('/wapi/zpCommon/data/city.json',{credentials:'omit'});
const d=await r.json();if(!r.ok||d.code!==0)return {ok:false};
return {ok:true,hot:(d.zpData?.hotCityList||[]).map(c=>({label:c.name,value:String(c.code)})),
provinces:(d.zpData?.cityList||[]).map(p=>({cities:(p.subLevelModelList||[]).map(c=>({label:c.name,value:String(c.code)}))}))};})()"""


async def read_catalog(cdp, region_city: str | None = None):
    dom=await cdp.evaluate(FILTER_DOM_JS)
    if not dom.get('available') or any(not dom['options'].get(k) for k in ('job_type','salary','experience')):
        raise CollectorError('filter_controls_unavailable','未识别BOSS原生筛选选项，请确认打开正常的搜索结果页。')
    result=await cdp.call('Runtime.evaluate',{'expression':CITY_JS,'returnByValue':True,'awaitPromise':True,'timeout':5000})
    city=result.get('result',{}).get('value',{})
    if not city.get('ok'):
        raise CollectorError('filter_controls_unavailable','无法读取公开城市目录；不携带登录凭证重试。')
    cities={c['value']:c for c in city.get('hot',[])}
    for province in city.get('provinces',[]):
        for c in province.get('cities',[]): cities[c['value']]=c
    if not cities:
        raise CollectorError('filter_controls_unavailable','公开城市目录为空，未生成虚假城市选项。')
    region_city=region_city or next((c['value'] for c in cities.values() if c['label']==dom['effective']['city']),None)
    if region_city not in cities:
        raise CollectorError('unsupported_filter','城市不在BOSS公开目录中。')
    regions=[]
    if region_city!='100010000':
        # 实际页面使用的公开区域目录；只导出行政区名/编码，不读取商圈坐标或凭证。
        expression="""(async()=>{const r=await fetch('/wapi/zpgeek/businessDistrict.json?cityCode='+CITY,{credentials:'omit'});
const d=await r.json(),b=d.zpData?.businessDistrict;
return {ok:r.ok&&d.code===0&&String(b?.code)===CITY,
regions:(b?.subLevelModelList||[]).map(n=>({label:n.name,value:String(n.code)}))};})()""".replace('CITY',json.dumps(region_city))
        result=await cdp.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True,'timeout':5000})
        area=result.get('result',{}).get('value',{})
        if not area.get('ok'):
            raise CollectorError('filter_controls_unavailable','无法读取所选城市的公开工作区域目录，未携带登录凭证重试。')
        regions=area['regions']
    return {'version':2,'cities':list(cities.values()),**dom['options'],'regions':regions,'region_city':region_city,
            'capabilities':{'experience':'native_multi','regions':'native_multi','salary':'split_single','job_type':'split_single'},
            'current':dom['effective'],
            'source':'BOSS public city metadata + current filter DOM',
            'captured_at':datetime.now(timezone.utc).isoformat()}


def resolve_filters(requested: dict, catalog: dict) -> dict:
    result={}
    for key,options_key in [('city','cities'),('job_type','job_type'),('salary','salary'),('experience','experience')]:
        values=as_values(requested[key])
        options=[]
        for value in values:
            option=next((o for o in catalog[options_key] if o['value']==value),None)
            if not option:
                raise CollectorError('unsupported_filter',f'筛选项{key}不在当前BOSS已确认选项中，请刷新选项后重新选择。')
            options.append(option)
        result[key]=combine_options(options)
    if 'regions' in catalog:
        if catalog['region_city']!=requested['city']:
            raise CollectorError('unsupported_filter','工作区域目录与城市不匹配，请刷新区域。')
        areas=[]
        for value in requested.get('regions',[]):
            option=next((o for o in catalog['regions'] if o['value']==value),None)
            if not option:raise CollectorError('unsupported_filter','工作区域不属于所选城市，未开始搜索。')
            areas.append(option)
        if requested['city']!='100010000' or areas:
            result['regions']=combine_options(areas)
    elif requested.get('regions'):
        raise CollectorError('unsupported_filter','没有已确认的工作区域目录。')
    return result


def as_values(value):
    return list(dict.fromkeys(value if isinstance(value,list) else [value]))


def combine_options(options):
    if len(options)==1:return options[0].copy()
    return {'label':' / '.join(o['label'] for o in options) or '不限',
            'value':','.join(o['value'] for o in options),
            'values':[o['value'] for o in options],'labels':[o['label'] for o in options]}


def filter_plan(requested: dict, catalog: dict):
    """薪资/求职类型为原生单选，串行组合取并集；经验/区域保持一次原生多选。"""
    aggregate=resolve_filters(requested,catalog)
    groups=[]
    for job_type,salary in product(as_values(requested['job_type']),as_values(requested['salary'])):
        groups.append(resolve_filters({**requested,'job_type':job_type,'salary':salary},catalog))
    return aggregate,groups


def search_url(current_url: str, resolved: dict) -> str:
    u=urlsplit(current_url)
    if u.scheme!='https' or not (u.hostname=='zhipin.com' or (u.hostname or '').endswith('.zhipin.com')):
        raise CollectorError('page_changed','仅允许从BOSS搜索页应用筛选。')
    old=parse_qs(u.query)
    # 只保留公开关键词，其余条件明确由本次四项配置取代，避免残留隐藏筛选。
    params={k:v for k,v in old.items() if k=='query'}
    for key,param in [('city','city'),('job_type','jobType'),('salary','salary'),('experience','experience')]:
        if resolved[key]['value']!='0': params[param]=[resolved[key]['value']]
    if resolved.get('regions',{}).get('value'):
        params['multiBusinessDistrict']=[resolved['regions']['value']]
    return urlunsplit(('https',u.hostname,'/web/geek/jobs',urlencode(params,doseq=True),''))


def verify_effective(page: dict, resolved: dict) -> bool:
    actual=page.get('native_filters') or {}
    values=page.get('native_filter_values',{})
    for key,option in resolved.items():
        if key in values:
            expected=option.get('values',[option['value']])
            if values[key] is None or set(values[key])!=set(expected):return False
        elif actual.get(key)!=option['label']:
            return False
    return True
