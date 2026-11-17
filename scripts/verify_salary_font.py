"""离线核对字体字形；不请求网站、不读取浏览器凭证。

开发工具额外依赖 fonttools[woff]；两份字体由当前页面公开资源获取，不提交 Git。
用法：python scripts/verify_salary_font.py REGULAR.ttf MIX.woff2
"""
import hashlib
import json
from pathlib import Path
import sys

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont


def verify(regular_path: Path, mix_path: Path):
    with TTFont(regular_path) as regular, TTFont(mix_path) as mix:
        if regular['head'].unitsPerEm != mix['head'].unitsPerEm:
            raise ValueError('字体尺寸不同，拒绝推断')
        def outline(font, name):
            glyphs = font.getGlyphSet()
            pen = DecomposingRecordingPen(glyphs)
            glyphs[name].draw(pen)
            return pen.value
        digits = {chr(cp): outline(regular, regular.getBestCmap()[cp]) for cp in range(48, 58)}
        mapping = {}
        for cp, glyph in mix.getBestCmap().items():
            if 0xE000 <= cp <= 0xF8FF:
                shape = outline(mix, glyph)
                matches = [digit for digit, other in digits.items() if shape == other]
                if len(matches) != 1:
                    raise ValueError(f'U+{cp:04X} 无唯一相同数字字形，拒绝猜测')
                mapping[f'U+{cp:04X}'] = matches[0]
    return {'method': 'cmap_and_identical_glyph_outlines', 'mapping': mapping,
            'regular_sha256': hashlib.sha256(regular_path.read_bytes()).hexdigest(),
            'mix_sha256': hashlib.sha256(mix_path.read_bytes()).hexdigest()}


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=True, indent=2))
