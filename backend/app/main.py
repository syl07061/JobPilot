"""JobPilot 本机检索入口；采集仅由显式 POST 触发。"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.health import router as health_router
from app.api.search import router as search_router

app = FastAPI(title="JobPilot API", version=__version__)
app.include_router(health_router)
app.include_router(search_router)


@app.middleware("http")
async def local_mutations_only(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = {"http://127.0.0.1:5173", f"http://{request.headers.get('host', '')}"}
        if request.url.hostname != "127.0.0.1" or (origin and origin not in allowed):
            return JSONResponse({"detail": "不接受其他站点发起的任务操作"}, status_code=403)
    return await call_next(request)
