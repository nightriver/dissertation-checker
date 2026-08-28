"""
Шлюз кроку 13 (`steps/step-13.md`): JSON-схема проєкту, атомарний імпорт
і перенесення статусів триажу в `search/state.py` (§18).

Пише і перевіряє цей шлюз незалежний оракул, який **не бачив** реалізації
`search/state.py` крок 13 — лише пакет кроку. Вхідні `SearchDocument` /
`SearchQuery` / `SearchResult` збираються тут вручну мінімальними
конструкторами; очікувані значення виписані з тексту пакета, а не з
запуску коду.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from search.state import (
    CURRENT_SCHEMA_VERSION,
    ImportRejected,
    ImportRejectReason,
    QueryState,
    add_failed_engine,
    apply_project,
    export_project,
    initial_state,
    is_absolute_http_url,
    is_counted_as_checked,
    mark_found,
    mark_no_result,
    parse_project,
    validate_project,
)
from search.types import (
    CalqueMetrics,
    CandidateMetrics,
    Channel,
    Confidence,
    DedupMetrics,
    Language,
    NormalizedText,
    QueryPart,
    QueryPartOrigin,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchQuery,
    SearchResult,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
    SourceSpan,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Мінімальні будівники — лише поля, потрібні для стану/JSON, решта — заглушки
# ---------------------------------------------------------------------------


def _span(block_id: str = "b0", page: int = 1, start: int = 0, end: int = 1) -> SourceSpan:
    return SourceSpan(parts=(RawSpan(block_id=block_id, physical_page=page, raw_start=start, raw_end=end),))


def _make_block(block_id: str, raw_text: str = "", block_index: int = 0) -> SearchBlock:
    return SearchBlock(
        block_id=block_id,
        raw_text=raw_text,
        normalized=NormalizedText(text="", origins=()),
        tokens=(),
        section_id="s1",
        heading_path=(),
        physical_page=1,
        block_index=block_index,
        zone_spans=(),
    )


def _make_document(
    *,
    sha256: str = "sha-doc-a",
    parser_version: str = "parser-1.0",
    blocks: tuple[SearchBlock, ...] = (),
) -> SearchDocument:
    return SearchDocument(
        document_sha256=sha256,
        parser_version=parser_version,
        n_pages=1,
        pages=(),
        expected_body_pages=0,
        extractable_body_pages=0,
        coverage_ratio=1.0,
        blocks=blocks,
        sections=(),
        sentences=(),
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.HIGH,
        applied_overrides=(),
    )


def _make_query(
    *,
    query_id: str,
    donor_id: str,
    query_text: str = "приклад запиту",
    primary_channel: Channel = Channel.K,
    subtype: str | None = "K2",
    parts: tuple[QueryPart, ...] | None = None,
) -> SearchQuery:
    span = _span()
    if parts is None:
        parts = (
            QueryPart(text=query_text, origin=QueryPartOrigin.CALQUE_RULE, origin_id="K001", source=span),
        )
    return SearchQuery(
        donor_id=donor_id,
        query_id=query_id,
        block_id="b0",
        section_id="s1",
        sentence_ordinal=0,
        primary_channel=primary_channel,
        attributed_channels=(primary_channel,),
        subtype=subtype,
        query_language=Language.UK,
        selection_stage=1,
        query_text=query_text,
        parts=parts,
        donor_text=query_text,
        donor_source=span,
        pdf_anchor=query_text,
        pdf_anchor_source=span,
        physical_page=1,
        score=1.0,
        rank_score=1.0,
        evidence_ids=(),
        reasons=(),
    )


def _make_result(
    document: SearchDocument,
    queries: tuple[SearchQuery, ...],
    *,
    algo_version: str = "algo-1.0",
    dictionary_version: str = "dict-1.0",
) -> SearchResult:
    return SearchResult(
        document=document,
        algo_version=algo_version,
        dictionary_version=dictionary_version,
        queries=queries,
        shortfalls=(),
        signal_hits=(),
        calque_metrics=CalqueMetrics(
            author_words=0, tier1_hits=0, tier2_hits=0, tier3_hits=0,
            tier1_density=0.0, excluded_zone_hits=(),
        ),
        candidate_metrics=CandidateMetrics(
            generated_by_channel=(), retained_primary_by_channel=(),
            attributed_by_channel=(), rejected_by_reason=(),
        ),
        dedup_metrics=DedupMetrics(
            input_count=0, component_count=0, removed_count=0, merged_channel_attributions=0,
        ),
        warnings=(),
    )


def _query_record(
    query: SearchQuery,
    *,
    status: str = "unchecked",
    needs_review: bool = False,
    previous_status: str | None = None,
    prior_snapshot: str | None = None,
    comment: str = "",
    source_url: str | None = None,
    found_engine: str | None = None,
    failed_engines: tuple[str, ...] = (),
) -> dict:
    """Ручна побудова запису `queries[query_id]` за розділом «Формат» пакета."""
    return {
        "donor_id": query.donor_id,
        "primary_channel": query.primary_channel.value,
        "subtype": query.subtype,
        "query_text": query.query_text,
        "parts": [
            {"text": p.text, "origin": p.origin.value, "origin_id": p.origin_id}
            for p in query.parts
        ],
        "status": status,
        "needs_review": needs_review,
        "previous_status": previous_status,
        "prior_snapshot": prior_snapshot,
        "comment": comment,
        "source_url": source_url,
        "found_engine": found_engine,
        "failed_engines": list(failed_engines),
    }


def _base_payload(
    document: SearchDocument,
    queries_records: dict | None = None,
    *,
    schema_version: int = 1,
    app_version: str = "app-1.0",
    parser_version: str | None = None,
    algo_version: str = "algo-1.0",
    dictionary_version: str = "dict-1.0",
    sha256: str | None = None,
    file_name: str = "dissertation.pdf",
    section_overrides: list | None = None,
    unmatched: list | None = None,
) -> dict:
    """Ручна побудова цілого проєктного JSON строго за розділом «Формат»."""
    return {
        "schema_version": schema_version,
        "app_version": app_version,
        "parser_version": document.parser_version if parser_version is None else parser_version,
        "algo_version": algo_version,
        "dictionary_version": dictionary_version,
        "file": {
            "sha256": document.document_sha256 if sha256 is None else sha256,
            "name": file_name,
        },
        "section_overrides": [] if section_overrides is None else section_overrides,
        "queries": {} if queries_records is None else queries_records,
        "unmatched": [] if unmatched is None else unmatched,
    }


# ---------------------------------------------------------------------------
# 1. Константа схеми
# ---------------------------------------------------------------------------


def test_current_schema_version_is_1():
    """§18.3: `CURRENT_SCHEMA_VERSION = 1`."""
    assert CURRENT_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# 2. Формат експорту
# ---------------------------------------------------------------------------


def test_export_project_contains_all_format_fields():
    """Експорт повертає словник з усіма ключами розділу «Формат», включно
    з порожніми `unmatched` і `section_overrides` для проєкту без запитів."""
    document = _make_document(sha256="sha-empty", parser_version="parser-9.9")
    result = _make_result(document, ())
    payload = export_project(document=document, result=result, states={}, app_version="ui-3.4.5")

    assert payload["schema_version"] == CURRENT_SCHEMA_VERSION
    assert payload["app_version"] == "ui-3.4.5"
    assert payload["parser_version"] == "parser-9.9"
    assert payload["algo_version"] == "algo-1.0"
    assert payload["dictionary_version"] == "dict-1.0"
    assert payload["file"]["sha256"] == "sha-empty"
    assert "name" in payload["file"]
    assert payload["section_overrides"] == []
    assert payload["queries"] == {}
    assert payload["unmatched"] == []


# ---------------------------------------------------------------------------
# 3. Round-trip
# ---------------------------------------------------------------------------


def test_export_import_round_trip_restores_all_states():
    """Експорт → `json.dumps` → `parse_project` → `apply_project` на тому
    самому документі й тих самих запитах відновлює всі стани без відхилення."""
    q1 = _make_query(query_id="Q1", donor_id="D1", query_text="перший запит")
    q2 = _make_query(
        query_id="Q2", donor_id="D2", query_text="другий запит",
        primary_channel=Channel.B, subtype=None,
    )
    document = _make_document(sha256="sha-roundtrip", parser_version="parser-1.0")
    result = _make_result(document, (q1, q2))
    states = {
        "Q1": mark_found(initial_state("Q1"), found_engine="google", source_url="https://example.com/a"),
        "Q2": mark_no_result(initial_state("Q2"), comment="нічого не знайдено"),
    }
    payload = export_project(document=document, result=result, states=states, app_version="ui-1.0")
    raw = json.dumps(payload, ensure_ascii=False)
    parsed = parse_project(raw)
    imported = apply_project(parsed, document=document, queries=(q1, q2))

    assert imported.restored_count == 2
    assert imported.needs_review_count == 0
    assert imported.states["Q1"].status == "found"
    assert imported.states["Q1"].found_engine == "google"
    assert imported.states["Q1"].source_url == "https://example.com/a"
    assert imported.states["Q2"].status == "no_result"
    assert imported.states["Q2"].comment == "нічого не знайдено"


# ---------------------------------------------------------------------------
# 4-8. Інваріанти моделі стану (§18.1)
# ---------------------------------------------------------------------------


def test_found_status_requires_non_empty_engine():
    """Інваріант: `found` без `found_engine` відхиляється моделлю."""
    with pytest.raises(ValueError):
        mark_found(initial_state("q"), found_engine="")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("www.example.com", False),
        ("https://example.com", True),
        ("ftp://example.com", False),
    ],
)
def test_source_url_scheme_invariant(url, expected):
    """`source_url` без схеми відхиляється; `https://` приймається; `ftp://` — ні."""
    assert is_absolute_http_url(url) is expected


def test_no_result_clears_engine_and_url():
    state = mark_found(initial_state("q"), found_engine="google", source_url="https://example.com/x")
    updated = mark_no_result(state)
    assert updated.found_engine is None
    assert updated.source_url is None


def test_no_result_keeps_failed_engines_and_comment():
    state = QueryState(
        query_id="q", status="found", found_engine="google",
        failed_engines=("bing",), comment="нотатка",
    )
    updated = mark_no_result(state)
    assert updated.failed_engines == ("bing",)
    assert updated.comment == "нотатка"


def test_failed_engines_do_not_change_status():
    """Технічна недоступність рушія — це `failed_engines`, а не статус запиту."""
    state = initial_state("q")
    state = add_failed_engine(state, "bing")
    state = add_failed_engine(state, "yandex")
    assert state.status == "unchecked"


# ---------------------------------------------------------------------------
# 9-14. Правила допуску імпорту
# ---------------------------------------------------------------------------


def test_malformed_json_is_rejected():
    with pytest.raises(ImportRejected) as exc:
        parse_project('{"schema_version": 1, "queries": ')
    assert exc.value.reason == ImportRejectReason.MALFORMED_JSON


def test_missing_schema_version_is_rejected():
    document = _make_document()
    payload = _base_payload(document)
    del payload["schema_version"]
    with pytest.raises(ImportRejected) as exc:
        validate_project(payload, document=document)
    assert exc.value.reason == ImportRejectReason.SCHEMA_MISSING


def test_schema_version_zero_is_rejected_as_mismatch():
    document = _make_document()
    payload = _base_payload(document, schema_version=0)
    with pytest.raises(ImportRejected) as exc:
        validate_project(payload, document=document)
    assert exc.value.reason == ImportRejectReason.SCHEMA_MISMATCH


def test_newer_schema_version_is_rejected_as_mismatch():
    """Новіша схема (2) відхиляється так само, як старіша — мігратора нема."""
    document = _make_document()
    payload = _base_payload(document, schema_version=2)
    with pytest.raises(ImportRejected) as exc:
        validate_project(payload, document=document)
    assert exc.value.reason == ImportRejectReason.SCHEMA_MISMATCH


def test_wrong_file_sha256_is_rejected():
    document = _make_document(sha256="sha-real")
    payload = _base_payload(document, sha256="sha-other-file")
    with pytest.raises(ImportRejected) as exc:
        validate_project(payload, document=document)
    assert exc.value.reason == ImportRejectReason.FILE_MISMATCH


def test_wrong_parser_version_is_rejected():
    document = _make_document(parser_version="parser-2.0")
    payload = _base_payload(document, parser_version="parser-1.0-old")
    with pytest.raises(ImportRejected) as exc:
        validate_project(payload, document=document)
    assert exc.value.reason == ImportRejectReason.PARSER_VERSION_MISMATCH


# ---------------------------------------------------------------------------
# 15-17. Некритичні розбіжності версій
# ---------------------------------------------------------------------------


def test_algo_version_mismatch_does_not_block_import():
    q1 = _make_query(query_id="Q1", donor_id="D1")
    document = _make_document()
    payload = _base_payload(
        document, {"Q1": _query_record(q1, status="found", found_engine="google")},
        algo_version="algo-OTHER",
    )
    result = apply_project(payload, document=document, queries=(q1,))
    assert result.states["Q1"].status == "found"
    assert result.restored_count == 1


def test_dictionary_version_mismatch_does_not_block_import():
    q1 = _make_query(query_id="Q1", donor_id="D1")
    document = _make_document()
    payload = _base_payload(
        document, {"Q1": _query_record(q1, status="found", found_engine="google")},
        dictionary_version="dict-OTHER",
    )
    result = apply_project(payload, document=document, queries=(q1,))
    assert result.states["Q1"].status == "found"
    assert result.restored_count == 1


def test_app_version_mismatch_is_informational_only():
    """`app_version` не бере участі у рішенні про допуск імпорту."""
    q1 = _make_query(query_id="Q1", donor_id="D1")
    document = _make_document()
    payload = _base_payload(
        document, {"Q1": _query_record(q1, status="found", found_engine="google")},
        app_version="ui-9.9.9-other",
    )
    result = apply_project(payload, document=document, queries=(q1,))
    assert result.states["Q1"].status == "found"
    assert result.restored_count == 1


# ---------------------------------------------------------------------------
# 18. Транзакційність
# ---------------------------------------------------------------------------


def test_rejected_import_does_not_corrupt_a_later_successful_import():
    """Транзакційність (§18.3): відхилений через чужий SHA-256 імпорт не
    залишає слідів — `ImportRejected` несе лише `reason` (жодного часткового
    `states` при собі), а наступний коректний виклик `apply_project` над тими
    самими вхідними даними відновлює стан так, ніби невдалої спроби не було.
    Наполовину застосований чужий JSON гірший за відхилений — тому тут
    перевіряється не сам факт винятку, а відсутність будь-якого просочення."""
    q1 = _make_query(query_id="Q1", donor_id="D1")
    q2 = _make_query(
        query_id="Q2", donor_id="D2", query_text="другий запит",
        primary_channel=Channel.B, subtype=None,
    )
    document = _make_document(sha256="sha-txn")
    records = {
        "Q1": _query_record(q1, status="found", found_engine="google", source_url="https://example.com/found"),
        "Q2": _query_record(q2, status="no_result", comment="перевірено"),
    }
    good_payload = _base_payload(document, records, sha256="sha-txn")
    bad_payload = _base_payload(document, records, sha256="sha-INTRUDER")

    with pytest.raises(ImportRejected) as exc:
        apply_project(bad_payload, document=document, queries=(q1, q2))
    assert exc.value.reason == ImportRejectReason.FILE_MISMATCH
    assert not hasattr(exc.value, "states")

    result = apply_project(good_payload, document=document, queries=(q1, q2))
    assert result.restored_count == 2
    assert result.states["Q1"].status == "found"
    assert result.states["Q2"].status == "no_result"


# ---------------------------------------------------------------------------
# 19-21. Зіставлення запитів (§18.2)
# ---------------------------------------------------------------------------


def test_matching_by_query_id_restores_state_fully():
    q1 = _make_query(query_id="Q1", donor_id="D1", query_text="точний запит")
    document = _make_document()
    record = _query_record(
        q1, status="found", found_engine="google",
        source_url="https://example.com/exact", comment="перевірено вручну",
    )
    payload = _base_payload(document, {"Q1": record})
    result = apply_project(payload, document=document, queries=(q1,))

    state = result.states["Q1"]
    assert state.status == "found"
    assert state.comment == "перевірено вручну"
    assert state.source_url == "https://example.com/exact"
    assert state.found_engine == "google"
    assert state.needs_review is False


def _build_migration_scenario():
    current = _make_query(
        query_id="Q_NEW", donor_id="D1", query_text="новий текст запиту",
        primary_channel=Channel.K, subtype="K2",
    )
    document = _make_document()
    record = {
        "donor_id": "D1",
        "primary_channel": "K",
        "subtype": "K2",
        "query_text": "старий текст запиту",  # інший текст → інший query_id
        "parts": [{"text": "старий текст запиту", "origin": "calque_rule", "origin_id": "K001"}],
        "status": "found",
        "needs_review": False,
        "previous_status": None,
        "prior_snapshot": None,
        "comment": "стара нотатка",
        "source_url": "https://example.com/old",
        "found_engine": "google",
        "failed_engines": [],
    }
    payload = _base_payload(document, {"Q_OLD": record})
    result = apply_project(payload, document=document, queries=(current,))
    return result, current


def test_matching_by_donor_channel_subtype_migrates_changed_query():
    """§18.2 п.2: `donor_id + primary_channel + subtype` збіглися, але текст
    запиту змінився — новий query_id іде на `unchecked/needs_review`, а старе
    рішення зберігається у знімку."""
    result, current = _build_migration_scenario()
    state = result.states[current.query_id]

    assert state.status == "unchecked"
    assert state.needs_review is True
    assert state.previous_status == "found"
    assert state.prior_snapshot
    assert state.comment == "стара нотатка"
    assert state.source_url == "https://example.com/old"


def test_migrated_record_is_not_counted_as_checked():
    """Мігрована відмітка не рахується перевіреною в метриках."""
    result, current = _build_migration_scenario()
    state = result.states[current.query_id]
    assert is_counted_as_checked(state) is False


# ---------------------------------------------------------------------------
# 22-23. `unmatched`
# ---------------------------------------------------------------------------


def test_unmatched_donor_does_not_touch_current_queries():
    q1 = _make_query(query_id="Q1", donor_id="D1")
    document = _make_document()
    matched_record = _query_record(q1, status="found", found_engine="google")
    orphan_record = {
        "donor_id": "D-ZOMBIE",
        "primary_channel": "N",
        "subtype": None,
        "query_text": "текст без донора в поточному документі",
        "parts": [],
        "status": "found",
        "needs_review": False,
        "previous_status": None,
        "prior_snapshot": None,
        "comment": "",
        "source_url": None,
        "found_engine": "bing",
        "failed_engines": [],
    }
    payload = _base_payload(document, {"Q1": matched_record, "Q_ORPHAN": orphan_record})
    result = apply_project(payload, document=document, queries=(q1,))

    assert result.states["Q1"].status == "found"
    assert len(result.unmatched) == 1
    unmatched = result.unmatched[0]
    assert unmatched.query_id == "Q_ORPHAN"
    assert unmatched.donor_id == "D-ZOMBIE"
    assert unmatched.payload == orphan_record


def test_top_level_unmatched_is_carried_through_unchanged():
    """Запис із поля `unmatched` вхідного JSON (вже нездоланий у минулому
    імпорті) переноситься в результат без втрат і не чіпає поточні картки."""
    q1 = _make_query(query_id="Q1", donor_id="D1")
    document = _make_document()
    carried_payload = {
        "donor_id": "D-LOST-LONG-AGO",
        "primary_channel": "T",
        "subtype": None,
        "query_text": "давно втрачений запит",
        "parts": [],
        "status": "no_result",
        "needs_review": False,
        "previous_status": None,
        "prior_snapshot": None,
        "comment": "",
        "source_url": None,
        "found_engine": None,
        "failed_engines": [],
    }
    carried = {"query_id": "Q_LOST", "donor_id": "D-LOST-LONG-AGO", "payload": carried_payload}
    payload = _base_payload(document, {}, unmatched=[carried])
    result = apply_project(payload, document=document, queries=(q1,))

    assert len(result.unmatched) == 1
    assert result.unmatched[0].query_id == "Q_LOST"
    assert result.unmatched[0].donor_id == "D-LOST-LONG-AGO"
    assert result.unmatched[0].payload == carried_payload
    assert "Q_LOST" not in result.states
    assert all(u.query_id != "Q1" for u in result.unmatched)


# ---------------------------------------------------------------------------
# 24-26. `section_overrides`
# ---------------------------------------------------------------------------


def test_section_overrides_round_trip_as_typed_tuple():
    block_intro = _make_block("b-intro", raw_text="Вступ", block_index=0)
    block_ch1 = _make_block("b-ch1", raw_text="Розділ 1. Щось", block_index=1)
    document = _make_document(blocks=(block_intro, block_ch1))
    overrides_json = [
        {"action": "set_kind", "heading_block_id": "b-intro", "section_kind": "intro"},
        {"action": "exclude_heading", "heading_block_id": "b-ch1", "section_kind": None},
    ]
    payload = _base_payload(document, {}, section_overrides=overrides_json)
    result = apply_project(payload, document=document, queries=())

    assert result.section_overrides == (
        SectionOverride(action=SectionOverrideAction.SET_KIND, heading_block_id="b-intro", section_kind=SectionKind.INTRO),
        SectionOverride(action=SectionOverrideAction.EXCLUDE_HEADING, heading_block_id="b-ch1", section_kind=None),
    )


def test_unknown_override_heading_block_id_rejects_whole_import():
    document = _make_document(blocks=(_make_block("b-real", raw_text="Реальний заголовок"),))
    overrides_json = [{"action": "set_kind", "heading_block_id": "b-does-not-exist", "section_kind": "intro"}]
    payload = _base_payload(document, {}, section_overrides=overrides_json)
    with pytest.raises(ImportRejected) as exc:
        apply_project(payload, document=document, queries=())
    assert exc.value.reason == ImportRejectReason.OVERRIDE_NOT_FOUND


def test_override_is_not_reassigned_by_matching_text_at_a_new_position():
    """Override посилається на `heading_block_id`, якого немає у поточному
    документі. Навіть якщо блок із таким самим текстом існує під іншим
    `block_id` і на іншій позиції — це не рахується збігом, увесь імпорт
    все одно відхиляється."""
    original_block = _make_block("b-intro-v1", raw_text="Вступ", block_index=0)
    payload = _base_payload(
        _make_document(sha256="sha-reposition", blocks=(original_block,)),
        {},
        section_overrides=[{"action": "set_kind", "heading_block_id": "b-intro-v1", "section_kind": "intro"}],
        sha256="sha-reposition",
    )

    reparsed_block = _make_block("b-intro-v2", raw_text="Вступ", block_index=3)
    reparsed_document = _make_document(sha256="sha-reposition", blocks=(reparsed_block,))

    with pytest.raises(ImportRejected) as exc:
        apply_project(payload, document=reparsed_document, queries=())
    assert exc.value.reason == ImportRejectReason.OVERRIDE_NOT_FOUND


# ---------------------------------------------------------------------------
# 27-29. Формат і детермінізм експорту, порожній проєкт
# ---------------------------------------------------------------------------


def test_export_does_not_leak_raw_coordinates_in_parts():
    """У `parts` немає `raw_start`/`raw_end`/`block_id` — лише три поля формату."""
    q1 = _make_query(query_id="Q1", donor_id="D1")
    document = _make_document()
    result = _make_result(document, (q1,))
    payload = export_project(document=document, result=result, states={"Q1": initial_state("Q1")}, app_version="ui-1.0")
    for part in payload["queries"]["Q1"]["parts"]:
        assert set(part.keys()) == {"text", "origin", "origin_id"}


def test_export_project_is_deterministic_and_key_ordered():
    q1 = _make_query(query_id="Q1", donor_id="D1")
    q2 = _make_query(
        query_id="Q2", donor_id="D2", query_text="другий",
        primary_channel=Channel.B, subtype=None,
    )
    document = _make_document()
    result = _make_result(document, (q1, q2))
    states = {"Q1": initial_state("Q1"), "Q2": initial_state("Q2")}

    first = export_project(document=document, result=result, states=states, app_version="ui-1.0")
    second = export_project(document=document, result=result, states=states, app_version="ui-1.0")

    assert json.dumps(first) == json.dumps(second)
    assert list(first["queries"].keys()) == list(second["queries"].keys())


def test_empty_project_exports_and_imports_without_error():
    document = _make_document()
    result = _make_result(document, ())
    payload = export_project(document=document, result=result, states={}, app_version="ui-1.0")
    raw = json.dumps(payload)
    imported = apply_project(parse_project(raw), document=document, queries=())
    assert imported.restored_count == 0


# ---------------------------------------------------------------------------
# 30. Модель без Streamlit
# ---------------------------------------------------------------------------


def test_state_module_never_imports_streamlit():
    """Правило №10 CLAUDE.md: стан триажу не живе у `st.session_state`,
    а `search/state.py` — чиста модель без залежності від Streamlit."""
    source = (ROOT / "search" / "state.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source
