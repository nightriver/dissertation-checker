"""Шлюз кроку 14: чисті картки, секції та зведення PLAN_SEARCH.md §§17–19."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from search.engines import ENGINES
from search.normalization import normalize_text, tokenize
from search.presentation import (
    build_query_card,
    build_search_summary,
    render_highlighted_text,
)
from search.state import QueryState, initial_state
from search.types import (
    BibliographyEntry,
    CalqueMetrics,
    CandidateMetrics,
    Channel,
    Confidence,
    DedupMetrics,
    Language,
    QueryPart,
    QueryPartOrigin,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchQuery,
    SearchResult,
    SectionInfo,
    SectionKind,
    SectionShortfall,
    ShortfallReason,
    SourceSpan,
    TextZone,
    ZoneSpan,
)
from search.ui_logic import DEFAULT_VISIBLE_PER_SECTION, apply_status_action, build_search_screen


TODAY = date(2026, 8, 29)


def _source(block_id: str = "b1", start: int = 0, end: int = 38) -> SourceSpan:
    return SourceSpan((RawSpan(block_id, 1, start, end),))


def _block(block_id: str = "b1", section_id: str = "s1") -> SearchBlock:
    text = "У діючому законодавстві наявні важливі правові прогалини."
    normalized = normalize_text(text)
    return SearchBlock(
        block_id=block_id,
        raw_text=text,
        normalized=normalized,
        tokens=tokenize(text, normalized),
        section_id=section_id,
        heading_path=("РОЗДІЛ 1",),
        physical_page=1,
        block_index=0,
        zone_spans=(ZoneSpan(0, len(text), TextZone.AUTHOR_TEXT, Confidence.HIGH, "gate"),),
    )


def _section(section_id: str = "s1", ordinal: int = 1) -> SectionInfo:
    return SectionInfo(
        section_id=section_id,
        kind=SectionKind.CHAPTER,
        ordinal=ordinal,
        heading=f"РОЗДІЛ {ordinal}",
        block_start=0,
        block_end=1,
        physical_pages=(1,),
        author_words=1000,
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        confidence=Confidence.HIGH,
    )


def _document(*, entry_ordinal: int = 32) -> SearchDocument:
    block = _block()
    entry = BibliographyEntry(
        entry_id="entry-32",
        ordinal=entry_ordinal,
        raw_text="Теория права и государства.",
        source=_source("bib", 0, 29),
        title="Теория права и государства",
        title_source=_source("bib", 0, 27),
        title_confidence=Confidence.HIGH,
        surnames=("Керимов",),
        year=2019,
        language=Language.RU,
        language_evidence="exclusive",
    )
    return SearchDocument(
        document_sha256="0" * 64,
        parser_version="gate",
        n_pages=1,
        pages=(),
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        blocks=(block,),
        sections=(_section(),),
        sentences=(),
        bibliography=(entry,),
        citations=(),
        body_biblio_confidence=Confidence.HIGH,
        applied_overrides=(),
    )


def _query(index: int = 1, channel: Channel = Channel.A) -> SearchQuery:
    donor = "У діючому законодавстві наявні важливі правові прогалини."
    source = _source(end=len(donor))
    text = f"«авторський запит номер {index} містить точну фразу»"
    return SearchQuery(
        donor_id=f"d{index}",
        query_id=f"q{index}",
        block_id="b1",
        section_id="s1",
        sentence_ordinal=index,
        primary_channel=channel,
        attributed_channels=(channel,),
        subtype=None,
        query_language=Language.UK,
        selection_stage=1,
        query_text=text,
        parts=(QueryPart(text, QueryPartOrigin.SOURCE_PHRASE, None, source),),
        donor_text=donor,
        donor_source=source,
        pdf_anchor="діючому законодавстві наявні важливі правові прогалини",
        pdf_anchor_source=source,
        physical_page=1,
        score=4.0,
        rank_score=4.0,
        evidence_ids=(f"e{index}",),
        reasons=("A.phrase.0",),
    )


def _k_query() -> SearchQuery:
    query = _query(3, Channel.K)
    donor = query.donor_text
    start = donor.index("діючому")
    calque_source = _source(start=start, end=start + len("діючому"))
    parts = (
        QueryPart("действующий", QueryPartOrigin.CALQUE_RULE, "diiuchyi", calque_source),
        QueryPart(" ", QueryPartOrigin.SYSTEM_LITERAL, "space", None),
        QueryPart("теория", QueryPartOrigin.RU_REFERENCE, "entry-32", _source("bib", 0, 6)),
    )
    return replace(
        query,
        subtype="K2",
        query_text="действующий теория",
        parts=parts,
        reasons=("K.diiuchyi",),
    )


def _result(queries: tuple[SearchQuery, ...], *, shortfalls=(), warnings=()) -> SearchResult:
    channels = tuple(channel for channel in Channel if channel != Channel.D)
    return SearchResult(
        document=_document(entry_ordinal=1),
        algo_version="gate",
        dictionary_version="gate",
        queries=queries,
        shortfalls=shortfalls,
        signal_hits=(),
        calque_metrics=CalqueMetrics(
            author_words=1000,
            tier1_hits=2,
            tier2_hits=1,
            tier3_hits=0,
            tier1_density=2.0,
            excluded_zone_hits=((TextZone.BIBLIOGRAPHY, 1),),
        ),
        candidate_metrics=CandidateMetrics(
            generated_by_channel=tuple((channel, 0) for channel in channels),
            retained_primary_by_channel=tuple((channel, 0) for channel in channels),
            attributed_by_channel=tuple((channel, 0) for channel in channels),
            rejected_by_reason=(("score_below_threshold_2:A", 3),),
        ),
        dedup_metrics=DedupMetrics(0, 0, 0, 0),
        warnings=warnings,
    )


def test_gate_html_is_escaped_after_spans_are_merged() -> None:
    rendered = render_highlighted_text('<script>&" хвіст', ((0, 8), (5, 10)))
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered and "&quot;" in rendered
    assert rendered.count("<mark>") == 1


def test_gate_card_distinguishes_prefill_from_home_fallback() -> None:
    card = build_query_card(_query(), initial_state("q1"), ENGINES, TODAY)
    google, scholar = card.engine_links[:2]
    assert google.is_prefilled and google.url.startswith("https://www.google.com/search?")
    assert google.action_label == "Google"
    assert not scholar.is_prefilled and scholar.url is None
    assert scholar.target_url == scholar.home_url
    assert "відкрити сайт" in scholar.action_label
    assert scholar.block_reason is not None
    assert scholar.copy_query == card.query_text


def test_gate_k_card_has_exact_form_normative_form_and_ru_reason() -> None:
    card = build_query_card(
        _k_query(), initial_state("q3"), ENGINES, TODAY, document=_document()
    )
    indicator = card.calque_indicators[0]
    assert (indicator.rule_id, indicator.tier) == ("diiuchyi", 1)
    assert indicator.matched_text == "діючому"
    assert indicator.normative_text == "чинний"
    assert "[32]" in card.ru_reference_reason
    assert "діючому" in card.donor_html
    assert card.block_text == _document().blocks[0].raw_text


def test_gate_merged_k_reason_recovers_form_without_k_query_parts() -> None:
    query = replace(
        _query(),
        attributed_channels=(Channel.A, Channel.K),
        reasons=("A.phrase.0", "K.diiuchyi"),
    )
    card = build_query_card(query, initial_state("q1"), ENGINES, TODAY, document=_document())
    assert card.calque_indicators[0].matched_text == "діючому"
    assert "<mark>діючому</mark>" in card.donor_html


def test_gate_card_exposes_complete_triage_state() -> None:
    state = QueryState(
        "q1",
        status="unchecked",
        needs_review=True,
        previous_status="found",
        prior_snapshot="old",
        source_url="https://example.com/source",
        failed_engines=("google_scholar",),
        comment="перевірити повторно",
    )
    card = build_query_card(_query(), state, ENGINES, TODAY)
    assert card.needs_review is True
    assert card.previous_status_label == "знайдено"
    assert card.source_url == state.source_url
    assert card.comment == state.comment
    assert card.failed_engines == state.failed_engines
    assert {item.code for item in card.status_actions} == {"unchecked", "no_result", "found"}


def test_gate_usefulness_counts_primary_channel_without_review_items() -> None:
    queries = (_query(1), _query(2), _k_query())
    states = {
        "q1": QueryState("q1", status="found", found_engine="google"),
        "q2": QueryState("q2", status="no_result"),
        "q3": QueryState("q3", status="found", needs_review=True, found_engine="yandex"),
    }
    summary = build_search_summary(_result(queries), states, ENGINES)
    by_channel = {item.channel: item for item in summary.channel_usefulness}
    assert set(by_channel) == {Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L}
    assert (by_channel[Channel.A].found, by_channel[Channel.A].checked) == (1, 2)
    assert by_channel[Channel.A].hit_rate_label == "50%"
    assert by_channel[Channel.K].checked == 0
    assert by_channel[Channel.K].hit_rate_label == "—"


def test_gate_engine_failures_include_explicit_zeroes() -> None:
    states = {
        "q1": QueryState("q1", failed_engines=("google", "google_scholar")),
        "q2": QueryState("q2", failed_engines=("google",)),
    }
    summary = build_search_summary(_result((_query(1), _query(2))), states, ENGINES)
    failures = {item.engine_code: item.count for item in summary.engine_failures}
    assert failures["google"] == 2
    assert failures["google_scholar"] == 1
    assert failures["nrat"] == 0


def test_gate_summary_keeps_calques_language_coverage_and_warnings() -> None:
    summary = build_search_summary(_result((), warnings=("увага",)), {}, ENGINES)
    assert summary.n_pages == 1 and summary.coverage_label == "100%"
    assert summary.calques.tier1_hits == 2
    assert summary.calques.band == "prominent"
    assert "не доказ" in summary.calques.notice
    assert summary.bibliography.total == 1
    assert summary.bibliography.ru == 1
    assert summary.bibliography.ru_percentage_label == "100%"
    assert summary.warnings == ("увага",)


def test_gate_shortfalls_are_counted_by_typed_reason() -> None:
    shortfall = SectionShortfall(
        section_id="s1",
        target=10,
        actual=3,
        author_words=1000,
        raw_sentence_count=5,
        eligible_donor_count=3,
        generated_window_count=3,
        eligible_pre_dedup_count=3,
        post_dedup_count=3,
        coverage_ratio=0.8,
        normative_sentence_ratio=0.0,
        primary_reason=ShortfallReason.INSUFFICIENT_QUALITY,
        contributing_reasons=(ShortfallReason.PARTIAL_COVERAGE,),
        rejected_by_reason=(("diversity_limit", 2),),
    )
    summary = build_search_summary(_result((), shortfalls=(shortfall,)), {}, ENGINES)
    reasons = {item.reason: item for item in summary.shortfall_reasons}
    assert summary.shortfall_section_count == 1
    assert reasons[ShortfallReason.INSUFFICIENT_QUALITY].primary_count == 1
    assert reasons[ShortfallReason.PARTIAL_COVERAGE].contributing_count == 1


def test_gate_screen_groups_first_five_and_reports_exact_hidden_count() -> None:
    queries = tuple(_query(index) for index in range(1, 8))
    screen = build_search_screen(_result(queries), {}, ENGINES, TODAY)
    assert DEFAULT_VISIBLE_PER_SECTION == 5
    assert len(screen.sections) == 1
    section = screen.sections[0]
    assert len(section.visible_cards) == 5
    assert len(section.hidden_cards) == 2
    assert section.hidden_count == 2
    assert all(card.status_label == "не перевірено" for card in (*section.visible_cards, *section.hidden_cards))


def test_gate_failed_engine_action_is_idempotent_and_keeps_status() -> None:
    states = {"q1": QueryState("q1", status="no_result")}
    once = apply_status_action(states, "q1", "failed_engine", failed_engine="google")
    twice = apply_status_action(once, "q1", "failed_engine", failed_engine="google")
    assert twice["q1"].status == "no_result"
    assert twice["q1"].failed_engines == ("google",)
    with pytest.raises(ValueError):
        apply_status_action(states, "q1", "failed_engine", failed_engine="")


def test_gate_screen_is_deterministic() -> None:
    result = _result(tuple(_query(index) for index in range(1, 7)))
    assert build_search_screen(result, {}, ENGINES, TODAY) == build_search_screen(
        result, {}, ENGINES, TODAY
    )


def test_gate_negative_visible_limit_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_search_screen(_result((_query(),)), {}, ENGINES, TODAY, visible_limit=-1)
