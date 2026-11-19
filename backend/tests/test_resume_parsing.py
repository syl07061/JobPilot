import asyncio,json
from io import BytesIO
import zipfile

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
from sqlalchemy import select,func
from sqlalchemy.orm import Session

from app.api.resumes import get_resume_parser
from app.main import app
from app.services.resumes import ResumeStore
from app.services.resume_parsing import ResumeParser,Profile,ParseError,CurrentProfile,ParseAttempt,extract_text,call_model

TEXT='Fixture Person is a Python engineer. Experience includes FastAPI, SQLAlchemy and backend testing.'
def word():
    buf=BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('[Content_Types].xml','wordprocessingml.document.main+xml')
        z.writestr('word/document.xml',f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{TEXT}</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table skill</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return buf.getvalue()

def pdf(blank=False):
    writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
    if not blank:
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 40 700 Td ('+TEXT+') Tj ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out);return out.getvalue()

class Config:
    def credentials(self):return {'base_url':'https://model.example/v1','model':'fixture','api_key':'private-fixture-key'}

async def success(config,document):
    assert 'Python' in document
    return Profile(name='Fixture Person',skills=['Python','FastAPI'])

def setup(tmp_path,model=success):
    store=ResumeStore(tmp_path);store.save('fixture.docx',word())
    return store,ResumeParser(store,Config(),model)

def test_pdf_and_docx_text_with_tables(tmp_path):
    for name,data in [('fixture.pdf',pdf()),('fixture.docx',word())]:
        path=tmp_path/name;path.write_bytes(data)
        assert TEXT in extract_text(path)
        if name.endswith('.docx'):assert 'Table skill' in extract_text(path)

def test_blank_pdf_and_legacy_doc_fail_without_ocr(tmp_path):
    path=tmp_path/'blank.pdf';path.write_bytes(pdf(True))
    with pytest.raises(ParseError) as error:extract_text(path)
    assert error.value.code=='text_unavailable'
    with pytest.raises(ParseError) as error:extract_text(tmp_path/'fixture.doc')
    assert error.value.code=='doc_conversion_required'

def test_only_one_active_profile_is_replaced_and_persisted(tmp_path):
    async def scenario():
        store,parser=setup(tmp_path)
        assert (await parser.start())['attempt']['state']=='running'
        await parser.worker
        assert parser.view()['profile']['version']==1 and parser.view()['is_current']
        store.save('new.docx',word())
        assert not parser.view()['is_current'] and parser.view()['profile']['version']==1
        await parser.start();await parser.worker
        assert ResumeParser(store,Config()).view()['profile']['version']==2
        with Session(store.engine) as s:assert s.scalar(select(func.count()).select_from(CurrentProfile))==1
        assert len(store.list())==2
        store.engine.dispose()
    asyncio.run(scenario())

def test_failure_keeps_previous_profile_and_newer_upload_discards_inflight_result(tmp_path):
    async def scenario():
        store,parser=setup(tmp_path);await parser.start();await parser.worker
        original=parser.view()['profile']
        store.save('new.docx',word())
        async def fail(*_):raise ParseError('model_timeout','模型超时')
        parser.model_call=fail;await parser.start();await parser.worker
        assert parser.view()['profile']==original and parser.view()['attempt']['code']=='model_timeout'
        entered,release=asyncio.Event(),asyncio.Event()
        async def delayed(*args):entered.set();await release.wait();return await success(*args)
        parser.model_call=delayed;await parser.start();await entered.wait()
        with pytest.raises(ParseError) as error:await parser.start()
        assert error.value.code=='busy'
        store.save('latest.docx',word());release.set();await parser.worker
        assert parser.view()['profile']==original and parser.view()['attempt']['code']=='newer_resume_uploaded'
        store.engine.dispose()
    asyncio.run(scenario())

def test_restart_marks_interruption_without_overwriting_profile(tmp_path):
    store,_=setup(tmp_path)
    with Session(store.engine) as s:s.add(ParseAttempt(id=1,job_id='fixture',resume_id='fixture',version=1,state='running'));s.commit()
    parser=ResumeParser(store,Config())
    assert parser.view()['attempt']['code']=='interrupted' and parser.view()['profile'] is None
    store.engine.dispose()

@pytest.mark.parametrize('status,output,code',[
    (401,{},'model_http_error'),(429,{},'model_http_error'),
    (404,{'error':{'code':'ModelNotOpen','message':'account private-fixture-key has not activated model'}},'model_not_open'),
    (404,{'error':{'code':'InvalidEndpointOrModel.ModelIDAccessDisabled','message':'private-fixture-key'}},'model_endpoint_required'),
    (404,{'error':{'code':'InvalidEndpointOrModel.NotFound','message':'private-fixture-key'}},'model_not_accessible'),
    (200,{'choices':[{'finish_reason':'length','message':{'content':'{}'}}]},'model_invalid_output'),
    (200,{'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]},'model_invalid_output'),
    (200,{'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]},'model_invalid_output'),
])
def test_model_errors_are_sanitized_and_not_retried(monkeypatch,status,output,code):
    real=httpx.AsyncClient;calls=[]
    def handler(request):calls.append(request);return httpx.Response(status,json={**output,'private':'private-fixture-key'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    async def scenario():
        with pytest.raises(ParseError) as error:await call_model(Config().credentials(),TEXT)
        assert error.value.code==code and 'private-fixture-key' not in str(error.value)
        assert len(calls)==1
    asyncio.run(scenario())

def test_ark_request_and_valid_json(monkeypatch):
    real=httpx.AsyncClient;calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':Profile(skills=['Python']).model_dump_json()}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    async def scenario():
        config={**Config().credentials(),'base_url':'https://ark.cn-beijing.volces.com/api/v3'}
        result=await call_model(config,TEXT)
        assert result.skills==['Python'] and len(calls)==1
        request=calls[0];payload=json.loads(request.content)
        assert str(request.url)=='https://ark.cn-beijing.volces.com/api/v3/chat/completions'
        assert payload['thinking']=={'type':'disabled'} and payload['messages'][1]['content']==TEXT
        assert 'private-fixture-key' not in request.content.decode()
    asyncio.run(scenario())

def test_parse_api_is_explicit_and_profile_get_does_not_call_model(tmp_path):
    async def scenario():
        store,parser=setup(tmp_path)
        app.dependency_overrides[get_resume_parser]=lambda:parser
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8200') as client:
                assert (await client.get('/resumes/profile')).json()['profile'] is None and parser.worker is None
                result=await client.post('/resumes/parse-latest');assert result.status_code==202
                await parser.worker
                body=(await client.get('/resumes/profile')).json()
                assert body['profile']['data']['skills']==['Python','FastAPI']
                assert 'private-fixture-key' not in json.dumps(body)
        finally:app.dependency_overrides.clear();store.engine.dispose()
    asyncio.run(scenario())
