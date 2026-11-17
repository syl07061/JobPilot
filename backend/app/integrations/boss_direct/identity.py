"""岗位身份由公开详情链接确定，标题可能因列表营销后缀而不同。"""


def detail_matches(detail: dict | None, expected_url: str | None) -> bool:
    if not detail or not expected_url or detail.get('url') != expected_url:
        return False
    jd = detail.get('jd')
    if not isinstance(jd, str) or not jd.strip():
        return False
    # 新快照带选中卡片证据；有该证据时必须唯一指向同一岗位。
    # 兼容旧版快照：未记录选中状态时仍可根据实际详情链接判断。
    return 'active_job_urls' not in detail or detail['active_job_urls'] == [expected_url]
