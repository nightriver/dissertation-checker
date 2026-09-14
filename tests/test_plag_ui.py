"""Тести екрана режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 7."""

from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from streamlit.testing.v1 import AppTest

from ui_helpers import is_plag_filter_mode

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
ROOT = Path(__file__).resolve().parent.parent
REPORT_2002 = ROOT / "examples" / "plag" / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"


# ---------------------------------------------------------------------------
# is_plag_filter_mode — за зразком тестів is_table_highlight_mode
# ---------------------------------------------------------------------------


def test_is_plag_filter_mode_true_only_for_plag_filter() -> None:
    assert is_plag_filter_mode({"mode": "plag-filter"})
    assert not is_plag_filter_mode({})
    assert not is_plag_filter_mode({"mode": "compare"})


def test_is_plag_filter_mode_handles_query_param_lists() -> None:
    assert is_plag_filter_mode({"mode": ["plag-filter"]})
    assert not is_plag_filter_mode({"mode": []})


# ---------------------------------------------------------------------------
# Запуск app.py через AppTest — PLAN_PLAG_FILTER.md, §10.2, етап 7
# ---------------------------------------------------------------------------


def test_plag_filter_screen_opens_without_exceptions() -> None:
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "plag-filter"
    app.run(timeout=30)

    assert not app.exception
    assert "Очищення звіту Plag" in "\n".join(item.value for item in app.title)
    assert len(app.get("file_uploader")) == 1


@pytest.mark.parametrize("mode", [None, "search", "compare", "table-highlight"])
def test_other_modes_still_open(mode: str | None) -> None:
    app = AppTest.from_file(APP_PATH)
    if mode is not None:
        app.query_params["mode"] = mode
    app.run(timeout=30)

    assert not app.exception


# ---------------------------------------------------------------------------
# Сценарій без ручного втручання (corpus) — PLAN_PLAG_FILTER.md, §10.2, етап 7
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_full_screen_scenario_without_manual_decisions() -> None:
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "plag-filter"
    app.run(timeout=60)

    app.get("file_uploader")[0].upload(
        "report.pdf", REPORT_2002.read_bytes(), "application/pdf"
    )
    app.run(timeout=120)
    assert not app.exception

    app.text_input(key="plag_surname").set_value("Вигаданко")
    app.text_input(key="plag_initials").set_value("О. А.")
    app.number_input(key="plag_year").set_value(2002)
    app.run(timeout=120)

    app.checkbox(key="plag_confirmed").set_value(True)
    app.run(timeout=120)
    assert not app.exception

    markdown = "\n".join(item.value for item in app.markdown)
    assert "Джерела на аркуші" in markdown

    project = app.session_state["plag_project"]
    assert all(state.manual is None for state in project.states.values())

    before_page = app.session_state["plag_page"]
    next_button = next(b for b in app.get("button") if b.label == "Наступна ▶")
    next_button.click()
    app.run(timeout=120)
    assert not app.exception

    after_page = app.session_state["plag_page"]
    assert after_page == before_page + 1
