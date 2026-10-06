"""前端资源的一致性检查。

用户会自己改 index.html 里的文案，这个测试守住的是「改文案可以、改 id 会崩」这条界线：
app.js 里所有 $("xxx") 引用的元素，都必须在 index.html 里存在。
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "web"


def test_every_id_used_by_js_exists_in_html():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")

    declared = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'\$\("([^"]+)"\)', js))

    missing = sorted(used - declared)
    assert not missing, f"app.js 引用了 index.html 中不存在的 id: {missing}"


def test_static_assets_are_referenced_without_version_query():
    """静态资源改用 no-store，不再依赖手工版本号。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "/static/app.js" in html
    assert "/static/style.css" in html
    assert "?v=" not in html


def test_html_keeps_editing_guide_comment():
    """给使用者看的「怎么改」注释不能被误删。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "怎么改" in html
    assert "不要改 id" in html
