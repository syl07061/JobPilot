"""仅对已核对的页面字体做文本还原；未知字体不猜测数字。"""
REGULAR_FONT = 'https://img.bosszhipin.com/static/file/2022/16a48d9v961651026858984.ttf'
MIX_FONT = 'https://img.bosszhipin.com/static/file/2023/3kovsijnt11693967587313.woff2'
MIX_SHA256 = '37be9f6d4984819476d27d4862faa8d23181acc32bed2edafd4f42e5184cd924'
# 由两份实际字体的 cmap 和完整 glyph outline 逐一相等验证，不按字符编码推测。
VERIFIED_DIGITS = dict(zip('\ue031\ue032\ue033\ue034\ue035\ue036\ue037\ue038\ue039\ue03a', '0123456789'))


def private_chars(value: str) -> bool:
    return any('\ue000' <= ch <= '\uf8ff' for ch in value)


def normalize(value: str | None, evidence: dict | None) -> tuple[str | None, dict]:
    if value is None or not private_chars(value):
        return value, {'status': 'plain_text'}
    evidence = evidence or {}
    resources = evidence.get('resources', [])
    known = ('kanzhun-mix' in evidence.get('family', '') and MIX_FONT in resources and REGULAR_FONT in resources)
    if not known or any(ch not in VERIFIED_DIGITS for ch in value if '\ue000' <= ch <= '\uf8ff'):
        return None, {'status': 'unverified_font', 'note': '未知字形未解码，原值保留在 salary_raw。'}
    return value.translate(str.maketrans(VERIFIED_DIGITS)), {
        'status': 'verified_font', 'method': 'cmap_and_identical_glyph_outlines',
        'font_resource': MIX_FONT, 'verified_asset_sha256': MIX_SHA256,
        'note': '已核对该静态字体的完整字形；页面需使用同一资源和字体族，未知字形不猜测。',
    }
