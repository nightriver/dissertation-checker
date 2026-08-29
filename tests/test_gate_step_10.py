"""Шлюз кроку 10: канонічні запити, provenance і якір PLAN_SEARCH.md §§12–15."""

from __future__ import annotations

from dataclasses import replace

from search.bibliography import build_citations
from search.markers import CandidateSignal
from search.normalization import normalize_text, tokenize
from search.query_builder import (
    MAX_QUERY_CHARS,
    _build_pdf_anchor,
    _word_frequencies,
    build_k_queries,
    build_search_result,
    build_source_channel_query,
    compose_query_parts,
    extract_surname_evidence,
    transliterate_surname,
    validate_query_parts,
)
from search.types import (
    BibliographyEntry,
    Channel,
    Confidence,
    Language,
    QueryPart,
    QueryPartOrigin,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SectionInfo,
    SectionKind,
    SentenceDonor,
    SourceSpan,
    TextZone,
    ZoneSpan,
)


def _block(text: str) -> SearchBlock:
    normalized = normalize_text(text)
    return SearchBlock(
        block_id="b1",
        raw_text=text,
        normalized=normalized,
        tokens=tokenize(text, normalized),
        section_id="s1",
        heading_path=(),
        physical_page=1,
        block_index=0,
        zone_spans=(ZoneSpan(0, len(text), TextZone.AUTHOR_TEXT, Confidence.HIGH, "gate"),),
    )


def _donor(block: SearchBlock) -> SentenceDonor:
    return SentenceDonor(
        donor_id="d1",
        block_id="b1",
        section_id="s1",
        sentence_ordinal=0,
        occurrence_index=0,
        source=SourceSpan((RawSpan("b1", 1, 0, len(block.raw_text)),)),
        raw_text=block.raw_text,
        normalized_text=block.normalized.text,
        author_word_count=sum(token.is_word for token in block.tokens),
    )


def _section() -> SectionInfo:
    return SectionInfo(
        section_id="s1",
        kind=SectionKind.CHAPTER,
        ordinal=1,
        heading="РОЗДІЛ 1",
        block_start=0,
        block_end=1,
        physical_pages=(1,),
        author_words=100,
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        confidence=Confidence.HIGH,
    )


def _document(block: SearchBlock, donor: SentenceDonor, entry: BibliographyEntry | None = None) -> SearchDocument:
    entries = (entry,) if entry else ()
    base = SearchDocument(
        document_sha256="0" * 64,
        parser_version="gate",
        n_pages=1,
        pages=(),
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        blocks=(block,),
        sections=(_section(),),
        sentences=(donor,),
        bibliography=entries,
        citations=(),
        body_biblio_confidence=Confidence.HIGH,
        applied_overrides=(),
    )
    return replace(base, citations=build_citations(base, entries)) if entry else base


def _entry() -> BibliographyEntry:
    source = SourceSpan((RawSpan("bib", 2, 0, 60),))
    return BibliographyEntry(
        entry_id="entry-1",
        ordinal=1,
        raw_text="1. Иванов И. И. Теория государства. Москва, 2020.",
        source=source,
        title="Теория государства",
        title_source=source,
        title_confidence=Confidence.HIGH,
        surnames=("Иванов",),
        year=2020,
        language=Language.RU,
        language_evidence="exclusive_or_spelling",
    )


def _signal(text: str, needle: str, channel: Channel) -> CandidateSignal:
    start = text.index(needle)
    return CandidateSignal(channel, f"{channel.value}.gate", start, start + len(needle), 4.0, "gate")


def test_gate_parts_are_the_only_source_of_query_text() -> None:
    source = SourceSpan((RawSpan("b1", 1, 0, 5),))
    parts = (
        QueryPart("«", QueryPartOrigin.SYSTEM_LITERAL, "open", None),
        QueryPart("текст", QueryPartOrigin.SOURCE_PHRASE, None, source),
        QueryPart("»", QueryPartOrigin.SYSTEM_LITERAL, "close", None),
    )
    assert compose_query_parts(parts) == "«текст»"
    assert validate_query_parts(parts, "«текст»")
    assert not validate_query_parts(parts, "інший текст")


def test_gate_content_without_source_or_audited_id_is_rejected() -> None:
    invalid = (QueryPart("вигадане", QueryPartOrigin.SOURCE_PHRASE, None, None),)
    assert not validate_query_parts(invalid, "вигадане")


def test_gate_a_n_t_are_quoted_b_and_l_are_not() -> None:
    text = "Автори пропонують дослідити важливе питання правового регулювання суспільства."
    block = _block(text)
    donor = _donor(block)
    freq = _word_frequencies(_document(block, donor))
    for channel in (Channel.A, Channel.N, Channel.T, Channel.B, Channel.L):
        signals = () if channel == Channel.L else (_signal(text, "дослідити", channel),)
        query = build_source_channel_query(
            donor=donor, block=block, channel=channel, signals=signals, score=4, freq=freq
        )
        assert query not in (None, "query_too_long")
        assert validate_query_parts(query.parts, query.query_text)
        if channel in (Channel.A, Channel.N, Channel.T):
            assert query.query_text.startswith("«") and query.query_text.endswith("»")
        else:
            assert not query.query_text.startswith("«")
        assert query.query_text.strip("«»") in donor.raw_text


def test_gate_query_too_long_is_explicit() -> None:
    huge = "пропон" + "у" * (MAX_QUERY_CHARS + 20)
    text = f"Автори {huge} важливе питання правового регулювання."
    block = _block(text)
    donor = _donor(block)
    signal = _signal(text, huge, Channel.A)
    assert build_source_channel_query(
        donor=donor, block=block, channel=Channel.A, signals=(signal,), score=4, freq={}
    ) == "query_too_long"


def test_gate_surname_is_extracted_only_next_to_initials() -> None:
    assert extract_surname_evidence("І. І. Петренко сформулював тезу.")[0].surname == "Петренко"
    assert extract_surname_evidence("Петренко сформулював тезу.") == ()


def test_gate_transliteration_rules_are_deterministic() -> None:
    assert transliterate_surname("Ковальський") == "Ковальский"
    assert transliterate_surname("Лук'яненко") == "Лукьяненко"
    assert transliterate_surname("Шишкін") == "Шишкын"
    assert transliterate_surname("Ґєрич") == "Герыч"


def test_gate_k1_k2_k3_and_all_provenance_origins() -> None:
    text = (
        "Під режимом розуміємо підхід, який І. І. Петренко вважає таким, "
        "що являється важливим для 2020 року [1]."
    )
    block = _block(text)
    donor = _donor(block)
    document = _document(block, donor, _entry())
    queries = build_k_queries(document, donor, block, score=6, freq=_word_frequencies(document))
    assert [query.subtype for query in queries] == ["K1", "K2", "K3"]
    origins = {part.origin for query in queries for part in query.parts}
    assert origins >= {
        QueryPartOrigin.SOURCE_PHRASE,
        QueryPartOrigin.CALQUE_RULE,
        QueryPartOrigin.RU_REFERENCE,
        QueryPartOrigin.SURNAME_TRANSLITERATION,
        QueryPartOrigin.LITERAL_NUMBER,
        QueryPartOrigin.SYSTEM_LITERAL,
    }
    assert all(validate_query_parts(query.parts, query.query_text) for query in queries)
    assert "определение" in queries[-1].query_text


def test_gate_k2_is_forbidden_without_link_or_surname() -> None:
    text = "Це положення являється важливим для подальшого дослідження правового режиму."
    block = _block(text)
    donor = _donor(block)
    document = _document(block, donor)
    queries = build_k_queries(document, donor, block, score=3, freq=_word_frequencies(document))
    assert [query.subtype for query in queries] == ["K1"]


def test_gate_k3_requires_definition_marker() -> None:
    text = "І. І. Петренко вказав, що це положення являється важливим для дослідження."
    block = _block(text)
    donor = _donor(block)
    document = _document(block, donor)
    queries = build_k_queries(document, donor, block, score=3, freq=_word_frequencies(document))
    assert "K2" in [query.subtype for query in queries]
    assert "K3" not in [query.subtype for query in queries]


def test_gate_anchor_prefers_stable_window_and_preserves_raw_text() -> None:
    words = ["zмішане"] * 8 + [
        "надійне", "вихідне", "вікно", "містить", "достатньо", "чистих", "слів", "пошуку",
    ]
    text = " ".join(words)
    normalized = normalize_text(text)
    tokens = [token for token in tokenize(text, normalized) if token.is_word]
    anchor, source, fallback = _build_pdf_anchor(tokens, text, "b1", 1, 0, freq={"пошуку": 1})
    assert not fallback
    assert "zмішане" not in anchor
    assert anchor in text
    span = source.parts[0]
    assert text[span.raw_start:span.raw_end] == anchor


def test_gate_anchor_falls_back_when_no_stable_window_exists() -> None:
    text = " ".join(["zмішане"] * 10)
    normalized = normalize_text(text)
    tokens = [token for token in tokenize(text, normalized) if token.is_word]
    anchor, _, fallback = _build_pdf_anchor(tokens, text, "b1", 1, 0)
    assert fallback and anchor == text


def test_gate_builders_are_deterministic() -> None:
    text = "І. І. Петренко зазначив, що положення являється важливим для 2020 року."
    block = _block(text)
    donor = _donor(block)
    document = _document(block, donor)
    args = (document, donor, block)
    assert build_k_queries(*args, score=3, freq=_word_frequencies(document)) == build_k_queries(
        *args, score=3, freq=_word_frequencies(document)
    )


def test_gate_full_builder_emits_new_channels_and_existing_evidence_ids() -> None:
    text = (
        "Уперше нами було опитано 100 респондентів, 50% яких вважають, "
        "що це положення являється важливим для правового регулювання."
    )
    block = _block(text)
    donor = _donor(block)
    document = _document(block, donor)
    result = build_search_result(document)
    channels = {channel for query in result.queries for channel in query.attributed_channels}
    assert channels >= {Channel.A, Channel.N, Channel.B, Channel.K}
    evidence_ids = {hit.evidence_id for hit in result.signal_hits}
    assert all(set(query.evidence_ids) <= evidence_ids for query in result.queries)
    assert result.calque_metrics.tier1_hits >= 1
    assert result == build_search_result(document)
