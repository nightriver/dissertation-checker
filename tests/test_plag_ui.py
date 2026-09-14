"""Тести екрана режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 7,
доповнено `PLAN_PLAG_FILTER_V2.md`, §9.2 (етапи 1, 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from streamlit.testing.v1 import AppTest

from plag_filter.fetch import FetchResult
from ui_helpers import is_plag_filter_mode

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
ROOT = Path(__file__).resolve().parent.parent
REPORT_2002 = ROOT / "examples" / "plag" / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"


def _fake_fetch_404(url: str, *, tmp_dir) -> FetchResult:
    """Підроблена мережа для тестів екрана — миттєва помилка без запиту."""
    return FetchResult(
        ok=False,
        error="http_404",
        url=url,
        final_url=None,
        kind=None,
        pages=[],
        meta={},
        jsonld=[],
        repository_meta={},
        hints={},
    )


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
def test_full_screen_scenario_without_manual_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    # Автоперевірка (етап 3) інакше піде в мережу — CLAUDE.md забороняє мережу в тестах.
    monkeypatch.setattr("plag_filter.fetch.fetch_document", _fake_fetch_404)

    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "plag-filter"
    app.run(timeout=60)

    app.get("file_uploader")[0].upload(
        "report.pdf", REPORT_2002.read_bytes(), "application/pdf"
    )
    app.run(timeout=120)
    assert not app.exception

    assert app.text_input(key="plag_surname").value.strip() != ""

    app.button(key="plag_confirm").click()
    app.run(timeout=120)
    assert not app.exception

    project = app.session_state["plag_project"]
    assert project.confirmed is True
    assert all(state.manual is None for state in project.states.values())

    markdown = "\n".join(item.value for item in app.markdown)
    assert "Джерела на аркуші" in markdown

    before_page = app.session_state["plag_page"]
    next_button = next(b for b in app.get("button") if b.label == "Наступна ▶")
    next_button.click()
    app.run(timeout=120)
    assert not app.exception

    after_page = app.session_state["plag_page"]
    assert after_page == before_page + 1

    app.text_input(key="plag_given_name").set_value("Змінено")
    app.run(timeout=120)
    assert not app.exception

    project = app.session_state["plag_project"]
    assert project.confirmed is False


# ---------------------------------------------------------------------------
# Автоматична паралельна перевірка — PLAN_PLAG_FILTER_V2.md, §9.2, етап 3
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_autocheck_runs_to_completion_without_next20_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plag_filter.fetch.fetch_document", _fake_fetch_404)

    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "plag-filter"
    app.run(timeout=60)

    app.get("file_uploader")[0].upload(
        "report.pdf", REPORT_2002.read_bytes(), "application/pdf"
    )
    app.run(timeout=120)
    assert not app.exception

    app.button(key="plag_confirm").click()

    from plag_filter.checker import pending_count

    for _ in range(20):
        app.run(timeout=120)
        assert not app.exception
        project = app.session_state["plag_project"]
        report = app.session_state["plag_report"]
        if pending_count(report, project) == 0:
            break

    project = app.session_state["plag_project"]
    report = app.session_state["plag_report"]
    assert pending_count(report, project) == 0

    labels = [b.label for b in app.get("button")]
    assert "Перевірити наступні 20" not in labels

    checked_reasons = {
        state.reason for state in project.states.values() if state.check is not None
    }
    assert "unavailable" in checked_reasons


@pytest.mark.corpus
def test_autocheck_stopped_shows_resume_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("plag_filter.fetch.fetch_document", _fake_fetch_404)

    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "plag-filter"
    app.run(timeout=60)

    app.get("file_uploader")[0].upload(
        "report.pdf", REPORT_2002.read_bytes(), "application/pdf"
    )
    app.run(timeout=120)
    assert not app.exception

    app.session_state["plag_check_stopped"] = True
    app.button(key="plag_confirm").click()
    app.run(timeout=120)
    assert not app.exception

    from plag_filter.checker import pending_count

    project = app.session_state["plag_project"]
    report = app.session_state["plag_report"]
    assert pending_count(report, project) > 0

    labels = [b.label for b in app.get("button")]
    assert "Продовжити перевірку" in labels
