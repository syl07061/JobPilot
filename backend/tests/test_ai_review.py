import asyncio
import json
from types import SimpleNamespace

from app.services.ai_review import REVIEW_RULE_VERSION, Assessment, ReviewService
from app.services.resume_parsing import ParseError


def test_review_status_hides_legacy_results_without_overwriting_them(tmp_path):
    path = tmp_path / "task.json"
    old_result = {"state": "completed", "matches": [], "excluded": [
        {"id": "4", "title": "AI应用开发工程师", "reason": "JD 未明确实际用人公司，按猎头规则过滤"}
    ]}
    path.write_text(json.dumps(old_result, ensure_ascii=False), encoding="utf-8")
    service = ReviewService(tmp_path)

    assert service.status("task") == {"rule_version": REVIEW_RULE_VERSION, "stale": True, "result": None}
    assert service.status("new-task") == {"rule_version": REVIEW_RULE_VERSION, "stale": False, "result": None}
    assert json.loads(path.read_text(encoding="utf-8")) == old_result


def test_review_ranks_all_scores_and_persists_exclusions(tmp_path):
    education = [{"organization": "示例大学", "title": "计算机科学与技术本科"}]

    async def model_call(settings, prompt, document, schema):
        inputs = json.loads(document)
        assert set(inputs) == {"jd", "education", "work_experience", "projects"}
        assert inputs["education"] == education
        jd = inputs["jd"]
        if jd == "模型失败":
            raise ParseError("model_timeout", "timeout")
        soft = {"requirement": "动手能力强", "category": "软性要求", "priority": "必备", "match": "匹配",
                "reason": "不应参与评分", "evidence": ""}
        if jd == "只有软性要求":
            return Assessment(requirements=[soft])
        assert jd in {"三年以上商务拓展或生态合作经验", "高匹配要求"}
        primary = (
            {"requirement": "Python", "category": "技术栈", "priority": "必备", "match": "匹配",
             "reason": "工作中使用 Python", "evidence": "使用 Python"}
            if jd == "高匹配要求" else
            {"requirement": "三年以上商务拓展或生态合作经验", "category": "相关经验", "priority": "必备",
             "match": "简历未体现", "reason": "简历未体现商务拓展或生态合作经历及对应年限", "evidence": ""}
        )
        return Assessment(requirements=[
            primary, soft,
            {"requirement": "SQL", "category": "技术栈", "priority": "加分", "match": "部分匹配",
             "reason": "项目中使用过", "evidence": "数据项目"},
        ])

    async def run():
        jobs = [SimpleNamespace(id=str(index), fields={"jd": jd, "company": company})
                for index, (company, jd) in enumerate([
                    ("示例公司", "三年以上商务拓展或生态合作经验"), ("某知名企业", "实际用人公司：示例公司"),
                    ("示例公司", ""), ("示例公司", "模型失败"), ("拓竹科技", "高匹配要求"),
                    (None, "实际用人公司：示例公司"), ("示例公司", "只有软性要求"),
                ])]
        task = SimpleNamespace(id="task", jobs=jobs)
        profile = {"version": 1, "parsed_at": "today",
                   "data": {"education": education, "work_experience": [], "projects": [], "email": "not-sent"}}
        service = ReviewService(tmp_path, model_call)
        assert service.start(task, profile, {})["state"] == "running"
        await asyncio.gather(*service.workers)
        status = ReviewService(tmp_path).status(task.id)
        assert not status["stale"]
        result = status["result"]
        assert result["rule_version"] == REVIEW_RULE_VERSION
        assert result["state"] == "completed"
        assert result["processed"] == result["total"] == 7
        assert [(job["id"], job["score"]) for job in result["matches"]] == [("4", 88), ("0", 12)]
        assert result["matches"][0]["employer"] == "拓竹科技"
        assert [job["id"] for job in result["excluded"]] == ["1", "2", "3", "5", "6"]
        assert result["excluded"][2]["company"] == "示例公司"
        assert len(result["matches"][0]["requirements"]) == 2
        assert result["matches"][1]["requirements"][0]["match"] == "简历未体现"
        assert result["excluded"][-1]["reason"] == "未识别到可评分的技术或硬性要求"

    asyncio.run(run())
