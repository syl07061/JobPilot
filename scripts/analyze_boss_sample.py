"""仅根据本地历史API样本更新报告表格；不访问平台或触发采集。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "id": "BossHunter岗位标识", "title": "岗位名称", "company": "公司名称", "salary": "薪资文本",
    "city": "检索城市（非办公地址）", "experience": "经验要求", "education": "学历要求",
    "recruitment_type": "规则推导的招聘类型", "jd": "已清洗的可用JD", "hr_name": "公开招聘者姓名",
    "hr_title": "公开招聘者职位", "hr_active": "公开活跃标签", "company_size": "企业规模",
    "company_industry": "企业行业", "url": "岗位详情URL", "score": "历史评分/默认值",
    "score_reason": "历史评分解释", "greeting": "应用招呼语", "status": "岗位应用状态",
    "created_at": "本地创建时间", "updated_at": "本地更新时间", "quick_score": "原版本地预筛分",
    "resume_path": "本地简历路径（屏蔽）", "deleted_at": "本地删除时间", "deleted_reason": "本地删除原因",
    "source_platform": "来源平台", "source_job_id": "链接派生来源ID", "source_keyword": "检索关键词上下文",
    "source_city_code": "检索城市编码上下文", "last_error": "应用处理错误", "last_error_code": "应用错误码",
    "resume_source_path": "简历源路径（屏蔽）", "resume_image_path": "简历图像路径（屏蔽）",
    "resume_review_status": "简历审查状态", "resume_generation_source": "简历生成来源",
    "resume_failure_reason": "简历失败原因", "resume_reviewed_at": "简历审查时间",
    "greeting_original": "招呼语原稿", "greeting_optimized": "招呼语优化稿",
    "greeting_style_issues": "招呼语质量提醒列表", "greeting_selection": "招呼语选择状态",
    "greeting_reviewed_at": "招呼语审查时间", "outsourcing_level": "规则判断的外包级别",
    "outsourcing_confirmed": "外包确认标记", "outsourcing_matches": "外包规则命中列表",
    "outsourcing_layers": "外包链条规则结果列表", "outsourcing_updated_at": "外包规则更新时间",
    "outsourcing_rules_version": "外包规则版本", "employment_type": "API计算的就业类型",
    "employment_review": "API就业规则审查", "greeting_activity": "API招呼语活动状态",
}
LIST_DETAIL = {"title", "salary", "company", "experience", "education", "url"}
DETAIL = {"jd", "hr_name", "hr_title", "hr_active", "company_size", "company_industry"}
RULE_FILTERS = {"salary", "experience", "education", "company", "source_platform", "source_job_id", "url", "city"}


def main():
    sample = json.loads((ROOT / "data/samples/bosshunter_jobs.json").read_text(encoding="utf-8"))
    stats = sample["field_stats_before_redaction"]
    count = len(sample["jobs"])
    lines = [f"采样时间：`{sample['captured_at']}`；记录数：**{count}**；字段数：**{len(stats)}**。JD长度：" +
             "、".join(str(len(job.get("jd") or "")) for job in sample["jobs"]) + " 字符。", "",
             "| 字段名 | 中文含义 | 数据类型 | 来源层级 | 实际是否获取 | 是否适合筛选 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for key, stat in stats.items():
        origin = "应用存储/处理 → 本地API"
        suitable = "非岗位事实；不用于岗位匹配硬筛"
        if key in LIST_DETAIL:
            origin = "列表/详情解析 → 采集器 → 本地API"
        elif key in DETAIL:
            origin = "详情解析 → 采集器 → 本地API"
        elif key in {"city", "source_keyword", "source_city_code", "source_platform"}:
            origin = "采集器检索上下文 → 本地API"
        elif key in {"id", "source_job_id"}:
            origin = "采集器链接派生 → 本地API"
        elif key in {"employment_type", "employment_review", "greeting_activity"}:
            origin = "本地API动态加工"
        elif key == "recruitment_type":
            origin = "详情/候选语义规则 → 本地API"
        if key in RULE_FILTERS:
            suitable = "可用于规则/去重；需保留原值与缺失状态"
        elif key == "jd":
            suitable = "后续AI语义复筛（结合简历与偏好）"
        elif key in {"company_size", "company_industry"}:
            suitable = "有值时可筛，当前样本缺失"
        elif key in {"title", "recruitment_type"}:
            suitable = "可作候选信号，规则推导不当成平台事实"
        obtained = f"{stat['nonempty']}/{count}非空"
        if key in {"resume_path", "resume_source_path", "resume_image_path"}:
            obtained += "；快照统一屏蔽"
        dtype = "/".join({"str": "string", "int": "integer", "bool": "boolean", "list": "array", "dict": "object", "NoneType": "null"}.get(t, t) for t in stat["types"])
        lines.append(f"| `{key}` | {LABELS.get(key, '未归类新增API字段（待核对）')} | {dtype} | {origin} | {obtained} | {suitable} |")
    doc = ROOT / "docs/boss-data-analysis.md"
    content = doc.read_text(encoding="utf-8")
    start, end = "<!-- SAMPLE_TABLE_START -->", "<!-- SAMPLE_TABLE_END -->"
    before, rest = content.split(start, 1)
    _, after = rest.split(end, 1)
    doc.write_text(before + start + "\n" + "\n".join(lines) + "\n" + end + after, encoding="utf-8")
    print(f"Updated {len(stats)} real API fields from {count} saved records. No collection started.")


if __name__ == "__main__":
    main()
