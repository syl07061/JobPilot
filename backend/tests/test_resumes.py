import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import struct
import zipfile

import httpx
import pytest

from app.api.resumes import get_resume_store
from app.main import app
from app.services.resumes import ResumeStore,MAX_FILE_SIZE,validate_document

PDF=b'%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\n%%EOF\n'


def docx():
    data=BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('[Content_Types].xml','<Types><Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('word/document.xml','<document>Offline test fixture, not a personal resume</document>')
    return data.getvalue()


def legacy_doc():
    # 只包含Word命名流的合成CFB容器，不包含任何个人简历或真实文档正文。
    free,end,fat=0xffffffff,0xfffffffe,0xfffffffd
    header=bytearray(512);header[:8]=bytes.fromhex('d0cf11e0a1b11ae1')
    struct.pack_into('<HHHHH',header,24,0x3e,3,0xfffe,9,6)
    struct.pack_into('<IIIIIIIII',header,40,0,1,1,0,4096,end,0,end,0)
    struct.pack_into('<109I',header,76,0,*([free]*108))
    sectors=[fat,end]+list(range(3,10))+[end]+list(range(11,18))+[end]
    table=struct.pack('<128I',*(sectors+[free]*(128-len(sectors))))
    directory=bytearray(512)
    for index,(name,kind,child,right,start,size) in enumerate([
        ('Root Entry',5,1,free,end,0),('WordDocument',2,free,2,2,4096),('0Table',2,free,free,10,4096)]):
        offset=index*128;encoded=(name+'\0').encode('utf-16-le')
        directory[offset:offset+len(encoded)]=encoded
        struct.pack_into('<HBBIII',directory,offset+64,len(encoded),kind,1,free,right,child)
        struct.pack_into('<IQ',directory,offset+116,start,size)
    return bytes(header)+table+bytes(directory)+bytes(8192)


@pytest.mark.parametrize('name,data',[('fixture.pdf',PDF),('fixture.docx',docx()),('fixture.doc',legacy_doc())])
def test_supported_containers_and_roundtrip(tmp_path,name,data):
    store=ResumeStore(tmp_path)
    record=store.save(name,data)
    assert record['version']==1 and record['parsed'] is False
    path,filename,_=store.download(record['id'])
    assert path.read_bytes()==data and filename==name
    assert 'path' not in record
    store.engine.dispose()
    restarted=ResumeStore(tmp_path)
    assert restarted.list()==[record]
    restarted.engine.dispose()


def test_same_filename_is_versioned_and_concurrent_uploads_are_unique(tmp_path):
    store=ResumeStore(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(lambda _:store.save('same.pdf',PDF),range(3)))
    assert sorted(r['version'] for r in rows)==[1,2,3]
    assert len({r['id'] for r in rows})==3 and len(list(store.files.glob('*.pdf')))==3
    assert [r['version'] for r in store.list()]==[3,2,1]
    store.engine.dispose()


@pytest.mark.parametrize('name,data',[
    ('bad.pdf',b'not pdf'),('bad.docx',PDF),('bad.doc',PDF),('bad.exe',PDF),('empty.pdf',b''),
    ('huge.pdf',b'0'*(MAX_FILE_SIZE+1)),('bad\n.pdf',PDF),
])
def test_invalid_files_do_not_create_versions(tmp_path,name,data):
    store=ResumeStore(tmp_path)
    with pytest.raises(ValueError):store.save(name,data)
    assert not store.list() and not list(store.files.iterdir())
    store.engine.dispose()


def test_path_traversal_name_is_not_used_as_storage_path(tmp_path):
    store=ResumeStore(tmp_path)
    record=store.save('C:\\fakepath\\..\\resume.pdf',PDF)
    assert record['filename']=='resume.pdf'
    path,_,_=store.download(record['id'])
    assert path.parent==store.files and path.name!=record['filename']
    store.engine.dispose()


def test_fake_zip_and_macro_docx_are_rejected():
    for macro in (False,True):
        data=BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('[Content_Types].xml','wordprocessingml.document.main+xml')
            if macro:
                z.writestr('word/document.xml','<document/>');z.writestr('word/vbaProject.bin','test')
        with pytest.raises(ValueError):validate_document('fixture.docx',data.getvalue())


def test_api_upload_list_download_limits_and_origin(tmp_path):
    async def scenario():
        store=ResumeStore(tmp_path)
        app.dependency_overrides[get_resume_store]=lambda:store
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8200') as client:
                assert (await client.get('/resumes')).json()=={'items':[]}
                response=await client.post('/resumes',files={'file':('fixture.pdf',PDF,'application/pdf')})
                assert response.status_code==201
                record=response.json()
                assert (await client.get('/resumes')).json()['items']==[record]
                download=await client.get(record['download_url'])
                assert download.content==PDF and 'attachment' in download.headers['content-disposition']
                assert download.headers['cache-control']=='no-store'
                assert (await client.get('/resumes/00000000-0000-0000-0000-000000000000/download')).status_code==404
                assert (await client.get('/resumes/not-a-uuid/download')).status_code==422
                assert (await client.post('/resumes',files={'file':('bad.pdf',b'bad')})).status_code==422
                assert (await client.post('/resumes',headers={'Origin':'https://unrelated.example'},files={'file':('f.pdf',PDF)})).status_code==403
                assert (await client.post('/resumes',headers={'Content-Length':str(MAX_FILE_SIZE+100000)},content=b'')).status_code==413
                assert len(store.list())==1
        finally:
            app.dependency_overrides.clear();store.engine.dispose()
    asyncio.run(scenario())


def test_chunked_request_limit_without_content_length(tmp_path):
    async def chunks():
        for _ in range(12):yield b'X'*(1024*1024)
    async def scenario():
        store=ResumeStore(tmp_path);app.dependency_overrides[get_resume_store]=lambda:store
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8200') as client:
                async def multipart():
                    yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="f.pdf"\r\n\r\n'
                    async for chunk in chunks():yield chunk
                    yield b'\r\n--boundary--\r\n'
                response=await client.post('/resumes',headers={'Content-Type':'multipart/form-data; boundary=boundary'},content=multipart())
                assert response.status_code==413,response.text
                assert not store.list()
        finally:app.dependency_overrides.clear();store.engine.dispose()
    asyncio.run(scenario())
