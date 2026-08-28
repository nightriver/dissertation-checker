"""
Модульні тести переходів статусу та JSON-проєкту `search/state.py` (§18).
Крок 3 §22 зафіксував сам об'єкт стану і чисті переходи (§18.1); крок 13
добудовує JSON-схему, транзакційну валідацію допуску (§18.3) і зіставлення
запитів при імпорті (§18.2).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from search.query_builder import build_search_result
from search.state import (
    CURRENT_SCHEMA_VERSION,
    ImportRejected,
    ImportRejectReason,
    QueryState,
    UnmatchedRecord,
    add_failed_engine,
    apply_project,
    export_project,
    initial_state,
    is_absolute_http_url,
    is_counted_as_checked,
    mark_found,
    mark_no_result,
    mark_unchecked,
    parse_project,
    validate_project,
)
from search.types import (
    Confidence,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SectionInfo,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
    SentenceDonor,
    SourceSpan,
    TextZone,
    ZoneSpan,
)
from search.normalization import normalize_text, tokenize
from search.sentences import split_sentences


def test_initial_state_is_unchecked_with_empty_fields():
    state = initial_state("q1")
    assert state.query_id == "q1"
    assert state.status == "unchecked"
    assert state.needs_review is False
    assert state.found_engine is None
    assert state.source_url is None
    assert state.failed_engines == ()
    assert state.comment == ""


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/x", True),
        ("http://example.com/x", True),
        ("ftp://example.com/x", False),
        ("not a url", False),
        ("example.com", False),
    ],
)
def test_is_absolute_http_url(url, expected):
    assert is_absolute_http_url(url) is expected


def test_mark_found_requires_non_empty_engine():
    state = initial_state("q1")
    with pytest.raises(ValueError):
        mark_found(state, found_engine="")


def test_mark_found_rejects_non_absolute_source_url():
    state = initial_state("q1")
    with pytest.raises(ValueError):
        mark_found(state, found_engine="Google", source_url="not-a-url")


def test_mark_found_sets_engine_and_url():
    state = initial_state("q1")
    updated = mark_found(state, found_engine="Google", source_url="https://example.com/doc")
    assert updated.status == "found"
    assert updated.found_engine == "Google"
    assert updated.source_url == "https://example.com/doc"


def test_mark_no_result_clears_engine_and_url_but_keeps_failed_engines_and_comment():
    state = QueryState(
        query_id="q1",
        status="found",
        found_engine="Google",
        source_url="https://example.com/doc",
        failed_engines=("yandex",),
        comment="старий коментар",
    )
    updated = mark_no_result(state)
    assert updated.status == "no_result"
    assert updated.found_engine is None
    assert updated.source_url is None
    assert updated.failed_engines == ("yandex",)
    assert updated.comment == "старий коментар"


def test_mark_no_result_overwrites_comment_when_a_new_one_is_given():
    state = mark_found(initial_state("q1"), found_engine="Google")
    updated = mark_no_result(state, comment="новий коментар")
    assert updated.comment == "новий коментар"


def test_mark_unchecked_clears_engine_and_url():
    state = mark_found(initial_state("q1"), found_engine="Google", source_url="https://example.com/doc")
    updated = mark_unchecked(state)
    assert updated.status == "unchecked"
    assert updated.found_engine is None
    assert updated.source_url is None


def test_add_failed_engine_is_idempotent_and_preserves_order():
    state = initial_state("q1")
    state = add_failed_engine(state, "yandex")
    state = add_failed_engine(state, "elibrary")
    state = add_failed_engine(state, "yandex")
    assert state.failed_engines == ("yandex", "elibrary")


def test_is_counted_as_checked_matches_18_1():
    assert is_counted_as_checked(initial_state("q1")) is False
    found = mark_found(initial_state("q1"), found_engine="Google")
    assert is_counted_as_checked(found) is True
    no_result = mark_no_result(initial_state("q1"))
    assert is_counted_as_checked(no_result) is True
    needs_review = QueryState(query_id="q1", status="found", found_engine="Google", needs_review=True)
    assert is_counted_as_checked(needs_review) is False


# ---------------------------------------------------------------------------
# JSON-проєкт: export/parse/validate/apply (§18.2, §18.3, крок 13)
# ---------------------------------------------------------------------------

_DOC_SHA = hashlib.sha256(b"test-state-json-project").hexdigest()
_SENTENCE = (
    "Ми пропонуємо, на нашу думку, важливе рішення для реформування "
    "вітчизняного законодавства."
)


def _build_document(text: str = _SENTENCE, *, sha: str = _DOC_SHA) -> SearchDocument:
    """Мінімальний реальний `SearchDocument` з одним CHAPTER-реченням."""
    section_id = "sec-000"
    block_id = "blk-00000"
    normalized = normalize_text(text)
    tokens = tokenize(text, normalized)
    zone_spans = (ZoneSpan(0, len(text), TextZone.AUTHOR_TEXT, Confidence.MEDIUM, "test"),)
    block = SearchBlock(
        block_id=block_id,
        raw_text=text,
        normalized=normalized,
        tokens=tokens,
        section_id=section_id,
        heading_path=(),
        physical_page=1,
        block_index=0,
        zone_spans=zone_spans,
    )
    author_words = sum(1 for t in tokens if t.is_word)
    section = SectionInfo(
        section_id=section_id,
        kind=SectionKind.CHAPTER,
        ordinal=None,
        heading="",
        block_start=0,
        block_end=1,
        physical_pages=(1,),
        author_words=author_words,
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        confidence=Confidence.MEDIUM,
    )
    sentences = []
    for ordinal, (start, end) in enumerate(split_sentences(text)):
        s_raw = text[start:end]
        s_normalized = normalize_text(s_raw).text
        author_word_count = sum(1 for t in tokenize(s_raw, normalize_text(s_raw)) if t.is_word)
        donor_id = hashlib.sha256(f"{sha}|0|{s_normalized}|0".encode("utf-8")).hexdigest()
        sentences.append(
            SentenceDonor(
                donor_id=donor_id,
                block_id=block_id,
                section_id=section_id,
                sentence_ordinal=ordinal,
                occurrence_index=0,
                source=SourceSpan(parts=(RawSpan(block_id, 1, start, end),)),
                raw_text=s_raw,
                normalized_text=s_normalized,
                author_word_count=author_word_count,
            )
        )
    return SearchDocument(
        document_sha256=sha,
        parser_version="test-parser-1",
        n_pages=1,
        pages=(),
        expected_body_pages=0,
        extractable_body_pages=0,
        coverage_ratio=0.0,
        blocks=(block,),
        sections=(section,),
        sentences=tuple(sentences),
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.LOW,
        applied_overrides=(),
    )


def test_current_schema_version_is_one():
    assert CURRENT_SCHEMA_VERSION == 1


def test_export_project_contains_all_format_fields_with_empty_lists():
    document = _build_document()
    result = build_search_result(document)
    payload = export_project(document=document, result=result, states={}, app_version="0.0")

    assert payload["schema_version"] == 1
    assert payload["app_version"] == "0.0"
    assert payload["parser_version"] == document.parser_version
    assert payload["algo_version"] == result.algo_version
    assert payload["dictionary_version"] == result.dictionary_version
    assert payload["file"]["sha256"] == document.document_sha256
    assert payload["section_overrides"] == []
    assert payload["unmatched"] == []
    assert set(payload["queries"]) == {q.query_id for q in result.queries}


def test_export_project_file_name_defaults_to_empty_and_is_purely_informational():
    document = _build_document()
    result = build_search_result(document)

    default_payload = export_project(document=document, result=result, states={}, app_version="0.0")
    assert default_payload["file"]["name"] == ""

    named_payload = export_project(
        document=document, result=result, states={}, app_version="0.0", file_name="dysertatsiya.pdf"
    )
    assert named_payload["file"]["name"] == "dysertatsiya.pdf"

    # file_name на допуск не впливає: із правильним sha256, але сторонньою
    # (навіть безглуздою) назвою файл усе одно проходить валідацію.
    named_payload["file"]["name"] = "зовсім інша, навіть безглузда назва.pdf"
    named_payload["queries"] = {}
    validate_project(named_payload, document=document)  # не піднімає


def test_round_trip_restores_all_states():
    document = _build_document()
    result = build_search_result(document)
    assert len(result.queries) >= 1

    states = {}
    for query in result.queries:
        states[query.query_id] = mark_found(
            initial_state(query.query_id), found_engine="google", source_url="https://example.com/x"
        )

    payload = export_project(document=document, result=result, states=states, app_version="1.0")
    raw = json.dumps(payload)

    parsed = parse_project(raw)
    import_result = apply_project(parsed, document=document, queries=result.queries)

    assert import_result.restored_count == len(result.queries)
    assert import_result.needs_review_count == 0
    assert import_result.unmatched == ()
    for query in result.queries:
        restored = import_result.states[query.query_id]
        assert restored.status == "found"
        assert restored.found_engine == "google"
        assert restored.source_url == "https://example.com/x"


def test_empty_project_round_trips_without_queries():
    document = _build_document(text="Замало слів.")
    result = build_search_result(document)
    assert result.queries == ()

    payload = export_project(document=document, result=result, states={}, app_version="1.0")
    parsed = parse_project(json.dumps(payload))
    import_result = apply_project(parsed, document=document, queries=result.queries)
    assert import_result.restored_count == 0
    assert import_result.states == {}


def test_export_parts_do_not_leak_raw_coordinates():
    document = _build_document()
    result = build_search_result(document)
    payload = export_project(document=document, result=result, states={}, app_version="1.0")
    for record in payload["queries"].values():
        for part in record["parts"]:
            assert set(part) == {"text", "origin", "origin_id"}


def test_export_is_deterministic():
    document = _build_document()
    result = build_search_result(document)
    first = export_project(document=document, result=result, states={}, app_version="1.0")
    second = export_project(document=document, result=result, states={}, app_version="1.0")
    assert first == second
    assert list(first["queries"]) == list(second["queries"])


def test_parse_project_rejects_malformed_json():
    with pytest.raises(ImportRejected) as exc_info:
        parse_project("{not valid json")
    assert exc_info.value.reason is ImportRejectReason.MALFORMED_JSON


def test_validate_project_rejects_missing_schema_version():
    document = _build_document()
    payload = {"file": {"sha256": document.document_sha256}, "parser_version": document.parser_version}
    with pytest.raises(ImportRejected) as exc_info:
        validate_project(payload, document=document)
    assert exc_info.value.reason is ImportRejectReason.SCHEMA_MISSING


@pytest.mark.parametrize("bad_version", [0, 2])
def test_validate_project_rejects_older_and_newer_schema(bad_version):
    document = _build_document()
    payload = {
        "schema_version": bad_version,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
    }
    with pytest.raises(ImportRejected) as exc_info:
        validate_project(payload, document=document)
    assert exc_info.value.reason is ImportRejectReason.SCHEMA_MISMATCH


def test_validate_project_rejects_file_mismatch():
    document = _build_document()
    payload = {
        "schema_version": 1,
        "file": {"sha256": "deadbeef"},
        "parser_version": document.parser_version,
    }
    with pytest.raises(ImportRejected) as exc_info:
        validate_project(payload, document=document)
    assert exc_info.value.reason is ImportRejectReason.FILE_MISMATCH


def test_validate_project_rejects_parser_version_mismatch():
    document = _build_document()
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": "other-parser",
    }
    with pytest.raises(ImportRejected) as exc_info:
        validate_project(payload, document=document)
    assert exc_info.value.reason is ImportRejectReason.PARSER_VERSION_MISMATCH


def test_validate_project_allows_algo_and_dictionary_version_drift():
    document = _build_document()
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
        "algo_version": "old-algo",
        "dictionary_version": "old-dict",
        "app_version": "old-app",
        "section_overrides": [],
    }
    validate_project(payload, document=document)  # не піднімає


def test_validate_project_rejects_override_not_found():
    document = _build_document()
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
        "section_overrides": [
            {"action": "set_kind", "heading_block_id": "unknown-block", "section_kind": "intro"}
        ],
    }
    with pytest.raises(ImportRejected) as exc_info:
        validate_project(payload, document=document)
    assert exc_info.value.reason is ImportRejectReason.OVERRIDE_NOT_FOUND


def test_apply_project_is_transactional_on_rejection():
    document = _build_document()
    result = build_search_result(document)
    payload = {"schema_version": 2}  # SCHEMA_MISMATCH
    with pytest.raises(ImportRejected):
        apply_project(payload, document=document, queries=result.queries)
    # apply_project не мутує зовнішній стан — перевіряємо, що виняток
    # піднявся до будь-якої побудови states/unmatched (нема побічних ефектів
    # на передані аргументи queries/document).
    assert result.queries == build_search_result(document).queries


def test_apply_project_matches_by_donor_key_when_query_text_changed():
    document = _build_document()
    result = build_search_result(document)
    query = result.queries[0]

    old_record = {
        "donor_id": query.donor_id,
        "primary_channel": query.primary_channel.value,
        "subtype": query.subtype,
        "query_text": query.query_text + " ЗМІНЕНО ЩЕ БІЛЬШЕ",
        "parts": [],
        "status": "found",
        "needs_review": False,
        "previous_status": None,
        "prior_snapshot": None,
        "comment": "старий коментар",
        "source_url": "https://example.com/old",
        "found_engine": "google",
        "failed_engines": [],
    }
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
        "section_overrides": [],
        "queries": {"old-query-id-not-matching-anything": old_record},
        "unmatched": [],
    }
    import_result = apply_project(payload, document=document, queries=result.queries)

    assert import_result.restored_count == 0
    assert import_result.needs_review_count == 1
    migrated = import_result.states[query.query_id]
    assert migrated.status == "unchecked"
    assert migrated.needs_review is True
    assert migrated.previous_status == "found"
    assert migrated.prior_snapshot
    assert migrated.comment == "старий коментар"
    assert migrated.source_url == "https://example.com/old"
    assert is_counted_as_checked(migrated) is False


def test_apply_project_sends_unmatched_donor_to_unmatched_list():
    document = _build_document()
    result = build_search_result(document)

    orphan_record = {
        "donor_id": "no-such-donor",
        "primary_channel": "A",
        "subtype": None,
        "query_text": "щось",
        "parts": [],
        "status": "found",
        "needs_review": False,
        "previous_status": None,
        "prior_snapshot": None,
        "comment": "",
        "source_url": None,
        "found_engine": "google",
        "failed_engines": [],
    }
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
        "section_overrides": [],
        "queries": {"orphan-query-id": orphan_record},
        "unmatched": [],
    }
    import_result = apply_project(payload, document=document, queries=result.queries)

    assert import_result.states == {}
    assert len(import_result.unmatched) == 1
    unmatched = import_result.unmatched[0]
    assert isinstance(unmatched, UnmatchedRecord)
    assert unmatched.query_id == "orphan-query-id"
    assert unmatched.donor_id == "no-such-donor"
    assert unmatched.payload == orphan_record


def test_apply_project_carries_top_level_unmatched_after_fresh_unmatched():
    """
    §18.3: `ImportResult.unmatched` — спершу свіжі несходинки (донор не
    знайдений саме в цьому імпорті), потім старі, перенесені з
    верхньорівневого поля `unmatched` вхідного файлу, у їхньому порядку.
    """
    document = _build_document()
    result = build_search_result(document)

    fresh_orphan = {
        "donor_id": "fresh-orphan-donor",
        "primary_channel": "A",
        "subtype": None,
        "query_text": "новий сирота",
        "parts": [],
        "status": "unchecked",
        "needs_review": False,
        "previous_status": None,
        "prior_snapshot": None,
        "comment": "",
        "source_url": None,
        "found_engine": None,
        "failed_engines": [],
    }
    old_carried_payload = {"будь-яка": "структура", "яку не інтерпретуємо": 1}
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
        "section_overrides": [],
        "queries": {"fresh-orphan-id": fresh_orphan},
        "unmatched": [
            {"query_id": "old-1", "donor_id": "old-donor-1", "payload": old_carried_payload},
            {"payload": {"навіть без query_id і donor_id": True}},
        ],
    }
    import_result = apply_project(payload, document=document, queries=result.queries)

    assert [u.query_id for u in import_result.unmatched] == ["fresh-orphan-id", "old-1", ""]
    assert import_result.unmatched[1].donor_id == "old-donor-1"
    assert import_result.unmatched[1].payload == old_carried_payload
    # Елемент без query_id/donor_id не викидається — порожній рядок замість
    # відсутнього поля, payload зберігається повністю.
    assert import_result.unmatched[2].query_id == ""
    assert import_result.unmatched[2].donor_id == ""
    assert import_result.unmatched[2].payload == {"навіть без query_id і donor_id": True}


def test_apply_project_round_trips_section_overrides_as_enums():
    document = _build_document()
    result = build_search_result(document)
    block_id = document.blocks[0].block_id
    payload = {
        "schema_version": 1,
        "file": {"sha256": document.document_sha256},
        "parser_version": document.parser_version,
        "section_overrides": [
            {"action": "set_kind", "heading_block_id": block_id, "section_kind": "intro"}
        ],
        "queries": {},
        "unmatched": [],
    }
    import_result = apply_project(payload, document=document, queries=result.queries)
    assert import_result.section_overrides == (
        SectionOverride(
            action=SectionOverrideAction.SET_KIND,
            heading_block_id=block_id,
            section_kind=SectionKind.INTRO,
        ),
    )
