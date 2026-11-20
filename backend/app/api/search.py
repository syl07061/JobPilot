import json
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.resumes import get_resume_parser
from app.services.ai_review import REVIEW_PROFILE_FIELDS, ReviewService
from app.core.config import PROJECT_ROOT, get_settings
from app.integrations.boss_direct.collector import DirectBossCollector
from app.integrations.boss_direct.safety import BatchBudget
from app.integrations.collector import CollectorError
from app.schemas.search import DirectSearchRequest, DirectSearchTask, ReadingLimitUpdate
from app.services.direct_search import DirectSearchService

router = APIRouter(prefix="/search", tags=["search"])


@lru_cache
def get_review_service():
    return ReviewService(PROJECT_ROOT / "data" / "ai_reviews")


@lru_cache
def get_service() -> DirectSearchService:
    return DirectSearchService(DirectBossCollector(str(get_settings().chrome_cdp_url),
                              PROJECT_ROOT / "data" / "direct_collector", daily_limit=get_settings().collector_daily_limit),
                              PROJECT_ROOT / "data" / "direct_tasks")


@router.get("/tasks/{task_id}/ai-review")
async def read_review(task_id: str, response: Response, service=Depends(get_service), review=Depends(get_review_service)):
    task = await task_status(task_id, service)
    response.headers["Cache-Control"] = "no-store"
    return review.status(task.id)


@router.post("/tasks/{task_id}/ai-review", status_code=202)
async def start_review(task_id: str, service=Depends(get_service), review=Depends(get_review_service),
                       parser=Depends(get_resume_parser)):
    task = await task_status(task_id, service)
    current = review.running.get(task.id)
    if current and current["state"] == "running":
        raise HTTPException(409, "该任务正在复筛，请等待完成。")
    if task.state != "completed" or not task.jobs:
        raise HTTPException(409, "请先完成初筛并采集岗位。")
    view = parser.view()
    profile = view["profile"]
    if not view["is_current"] or not profile:
        raise HTTPException(422, "请先在简历上传页解析最新简历。")
    if not any(profile["data"].get(key) for key in REVIEW_PROFILE_FIELDS):
        raise HTTPException(422, "当前简历档案没有教育、工作或项目经历，无法复筛。")
    settings = parser.settings.credentials()
    if not all(settings.get(key) for key in ("base_url", "model", "api_key")):
        raise HTTPException(422, "请先在配置页填写完整模型配置。")
    return review.start(task, profile, settings)


def get_budget() -> BatchBudget:
    return BatchBudget(PROJECT_ROOT / 'data' / 'direct_collector', get_settings().collector_daily_limit)


@router.get('/budget')
def reading_budget(budget: BatchBudget = Depends(get_budget)):
    try:
        return budget.status()
    except CollectorError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.put('/budget')
def configure_budget(body: ReadingLimitUpdate, budget: BatchBudget = Depends(get_budget)):
    try:
        return budget.set_limit(body.daily_limit)
    except CollectorError as exc:
        raise HTTPException(409 if exc.code == 'busy' else 503, str(exc)) from exc


@router.get("/collector-status")
async def collector_status(service: DirectSearchService = Depends(get_service)):
    try:
        return await service.adapter.status()
    except CollectorError as exc:
        return {"available": False, "code": exc.code, "message": str(exc)}


@router.get('/filter-options')
async def filter_options(refresh: bool=False, city: str | None=Query(default=None,pattern=r'^\d{9}$'), service: DirectSearchService=Depends(get_service)):
    try:
        return await service.adapter.filter_options(refresh,city)
    except CollectorError as exc:
        raise HTTPException(409 if exc.code=='busy' else 503,str(exc)) from exc


@router.get("/preview")
async def preview():
    """离线真实样本预览；不创建任务，绝不把历史样本称为新检索结果。"""
    from app.integrations.bosshunter.mapping import map_job  # 仅旧样本转换，不连接旧采集服务。
    path = PROJECT_ROOT / "data" / "samples" / "bosshunter_jobs.json"
    if not path.exists():
        raise HTTPException(404, "尚无本地真实样本，请先运行样本保存脚本；不自动采集。")
    sample = json.loads(path.read_text(encoding="utf-8"))
    return {"historical_sample": True, "captured_at": sample["captured_at"],
            "message": "已保存的真实历史样本，用于字段验证，不属于本次检索。",
            "jobs": [map_job(job, f"/api/jobs/{job['id']}") for job in sample["jobs"]]}


@router.post("/tasks", status_code=202, response_model=DirectSearchTask)
async def start_search(body: DirectSearchRequest, service: DirectSearchService = Depends(get_service)):
    try:
        return await service.create(body)
    except CollectorError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/tasks/{task_id}", response_model=DirectSearchTask)
async def task_status(task_id: str, service: DirectSearchService = Depends(get_service)):
    try:
        return await service.get(task_id)
    except KeyError:
        raise HTTPException(404, "检索任务不存在") from None


@router.post('/tasks/{task_id}/refresh-current', response_model=DirectSearchTask)
@router.post('/tasks/{task_id}/jobs/{job_id}/detail', response_model=DirectSearchTask)
async def refresh_current(task_id: str, job_id: str | None = None, service: DirectSearchService = Depends(get_service)):
    try:
        return await service.refresh_current(task_id, job_id)
    except KeyError:
        raise HTTPException(404, '任务或岗位不存在') from None
    except CollectorError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/tasks/{task_id}/auto-details', status_code=202, response_model=DirectSearchTask)
async def auto_details(task_id: str, service: DirectSearchService = Depends(get_service)):
    try:
        return await service.start_details(task_id)
    except KeyError:
        raise HTTPException(404, '检索任务不存在') from None
    except CollectorError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/tasks/{task_id}/stop", response_model=DirectSearchTask)
async def stop_search(task_id: str, service: DirectSearchService = Depends(get_service)):
    try:
        return await service.stop(task_id)
    except KeyError:
        raise HTTPException(404, "检索任务不存在") from None
    except CollectorError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post('/tasks/{task_id}/auto-details', status_code=202, response_model=DirectSearchTask)
async def auto_details(task_id: str, service: DirectSearchService = Depends(get_service)):
    try:
        return await service.start_details(task_id)
    except KeyError:
        raise HTTPException(404, '检索任务不存在') from None
    except CollectorError as exc:
        raise HTTPException(409, str(exc)) from exc
