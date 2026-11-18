"""检索契约；岗位字段允许缺失，不推断网页未提供的内容。"""
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    keyword: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=1, max_length=40)
    max_pages: int = Field(default=1, ge=1, le=10, strict=True)

    @field_validator("keyword", "city")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("不允许控制字符")
        return value


class RawSnapshot(BaseModel):
    source_layer: Literal["local_api", "search_list_dom", "detail_dom"] = "local_api"
    source: str = "BossHunter"
    endpoint: str
    captured_at: datetime
    payload: dict[str, Any]
    redactions: list[str] = Field(default_factory=list)
    note: str = "经脱敏的 BossHunter 本地 API 数据；已由采集器加工，不是 BOSS 原始响应或 DOM 快照。"


class JobView(BaseModel):
    id: str
    fields: dict[str, Any]
    raw: RawSnapshot
    detail_raw: RawSnapshot | None = None


Outcome = Literal[
    "new_jobs", "no_results", "duplicates_only", "filtered_only", "load_failed",
    "quota_exhausted", "platform_restricted", "collector_error", "source_unavailable",
    "unknown", "stopped",
]


class SearchTask(BaseModel):
    id: str
    request: SearchRequest
    state: Literal["starting", "running", "stopping", "completed"] = "starting"
    outcome: Outcome | None = None
    reason_code: str = ""
    message: str = "正在启动原版纯采集任务"
    created_at: datetime
    updated_at: datetime
    native_task_id: str | None = None
    native_run_id: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    jobs: list[JobView] = Field(default_factory=list)
    raw_run: RawSnapshot | None = None


class NativeFilters(BaseModel):
    model_config=ConfigDict(extra='forbid')
    city: str = Field(pattern=r'^\d{9}$')
    job_type: Annotated[str,Field(pattern=r'^\d+$')] | list[Annotated[str,Field(pattern=r'^\d+$')]]
    salary: Annotated[str,Field(pattern=r'^\d+$')] | list[Annotated[str,Field(pattern=r'^\d+$')]]
    experience: Annotated[str,Field(pattern=r'^\d+$')] | list[Annotated[str,Field(pattern=r'^\d+$')]]
    regions: list[Annotated[str,Field(pattern=r'^\d{6}$')]] = Field(default_factory=list,max_length=9)

    @field_validator('job_type','salary','experience')
    @classmethod
    def valid_selections(cls,value):
        if isinstance(value,list):
            value=list(dict.fromkeys(value))
            if not value or len(value)>9 or '0' in value and len(value)>1:
                raise ValueError('请选择1至9项；不限不能与具体条件同时选择。')
        return value

    @field_validator('regions')
    @classmethod
    def unique_regions(cls,value):return list(dict.fromkeys(value))

    @property
    def group_count(self):
        count=lambda value:len(value) if isinstance(value,list) else 1
        return count(self.job_type)*count(self.salary)


class DirectSearchRequest(BaseModel):
    """可传四项BOSS原生条件；旧请求不传filters时保持当前页面条件。"""
    model_config = ConfigDict(extra="forbid")
    max_pages: int = Field(default=1, ge=1, le=20, strict=True)
    filters: NativeFilters | None = None

    @model_validator(mode='after')
    def bounded_plan(self):
        if self.filters:
            if self.filters.group_count*self.max_pages>20:
                raise ValueError('每组读取批次×薪资/求职类型组合数不能超过20批，请缩小本次范围。')
        return self


class ReadingLimitUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    daily_limit: int = Field(ge=1, strict=True)


class DirectSearchTask(BaseModel):
    id: str
    request: DirectSearchRequest
    state: Literal["running", "stopping", "completed"] = "running"
    phase: Literal['filters', 'list', 'details', 'completed'] = 'list'
    outcome: str | None = None
    reason_code: str = ""
    message: str = "准备读取用户手动打开的 BOSS 搜索结果页"
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int] = Field(default_factory=dict)
    jobs: list[JobView] = Field(default_factory=list)
    source_page: dict[str, Any] | None = None
    page_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    applied_filters: dict[str, Any] | None = None
    search_groups: list[dict[str, Any]] = Field(default_factory=list)
