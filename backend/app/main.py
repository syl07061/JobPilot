"""JobPilot 本机检索入口；采集仅由显式 POST 触发。"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

from app import __version__
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.api.settings import router as settings_router
from app.api.resumes import router as resumes_router, ResumeUploadLimitMiddleware

app = FastAPI(title="JobPilot API", version=__version__)
app.include_router(health_router)
app.include_router(search_router)
app.include_router(settings_router)
app.include_router(resumes_router)
app.add_middleware(ResumeUploadLimitMiddleware)


@app.exception_handler(RequestValidationError)
async def safe_settings_validation(request: Request, exc: RequestValidationError):
    # FastAPI默认错误可能回显请求input，模型配置错误不得带出Key或URL中的凭证。
    if request.url.path=='/settings/model':
        return JSONResponse(status_code=422,content={'detail':'模型配置格式无效：URL须为无凭证的HTTP(S)地址；Key不能含空白；不能同时填写和清除Key。'})
    if request.url.path=='/resumes':
        return JSONResponse(status_code=422,content={'detail':'请使用multipart/form-data，通过file字段上传PDF或Word文件。'})
    return await request_validation_exception_handler(request,exc)


@app.middleware("http")
async def local_mutations_only(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = {"http://127.0.0.1:5173", f"http://{request.headers.get('host', '')}"}
        if request.url.hostname != "127.0.0.1" or (origin and origin not in allowed):
            return JSONResponse({"detail": "不接受其他站点发起的任务操作"}, status_code=403)
    return await call_next(request)
