"""Тести файлів компонента вкладки «Звіт» — PLAN_PLAG_VIEW.md, §7 етап 3.

JS у наборі не виконується; зовнішній вигляд перевіряється в браузері на
етапі 5. Тут — що файли на місці, без мережевих завантажень, з палітрою
Plag і плашкою номера через `::before`.
"""

from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "plag_filter" / "report_viewer"
FILES = ("report_viewer.html", "report_viewer.css", "report_viewer.js")

# Фон фрагмента для кожного з 10 кольорів — PLAN_PLAG_VIEW.md, §5.
FRAGMENT_BACKGROUNDS = (
    "248, 187, 208",
    "209, 196, 233",
    "187, 222, 251",
    "178, 235, 242",
    "178, 223, 219",
    "200, 230, 201",
    "240, 244, 195",
    "255, 236, 179",
    "255, 204, 188",
    "215, 204, 200",
)


def _read(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def test_component_files_exist_and_are_not_empty() -> None:
    for name in FILES:
        assert _read(name).strip(), name


def test_component_loads_nothing_from_network() -> None:
    js = _read("report_viewer.js")
    css = _read("report_viewer.css")
    html = _read("report_viewer.html")
    assert "fetch(" not in js and "import(" not in js and "XMLHttpRequest" not in js
    assert not re.search(r"^\s*import\s", js, re.M)
    assert "@import" not in css and "url(" not in css
    assert "<script" not in html and "<link" not in html
    # Єдина адреса в JS — простір імен SVG, а не завантаження.
    assert set(re.findall(r"https?://[^\s\"'`]+", js)) <= {"http://www.w3.org/2000/svg"}


def test_css_has_plag_palette_for_every_color_index() -> None:
    css = _read("report_viewer.css")
    for index, background in enumerate(FRAGMENT_BACKGROUNDS):
        assert f"--pr-c{index}-bg: rgb({background});" in css
        assert f'.pr-frag[data-c="{index}"]' in css
        assert f'.pr-row[data-c="{index}"]' in css


def test_badge_is_generated_content_outside_selection() -> None:
    css = _read("report_viewer.css")
    assert re.search(r"\[data-badge\]::before\s*\{[^}]*content:\s*attr\(data-badge\)", css)


def test_source_panel_is_sticky() -> None:
    css = _read("report_viewer.css")
    panel = re.search(r"\.pr-panel\s*\{([^}]*)\}", css)
    assert panel is not None
    assert "position: sticky" in panel.group(1)
    assert "clamp(260px, 32%, 400px)" in panel.group(1)


def test_component_does_not_bind_arrow_keys() -> None:
    js = _read("report_viewer.js")
    assert "keydown" not in js and "ArrowLeft" not in js


def _app() -> None:
    from plag_filter.report_viewer import render_report_viewer

    payload = {
        "page": 4,
        "pages": [4, 5, 6],
        "filename": "report.pdf",
        "scores": {
            "similarity": "76",
            "risk": "НАЙВИЩИЙ",
            "paraphrase": "4%",
            "wrong_citation": "0%",
            "text_matches": "72%",
        },
        "paragraphs": [
            [
                {"text": "", "number": 7, "marker": True, "color": 6, "excluded": False},
                {"text": "Текст", "number": 7, "marker": False, "color": 6, "excluded": False},
            ]
        ],
        "sources": [],
        "below_count": 0,
        "show_excluded": False,
        "signature": "x",
    }
    render_report_viewer(payload, key="plag_report_viewer")


def test_render_report_viewer_runs_in_app() -> None:
    app = AppTest.from_function(_app)
    app.run()
    assert not app.exception
