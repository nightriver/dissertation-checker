"""Шлюз кроку 15: повний екран і JSON-проєкт PLAN_SEARCH.md §§17–19."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from search.state import ImportRejected, QueryState, UnmatchedRecord
from search.types import SectionKind, SectionOverride, SectionOverrideAction
from search.ui_logic import (
    apply_status_action,
    import_search_project,
    rebuild_search_pipeline,
    run_search_pipeline,
    serialize_search_project,
)
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BODY = (
    "Ми пропонуємо, на нашу думку, важливе рішення для реформування "
    "вітчизняного законодавства."
)


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_htmlbox(
        fitz.Rect(72, 72, 500, 300),
        f"<p>РОЗДІЛ 1</p><p>{BODY}</p>",
    )
    data = document.tobytes()
    document.close()
    return data


def _chapter_override(result, kind: SectionKind = SectionKind.INTRO) -> SectionOverride:
    section = next(item for item in result.document.sections if item.kind == SectionKind.CHAPTER)
    heading_id = result.document.blocks[section.block_start].block_id
    return SectionOverride(SectionOverrideAction.SET_KIND, heading_id, kind)


def _run_app_with_pdf() -> AppTest:
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    app.get("file_uploader")[0].upload("work.pdf", _pdf_bytes(), "application/pdf")
    app.run(timeout=30)
    return app


def test_gate_rebuild_applies_override_and_restores_exact_state() -> None:
    data = _pdf_bytes()
    before = run_search_pipeline(data)
    query_id = before.queries[0].query_id
    states = {query_id: QueryState(query_id, status="found", found_engine="google")}
    override = _chapter_override(before)
    after, after_states, imported = rebuild_search_pipeline(
        data,
        (override,),
        before,
        states,
        app_version="gate",
        file_name="work.pdf",
    )
    assert after.document.applied_overrides == (override,)
    assert any(section.kind == SectionKind.INTRO for section in after.document.sections)
    assert after_states[after.queries[0].query_id].status == "found"
    assert imported.restored_count == 1


def test_gate_serialization_is_utf8_deterministic_and_carries_unmatched() -> None:
    result = run_search_pipeline(_pdf_bytes())
    states = {result.queries[0].query_id: QueryState(result.queries[0].query_id)}
    unmatched = (UnmatchedRecord("old", "donor", {"коментар": "не втрачено"}),)
    first = serialize_search_project(
        result,
        states,
        app_version="gate",
        file_name="робота.pdf",
        unmatched=unmatched,
    )
    second = serialize_search_project(
        result,
        states,
        app_version="gate",
        file_name="робота.pdf",
        unmatched=unmatched,
    )
    assert first == second
    payload = json.loads(first.decode("utf-8"))
    assert payload["file"]["name"] == "робота.pdf"
    assert payload["unmatched"][0]["payload"]["коментар"] == "не втрачено"


def test_gate_import_applies_overrides_before_final_query_matching() -> None:
    data = _pdf_bytes()
    before = run_search_pipeline(data)
    query_id = before.queries[0].query_id
    raw = serialize_search_project(
        before,
        {query_id: QueryState(query_id, status="no_result")},
        app_version="gate",
        file_name="work.pdf",
    )
    payload = json.loads(raw.decode("utf-8"))
    override = _chapter_override(before)
    payload["section_overrides"] = [{
        "action": override.action.value,
        "heading_block_id": override.heading_block_id,
        "section_kind": override.section_kind.value,
    }]
    result, states, imported = import_search_project(data, payload, before)
    assert result.document.applied_overrides == (override,)
    assert states[result.queries[0].query_id].status == "no_result"
    assert imported.section_overrides == (override,)


def test_gate_rejected_import_does_not_mutate_current_objects() -> None:
    data = _pdf_bytes()
    current = run_search_pipeline(data)
    raw = serialize_search_project(
        current,
        {},
        app_version="gate",
        file_name="work.pdf",
    )
    payload = json.loads(raw.decode("utf-8"))
    payload["file"]["sha256"] = "f" * 64
    snapshot = current
    with pytest.raises(ImportRejected):
        import_search_project(data, payload, current)
    assert current == snapshot


def test_gate_comment_action_does_not_change_unchecked_status() -> None:
    states = {"q": QueryState("q")}
    updated = apply_status_action(states, "q", "comment", comment="нотатка")
    assert updated["q"].status == "unchecked"
    assert updated["q"].comment == "нотатка"


def test_gate_loaded_screen_shows_all_seven_blocks_and_fallbacks() -> None:
    app = _run_app_with_pdf()
    assert not app.exception
    markdown = "\n".join(item.value for item in app.markdown)
    for heading in (
        "Карта розділів",
        "Ознаки перекладу",
        "Рік і мови бібліографії",
        "Ознаки перекладу за розділами",
        "Запити за розділами",
        "Стан проєкту",
    ):
        assert heading in markdown
    assert len(app.get("file_uploader")) == 2
    assert len(app.get("download_button")) == 1
    link_labels = {item.label for item in app.get("link_button")}
    assert "Google" in link_labels
    assert "Google Scholar · відкрити сайт" in link_labels
    assert "НРАТ · відкрити сайт" in link_labels


def test_gate_screen_keeps_zero_channel_and_engine_counters_visible() -> None:
    app = _run_app_with_pdf()
    frames = [item.value for item in app.get("dataframe")]
    usefulness = next(frame for frame in frames if "Тип запиту" in frame.columns)
    assert set(usefulness["Тип запиту"]) == {
        "Авторське положення",
        "Наукова новизна",
        "Емпіричні дані",
        "Ознаки перекладу",
        "Рідкісна словоформа",
        "Довге змістовне речення",
    }
    novelty_row = usefulness["Тип запиту"] == "Наукова новизна"
    assert usefulness.loc[novelty_row, "Перевірено"].iloc[0] == 0
    captions = "\n".join(item.value for item in app.caption)
    assert "Google: 0" in captions and "НРАТ: 0" in captions


def test_gate_ui_override_rebuilds_the_section_map() -> None:
    app = _run_app_with_pdf()
    app.selectbox(key="search_override_kind").set_value("intro")
    app.button(key="search_apply_override").click().run(timeout=30)
    assert not app.exception
    result = app.session_state["search_result"]
    assert result.document.applied_overrides
    assert any(section.kind == SectionKind.INTRO for section in result.document.sections)

    app.button(key="search_reset_overrides").click().run(timeout=30)
    reset_result = app.session_state["search_result"]
    assert reset_result.document.applied_overrides == ()
    assert any(section.kind == SectionKind.CHAPTER for section in reset_result.document.sections)
