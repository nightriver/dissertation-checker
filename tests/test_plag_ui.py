"""Тести екрана режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 7,
доповнено `PLAN_PLAG_FILTER_V2.md`, §9.2 (етапи 1, 3, 9)."""

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

    # Аркуші гортає компонент перегляду: подія `page` → `apply_viewer_event`
    # → `session_state["plag_page"]` — PLAN_PLAG_FILTER_V2.md, §9.2 етап 8.
    import streamlit as st

    from plag_filter.view import apply_viewer_event

    report = app.session_state["plag_report"]
    before_page = app.session_state["plag_page"]
    st.session_state["plag_page"] = before_page
    assert apply_viewer_event(
        project, report, {"type": "page", "page": before_page + 1}
    )
    assert st.session_state["plag_page"] == before_page + 1

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
def test_demo_environment_variable_opens_report_without_uploader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Режим показу для браузера — PLAN_PLAG_FILTER_V2.md, §8.6, §9.2 етап 8."""
    monkeypatch.setattr("plag_filter.fetch.fetch_document", _fake_fetch_404)
    monkeypatch.setenv("PLAG_FILTER_DEMO_PDF", str(REPORT_2002))

    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "plag-filter"
    app.run(timeout=120)

    assert not app.exception
    uploader_labels = [item.label for item in app.get("file_uploader")]
    assert "Звіт Plag (PDF)" not in uploader_labels
    assert app.session_state["plag_report"].page_count > 0
    assert app.text_input(key="plag_surname").value.strip() != ""


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


# ---------------------------------------------------------------------------
# Порядок екрана й підсумок двома блоками — PLAN_PLAG_FILTER_V2.md, §9.2, етап 9
# ---------------------------------------------------------------------------


def test_ui_module_source_has_no_disputed_word() -> None:
    """На екрані немає узагальненого слова «спірні» — §9.2 етап 9."""
    source_path = Path(__file__).resolve().parents[1] / "plag_filter" / "ui.py"
    text = source_path.read_text(encoding="utf-8").casefold()
    assert "спірн" not in text


@pytest.mark.corpus
def test_summary_blocks_sum_to_all_rows_after_fake_check() -> None:
    """Сума обох блоків підсумку дорівнює кількості рядків переліку —
    §9.2 етап 9."""
    import tempfile

    from plag_filter.checker import check_batch, pending_count
    from plag_filter.pdf import parse_report
    from plag_filter.project import new_project

    report = parse_report(REPORT_2002.read_bytes())
    project = new_project(report, "report.pdf")
    project.surname = "Вигаданко"
    project.given_name = "Ганна"
    project.patronymic = "Іванівна"
    project.initials = "Г.І."
    project.year = 2002
    project.confirmed = True

    with tempfile.TemporaryDirectory() as tmp:
        while pending_count(report, project) > 0:
            check_batch(
                report, project, fetch=_fake_fetch_404, tmp_dir=Path(tmp), limit=50, workers=6
            )

    reason_counts: dict[str, int] = {}
    for state in project.states.values():
        reason_counts[state.reason] = reason_counts.get(state.reason, 0) + 1

    excluded_total = sum(
        reason_counts.get(reason, 0)
        for reason in ("own_work", "cites_author", "later", "below_threshold", "manual_exclude")
    )
    kept_total = sum(
        reason_counts.get(reason, 0)
        for reason in (
            "earlier",
            "date_unknown",
            "unavailable",
            "date_conflict",
            "same_year",
            "manual_keep",
            "unchecked",
            "unconfirmed",
        )
    )

    assert excluded_total + kept_total == len(report.rows)


@pytest.mark.corpus
def test_summary_shows_own_work_and_later_with_download_and_project_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Наскрізний сценарій: власна робота, пізніше джерело, кнопка
    завантаження і «Проєкт» останнім — §9.2 етап 9."""
    from plag_filter.pdf import parse_report
    from plag_filter.rules import extract_author

    report = parse_report(REPORT_2002.read_bytes())
    guess = extract_author(report.title_text)
    assert guess is not None

    visible_numbers = sorted(
        number
        for number, row in report.rows.items()
        if row.percent is None or row.percent >= 0.1
    )
    own_work_number, later_number, *_rest = visible_numbers
    own_work_url = report.rows[own_work_number].urls[0]
    later_url = report.rows[later_number].urls[0]
    byline_text = f"© {guess.surname} {guess.initials} канд. наук"

    def fake_fetch(url: str, *, tmp_dir) -> FetchResult:
        if url == own_work_url:
            return FetchResult(
                ok=True,
                error=None,
                url=url,
                final_url=url,
                kind="html",
                pages=[byline_text],
                meta={},
                jsonld=[],
                repository_meta={},
                hints={},
            )
        if url == later_url:
            return FetchResult(
                ok=True,
                error=None,
                url=url,
                final_url=url,
                kind="html",
                pages=["Київ – 2010. Матеріали конференції без автора."],
                meta={},
                jsonld=[],
                repository_meta={},
                hints={},
            )
        return _fake_fetch_404(url, tmp_dir=tmp_dir)

    monkeypatch.setattr("plag_filter.fetch.fetch_document", fake_fetch)

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

    reason_counts: dict[str, int] = {}
    for state in project.states.values():
        reason_counts[state.reason] = reason_counts.get(state.reason, 0) + 1
    assert reason_counts.get("own_work", 0) >= 1
    assert reason_counts.get("later", 0) >= 1

    download_labels = [b.label for b in app.get("download_button")]
    assert "Завантажити очищений PDF" in download_labels

    expander_labels = [e.label for e in app.get("expander")]
    assert expander_labels[-1] == "Проєкт"

    from plag_filter.project import protocol_paragraphs

    joined = "\n".join(protocol_paragraphs(project, report))
    assert "Виключено з PDF" in joined
    assert "Залишено в PDF" in joined
