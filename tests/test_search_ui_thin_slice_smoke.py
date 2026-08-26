"""
Шлюз кроку 3 §22 PLAN_SEARCH.md (друга половина): UI smoke-тест показує
одну картку каналу A на реальному Streamlit-екрані ?mode=search і змінює
її статус. Доменна логіка (парсер, канал A, побудова запиту, переходи
статусу) перевіряється окремо в `test_search_thin_slice_integration.py` і
модульних тестах `search/ui_logic.py`; тут перевіряється лише «чистий
екран» app.py відповідно до §22: «app.py залишається тонкою оболонкою без
непокритої логіки».
"""

from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

HEADING_TEXT = "РОЗДІЛ 1"
BODY_TEXT = (
    "Ми пропонуємо, на нашу думку, важливе рішення для реформування "
    "вітчизняного законодавства."
)


def _build_single_section_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    html = f"<p>{HEADING_TEXT}</p><p>{BODY_TEXT}</p>"
    page.insert_htmlbox(fitz.Rect(72, 72, 500, 300), html)
    data = doc.tobytes()
    doc.close()
    return data


def _run_with_uploaded_pdf() -> AppTest:
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    uploader = app.get("file_uploader")[0]
    uploader.upload("dissertation.pdf", _build_single_section_pdf_bytes(), "application/pdf")
    app.run(timeout=30)
    return app


def test_uploaded_pdf_renders_exactly_one_query_card():
    app = _run_with_uploaded_pdf()
    assert not app.exception

    radios = app.get("radio")
    assert len(radios) == 1
    assert radios[0].label == "Статус"
    assert radios[0].value == "unchecked"
    assert radios[0].index == 0

    markdown_text = "\n".join(md.value for md in app.markdown)
    assert "[A]" in markdown_text
    assert "«" in markdown_text and "»" in markdown_text

    code_blocks = [c.value for c in app.get("code")]
    assert any("пропонуємо" in block for block in code_blocks)


def test_changing_the_status_radio_updates_the_triage_state():
    app = _run_with_uploaded_pdf()
    result = app.session_state["search_result"]
    query_id = result.queries[0].query_id
    assert app.session_state["search_query_states"][query_id].status == "unchecked"

    app.get("radio")[0].set_value("found").run(timeout=30)

    assert not app.exception
    updated_state = app.session_state["search_query_states"][query_id]
    assert updated_state.status == "found"
    assert updated_state.found_engine == "Google"
    assert app.get("radio")[0].value == "found"


def test_back_button_still_returns_from_the_search_screen():
    app = _run_with_uploaded_pdf()
    app.button(key="search_back").click().run(timeout=30)
    assert "mode" not in app.query_params
