"""Шлюз кроку 11: драбина, дедуплікація та SectionShortfall PLAN_SEARCH.md §14."""

from __future__ import annotations

from dataclasses import replace

from search.query_builder import (
    DEDUP_JACCARD_THRESHOLD,
    MAX_QUERIES_PER_SECTION,
    TARGET_QUERIES_PER_SECTION,
    TOP_VISIBLE_PER_SECTION,
    _duplicate_candidate_pairs,
    _duplicate_features,
    _deduplicate_queries,
    _queries_are_duplicates,
    select_query_pool,
)
from search.normalization import normalize_text, tokenize
from search.types import (
    Channel,
    Confidence,
    Language,
    QueryPart,
    QueryPartOrigin,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchQuery,
    SectionInfo,
    SectionKind,
    ShortfallReason,
    SourceSpan,
    TextZone,
    ZoneSpan,
)


def _block(index: int, *, page: int | None = None, heading: str = "підрозділ") -> SearchBlock:
    text = f"Текст блока {index} містить достатньо вихідних слів для перевірки."
    normalized = normalize_text(text)
    return SearchBlock(
        block_id=f"b{index}",
        raw_text=text,
        normalized=normalized,
        tokens=tokenize(text, normalized),
        section_id="s1",
        heading_path=(heading,),
        physical_page=page if page is not None else index + 1,
        block_index=index,
        zone_spans=(ZoneSpan(0, len(text), TextZone.AUTHOR_TEXT, Confidence.HIGH, "gate"),),
    )


def _section(*, kind: SectionKind = SectionKind.CHAPTER, words: int = 1000, coverage: float = 1.0) -> SectionInfo:
    return SectionInfo(
        section_id="s1",
        kind=kind,
        ordinal=1,
        heading="РОЗДІЛ 1",
        block_start=0,
        block_end=20,
        physical_pages=tuple(range(1, 21)),
        author_words=words,
        expected_body_pages=20,
        extractable_body_pages=20,
        coverage_ratio=coverage,
        confidence=Confidence.HIGH,
    )


def _document(
    blocks: tuple[SearchBlock, ...],
    *,
    section: SectionInfo | None = None,
    confidence: Confidence = Confidence.HIGH,
) -> SearchDocument:
    return SearchDocument(
        document_sha256="0" * 64,
        parser_version="gate",
        n_pages=max((block.physical_page for block in blocks), default=0),
        pages=(),
        expected_body_pages=len(blocks),
        extractable_body_pages=len(blocks),
        coverage_ratio=(section or _section()).coverage_ratio,
        blocks=blocks,
        sections=(section or _section(),),
        sentences=(),
        bibliography=(),
        citations=(),
        body_biblio_confidence=confidence,
        applied_overrides=(),
    )


def _query(
    index: int,
    channel: Channel,
    text: str,
    *,
    language: Language = Language.UK,
    score: float = 4.0,
    stage: int = 1,
    donor: str | None = None,
    page: int | None = None,
) -> SearchQuery:
    block_id = f"b{index}"
    source = SourceSpan((RawSpan(block_id, page if page is not None else index + 1, 0, len(text)),))
    return SearchQuery(
        donor_id=donor or f"d{index}-{channel.value}",
        query_id=f"q{index}-{channel.value}-{language.value}-{stage}",
        block_id=block_id,
        section_id="s1",
        sentence_ordinal=index,
        primary_channel=channel,
        attributed_channels=(channel,),
        subtype=None,
        query_language=language,
        selection_stage=stage,
        query_text=text,
        parts=(QueryPart(text, QueryPartOrigin.SOURCE_PHRASE, None, source),),
        donor_text=text,
        donor_source=source,
        pdf_anchor=text,
        pdf_anchor_source=source,
        physical_page=page if page is not None else index + 1,
        score=score,
        rank_score=score,
        evidence_ids=(f"e{index}-{channel.value}",),
        reasons=(f"r{index}-{channel.value}",),
    )


_UNIQUE_WORDS = (
    "бурштиновий", "смарагдовий", "волошковий", "малиновий", "сріблястий",
    "золотавий", "багрянистий", "лазуровий", "перламутровий", "графітовий",
    "каштановий", "помаранчевий", "фіалковий", "бірюзовий", "пурпуровий",
)


def _unique_text(index: int) -> str:
    word = _UNIQUE_WORDS[index]
    return " ".join([word] * 6)


def test_gate_constants_match_plan() -> None:
    assert TARGET_QUERIES_PER_SECTION == 10
    assert MAX_QUERIES_PER_SECTION == 12
    assert TOP_VISIBLE_PER_SECTION == 5
    assert DEDUP_JACCARD_THRESHOLD == 0.65


def test_gate_jaccard_component_merges_channels_reasons_and_evidence() -> None:
    blocks = {"b0": _block(0), "b1": _block(1)}
    left = _query(0, Channel.A, "унікальне правове регулювання суспільних відносин державою")
    right = _query(1, Channel.B, "правове регулювання суспільних відносин державою сьогодні")
    winners, components, removed, merged = _deduplicate_queries((left, right), blocks)
    assert (components, removed, merged) == (1, 1, 1)
    assert winners[0].attributed_channels == (Channel.A, Channel.B)
    assert set(winners[0].reasons) == set(left.reasons + right.reasons)
    assert set(winners[0].evidence_ids) == set(left.evidence_ids + right.evidence_ids)


def test_gate_different_languages_never_deduplicate() -> None:
    blocks = {"b0": _block(0), "b1": _block(1)}
    text = "однакове правове регулювання суспільних відносин державою"
    uk = _query(0, Channel.K, text, language=Language.UK)
    ru = _query(1, Channel.K, text, language=Language.RU)
    winners, components, removed, _ = _deduplicate_queries((uk, ru), blocks)
    assert len(winners) == components == 2
    assert removed == 0


def test_gate_a_and_b_same_donor_always_have_an_edge() -> None:
    blocks = {"b0": _block(0), "b1": _block(1)}
    a = _query(0, Channel.A, "цілком різні слова першого запиту", donor="same")
    b = replace(_query(1, Channel.B, "несхожий текст другого кандидата", donor="same"), block_id="b1")
    winners, _, removed, _ = _deduplicate_queries((a, b), blocks)
    assert len(winners) == 1 and removed == 1


def test_gate_union_find_merges_transitive_duplicates() -> None:
    blocks = {f"b{i}": _block(i) for i in range(3)}
    first = _query(0, Channel.A, "альфа бета гамма дельта епсилон один")
    middle = _query(1, Channel.N, "альфа бета гамма дельта епсилон дзета")
    last = _query(2, Channel.B, "бета гамма дельта епсилон дзета два")
    winners, components, removed, _ = _deduplicate_queries((first, middle, last), blocks)
    assert len(winners) == components == 1
    assert removed == 2


def test_gate_prefix_filter_keeps_every_true_duplicate_edge() -> None:
    blocks = {f"b{i}": _block(i) for i in range(8)}
    queries = (
        _query(0, Channel.A, "альфа бета гамма дельта епсилон один", donor="same"),
        _query(1, Channel.B, "цілком інші слова другого запиту", donor="same"),
        _query(2, Channel.N, "альфа бета гамма дельта епсилон два"),
        _query(3, Channel.K, "бета гамма дельта епсилон дзета три"),
        _query(4, Channel.T, "окремий бурштиновий контекст без перетину"),
        _query(5, Channel.L, "окремий смарагдовий контекст без перетину"),
        _query(6, Channel.A, "альфа бета гамма", language=Language.RU),
        replace(_query(7, Channel.B, "альфа бета гамма"), section_id="s2"),
    )
    features = tuple(_duplicate_features(query.query_text) for query in queries)
    candidates = set(_duplicate_candidate_pairs(queries, features))
    true_edges = {
        (left, right)
        for left in range(len(queries))
        for right in range(left + 1, len(queries))
        if _queries_are_duplicates(queries[left], queries[right], blocks)
    }
    assert true_edges <= candidates


def test_gate_winner_prefers_stage_then_rank_then_channel() -> None:
    blocks = {"b0": _block(0), "b1": _block(1)}
    stage2 = _query(0, Channel.A, "спільні значущі слова для одного компонента", score=9, stage=2)
    stage1 = _query(1, Channel.N, "спільні значущі слова для одного компонента", score=4, stage=1)
    winner = _deduplicate_queries((stage2, stage1), blocks)[0][0]
    assert winner.query_id == stage1.query_id


def test_gate_slot_order_alternates_a_k_b_for_first_positions() -> None:
    blocks = tuple(_block(i) for i in range(12))
    document = _document(blocks)
    pool = tuple(
        _query(i, channel, _unique_text(i))
        for i, channel in enumerate((Channel.A, Channel.K, Channel.B) * 4)
    )
    selected, shortfalls, _, _ = select_query_pool(document, pool)
    assert [query.primary_channel for query in selected[:5]] == [
        Channel.A, Channel.K, Channel.B, Channel.A, Channel.K,
    ]
    assert len(selected) == 10
    assert shortfalls == ()


def test_gate_block_and_page_limits_create_diversity_shortfall() -> None:
    blocks = tuple(_block(i, page=1) for i in range(12))
    document = _document(blocks)
    pool = tuple(_query(i, Channel.A, _unique_text(i), page=1) for i in range(12))
    selected, shortfalls, _, rejected = select_query_pool(document, pool)
    assert len(selected) == 3
    assert shortfalls[0].primary_reason == ShortfallReason.DIVERSITY_LIMITS
    assert dict(rejected)["diversity_limit"] >= 1


def test_gate_empty_body_has_exact_shortfall_reason() -> None:
    document = _document((), section=_section(words=0))
    selected, shortfalls, _, _ = select_query_pool(document, ())
    assert selected == ()
    assert shortfalls[0].primary_reason == ShortfallReason.NO_EXTRACTABLE_BODY


def test_gate_partial_coverage_is_only_contributing_reason() -> None:
    block = _block(0)
    document = _document((block,), section=_section(coverage=0.5))
    selected, shortfalls, _, _ = select_query_pool(document, ())
    assert selected == ()
    assert shortfalls[0].primary_reason == ShortfallReason.NO_VALID_WINDOWS
    assert shortfalls[0].contributing_reasons == (ShortfallReason.PARTIAL_COVERAGE,)


def test_gate_unresolved_boundary_has_first_priority() -> None:
    document = _document((), section=_section(words=0), confidence=Confidence.LOW)
    _, shortfalls, _, _ = select_query_pool(document, ())
    assert shortfalls[0].primary_reason == ShortfallReason.SECTION_UNRESOLVED


def test_gate_selection_is_byte_for_byte_deterministic() -> None:
    blocks = tuple(_block(i) for i in range(6))
    document = _document(blocks)
    pool = tuple(_query(i, (Channel.A, Channel.K, Channel.B)[i % 3], _unique_text(i)) for i in range(6))
    assert select_query_pool(document, pool) == select_query_pool(document, pool)


def test_gate_t_candidates_get_only_the_documented_relaxed_limits() -> None:
    block = _block(0, page=1)
    document = _document((block,))
    pool = tuple(
        replace(
            _query(i, Channel.T, _unique_text(i), stage=4, page=1),
            block_id="b0",
        )
        for i in range(5)
    )
    selected, shortfalls, _, _ = select_query_pool(document, pool)
    assert len(selected) == 3
    assert shortfalls[0].primary_reason == ShortfallReason.INSUFFICIENT_QUALITY


def test_gate_intro_can_add_two_n_queries_after_the_base_ten() -> None:
    blocks = tuple(_block(i) for i in range(12))
    document = _document(blocks, section=_section(kind=SectionKind.INTRO))
    pool = tuple(_query(i, Channel.N, _unique_text(i)) for i in range(12))
    selected, shortfalls, _, _ = select_query_pool(document, pool)
    assert len(selected) == MAX_QUERIES_PER_SECTION == 12
    assert shortfalls == ()


def test_gate_shortfall_distinguishes_quality_from_deduplication() -> None:
    blocks = tuple(_block(i) for i in range(10))
    document = _document(blocks)
    insufficient = tuple(_query(i, Channel.A, _unique_text(i)) for i in range(5))
    _, quality_shortfalls, _, _ = select_query_pool(document, insufficient)
    assert quality_shortfalls[0].primary_reason == ShortfallReason.INSUFFICIENT_QUALITY

    duplicated = tuple(
        _query(i, Channel.A, "тотожний авторський фрагмент для перевірки дедуплікації")
        for i in range(10)
    )
    _, dedup_shortfalls, metrics, _ = select_query_pool(document, duplicated)
    assert dedup_shortfalls[0].primary_reason == ShortfallReason.DEDUPLICATION_REDUCED
    assert metrics.removed_count == 9


def test_gate_shortfall_rejections_are_local_to_the_section() -> None:
    first_blocks = tuple(_block(i, page=1) for i in range(4))
    second_block = replace(_block(4, page=2), section_id="s2")
    first = _section()
    second = replace(_section(), section_id="s2", ordinal=2)
    document = replace(
        _document((*first_blocks, second_block)),
        sections=(first, second),
    )
    first_pool = tuple(_query(i, Channel.A, _unique_text(i), page=1) for i in range(4))
    second_query = replace(
        _query(4, Channel.A, _unique_text(4), page=2),
        section_id="s2",
    )
    _, shortfalls, _, rejected = select_query_pool(document, (*first_pool, second_query))
    by_section = {shortfall.section_id: dict(shortfall.rejected_by_reason) for shortfall in shortfalls}
    assert by_section["s1"]["diversity_limit"] >= 1
    assert by_section["s2"] == {}
    assert dict(rejected)["diversity_limit"] >= 1
