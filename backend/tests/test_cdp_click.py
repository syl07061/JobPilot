"""真实标签激活、绘制同步以及不满足前置条件时绝不点击的回归。"""
import asyncio

import pytest

from app.integrations.boss_direct.cdp import PAINT_READY_JS, PageCDP
from app.integrations.collector import CollectorError


class ClickConnection(PageCDP):
    def __init__(self, visibility=(True,True), target_ready=True):
        super().__init__('offline')
        self.commands=[]
        self.visibility=iter(visibility)
        self.target_ready=target_ready

    async def call(self, method, params=None):
        self.commands.append((method,params))
        if method=='Runtime.evaluate':
            expression=params['expression']
            if expression==PAINT_READY_JS:
                assert params['awaitPromise'] is True
                return {'result':{'value':{'visible':next(self.visibility),'has_focus':True}}}
            if '"scroll": true' in expression:
                return {'result':{'value':{'status':'scrolled'}}}
            return {'result':{'value':{'status':'ready' if self.target_ready else 'card_not_found',
                                      'x':120,'y':240,'hit':{'tag':'SPAN','classes':['job-salary']}}}}
        return {}


JOB={'url':'https://www.zhipin.com/job_detail/offline.html','title':'offline'}


def test_click_activates_real_page_and_waits_for_paint_after_scroll():
    async def scenario():
        connection=ClickConnection()
        diagnostic=await connection.select_job(JOB,{})
        commands=connection.commands
        assert commands[0][0]=='Page.bringToFront'
        pointer=[params for method,params in commands if method=='Input.dispatchMouseEvent']
        assert [p['type'] for p in pointer]==['mousePressed','mouseReleased']
        assert all(p['x']==120 and p['y']==240 for p in pointer)
        assert diagnostic['visible_before_click'] and diagnostic['target_url']==JOB['url']
        assert diagnostic['hit']['classes']==['job-salary']
        assert sum(params.get('expression')==PAINT_READY_JS for method,params in commands if method=='Runtime.evaluate')==2
        assert not any(method.startswith('Emulation.') for method,_ in commands)
    asyncio.run(scenario())


@pytest.mark.parametrize('visibility',[(False,),(True,False)])
def test_hidden_page_or_page_hidden_during_scroll_is_never_clicked(visibility):
    async def scenario():
        connection=ClickConnection(visibility)
        with pytest.raises(CollectorError) as error:
            await connection.select_job(JOB,{})
        assert error.value.code=='page_not_visible'
        assert not any(method.startswith('Input.') for method,_ in connection.commands)
    asyncio.run(scenario())


def test_disappeared_card_after_paint_is_not_clicked():
    async def scenario():
        connection=ClickConnection(target_ready=False)
        with pytest.raises(CollectorError) as error:
            await connection.select_job(JOB,{})
        assert error.value.code=='detail_not_found'
        assert not any(method.startswith('Input.') for method,_ in connection.commands)
    asyncio.run(scenario())
