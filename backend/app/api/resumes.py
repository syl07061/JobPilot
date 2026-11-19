from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import PROJECT_ROOT
from app.services.resumes import MAX_FILE_SIZE, ResumeStore
from app.api.settings import get_model_store
from app.services.resume_parsing import ResumeParser,ParseError

router=APIRouter(prefix='/resumes',tags=['resumes'])


@lru_cache
def get_resume_store():
    try:return ResumeStore(PROJECT_ROOT/'data/resumes')
    except Exception:raise HTTPException(503,'无法初始化本机简历存储，请检查目录权限和数据库文件') from None


@lru_cache
def get_resume_parser():return ResumeParser(get_resume_store(),get_model_store())


@router.get('/profile')
def get_profile(parser:ResumeParser=Depends(get_resume_parser)):
    return parser.view()


@router.post('/parse-latest',status_code=202)
async def parse_latest(parser:ResumeParser=Depends(get_resume_parser)):
    try:return await parser.start()
    except ParseError as exc:raise HTTPException(409 if exc.code=='busy' else 422,exc.message) from None
    except RuntimeError:raise HTTPException(503,'无法读取模型配置，未开始解析。') from None


@router.get('')
def list_resumes(store:ResumeStore=Depends(get_resume_store)):
    try:return {'items':store.list()}
    except Exception:raise HTTPException(503,'无法读取简历版本记录') from None


@router.post('',status_code=201)
async def upload_resume(file:UploadFile,store:ResumeStore=Depends(get_resume_store)):
    try:
        data=bytearray()
        while chunk:=await file.read(64*1024):
            data.extend(chunk)
            if len(data)>MAX_FILE_SIZE:raise HTTPException(413,'简历文件不能超过10MB')
        return await run_in_threadpool(store.save,file.filename or '',bytes(data))
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except HTTPException:raise
    except Exception:raise HTTPException(503,'简历保存失败，未确认上传成功，请刷新版本列表核对') from None
    finally:await file.close()


@router.get('/{resume_id}/download')
def download_resume(resume_id:UUID,store:ResumeStore=Depends(get_resume_store)):
    try:path,name,mime=store.download(str(resume_id))
    except FileNotFoundError:raise HTTPException(404,'简历版本或原文件不存在') from None
    return FileResponse(path,filename=name,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


class ResumeUploadLimitMiddleware:
    """限制上传请求体，避免multipart解析前写入无界临时文件。"""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method']!='POST' or scope['path']!='/resumes':
            return await self.app(scope,receive,send)
        from starlette.responses import JSONResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException
        limit=MAX_FILE_SIZE+64*1024
        headers=dict(scope['headers'])
        try:declared=int(headers.get(b'content-length',b'0'))
        except ValueError:return await JSONResponse({'detail':'请求长度无效'},400)(scope,receive,send)
        if declared>limit:return await JSONResponse({'detail':'上传请求不能超过10MB及必要表单开销'},413)(scope,receive,send)
        total=0
        async def bounded_receive():
            nonlocal total
            message=await receive()
            total+=len(message.get('body',b''))
            if total>limit:raise StarletteHTTPException(413,'上传请求过大')
            return message
        await self.app(scope,bounded_receive,send)
