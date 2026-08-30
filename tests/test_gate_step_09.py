"""Шлюз кроку 9: виробничі маркери PLAN_SEARCH.md, §§10–12."""

from __future__ import annotations

from dataclasses import replace

import search.calques as calques
from search.calques import CalqueRule
from search.markers import (
    CHANNEL_A_MAX_SCORE,
    CHANNEL_B_MAX_SCORE,
    CHANNEL_K_TIER1_MAX_SCORE,
    LONG_MIN_WORDS,
    MARKERS_VERSION,
    NORMATIVE_MARKERS_VERSION,
    REASON_K_EXCLUDED_ZONE,
    REASON_SECTION_NOT_CONTENT,
    STOPWORDS_VERSION,
    channel_a_signals,
    evaluate_candidate,
    find_channel_b_signals,
    find_channel_k_signals,
    find_channel_n_signals,
    find_channel_t_signals,
    is_channel_l_candidate,
    is_normative_heavy,
    normative_marker_ids,
    rare_word_forms,
)
from search.normalization import normalize_text, tokenize
from search.types import (
    Confidence,
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


def _block(
    text: str,
    *,
    zone: TextZone = TextZone.AUTHOR_TEXT,
    headings: tuple[str, ...] = (),
    block_index: int = 0,
) -> SearchBlock:
    normalized = normalize_text(text)
    return SearchBlock(
        block_id=f"b{block_index}",
        raw_text=text,
        normalized=normalized,
        tokens=tokenize(text, normalized),
        section_id="s1",
        heading_path=headings,
        physical_page=1,
        block_index=block_index,
        zone_spans=(ZoneSpan(0, len(text), zone, Confidence.HIGH, "gate"),),
    )


def _donor(block: SearchBlock, *, words: int | None = None) -> SentenceDonor:
    word_count = words if words is not None else sum(
        token.is_word and any(character.isalpha() for character in token.normalized)
        for token in block.tokens
    )
    return SentenceDonor(
        donor_id="d1",
        block_id=block.block_id,
        section_id="s1",
        sentence_ordinal=0,
        occurrence_index=0,
        source=SourceSpan((RawSpan(block.block_id, 1, 0, len(block.raw_text)),)),
        raw_text=block.raw_text,
        normalized_text=block.normalized.text,
        author_word_count=word_count,
    )


def _section(kind: SectionKind = SectionKind.CHAPTER, heading: str = "РОЗДІЛ 1") -> SectionInfo:
    return SectionInfo(
        section_id="s1",
        kind=kind,
        ordinal=1,
        heading=heading,
        block_start=0,
        block_end=1,
        physical_pages=(1,),
        author_words=100,
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        confidence=Confidence.HIGH,
    )


def _document(blocks: tuple[SearchBlock, ...]) -> SearchDocument:
    return SearchDocument(
        document_sha256="0" * 64,
        parser_version="gate",
        n_pages=1,
        pages=(),
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        blocks=blocks,
        sections=(_section(),),
        sentences=tuple(_donor(block) for block in blocks),
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.HIGH,
        applied_overrides=(),
    )


def _rule(rule_id: str, pattern: str, tier: int) -> CalqueRule:
    return CalqueRule(
        rule_id=rule_id,
        pattern=pattern,
        ru_origin=rule_id,
        uk_norm=rule_id,
        status="active",
        tier=tier,
        group="gate",
        rationale="перевірка шлюзу",
    )


def test_gate_versions_are_explicit() -> None:
    assert MARKERS_VERSION.startswith("search-markers-")
    assert STOPWORDS_VERSION.startswith("search-stopwords-")
    assert NORMATIVE_MARKERS_VERSION.startswith("normative-markers-")


def test_gate_a_scores_two_per_match_and_caps_at_six() -> None:
    signals = channel_a_signals(
        "Ми пропонуємо, вважаємо це доцільним та обґрунтованим рішенням."
    )
    assert sum(item.score for item in signals) == CHANNEL_A_MAX_SCORE
    assert any(item.score == 0 for item in signals)


def test_gate_a_word_windows_ignore_punctuation_but_keep_exact_limits() -> None:
    assert any(
        item.rule_id == "A.under_understand"
        for item in channel_a_signals("Під поняттям, на нашу думку, розуміємо правовий режим.")
    )
    assert any(
        item.rule_id == "A.we_surveyed"
        for item in channel_a_signals("Нами було ретельно, повторно проаналізовано матеріали.")
    )


def test_gate_k_cap_is_restarted_for_each_candidate_range(monkeypatch) -> None:
    monkeypatch.setattr(calques, "CALQUES", (_rule("t1", r"\btierone\b", 1),))
    block = _block("tierone tierone tierone. tierone tierone tierone.")
    boundary = block.raw_text.index(". ") + 2
    first = find_channel_k_signals(block, raw_start=0, raw_end=boundary)
    second = find_channel_k_signals(block, raw_start=boundary, raw_end=len(block.raw_text))
    assert sum(item.score for item in first) == 6
    assert sum(item.score for item in second) == 6


def test_gate_n_heading_and_each_text_marker_score_once() -> None:
    heading = find_channel_n_signals("Нейтральне речення.", ("НАУКОВА НОВИЗНА ОДЕРЖАНИХ РЕЗУЛЬТАТІВ",))
    assert len(heading) == 1 and heading[0].score == 4
    for text in (
        "Уперше доведено тезу.",
        "Вперше обґрунтовано нову модель.",
        "Метод удосконалено.",
        "Це набуло подальшого розвитку.",
    ):
        signals = find_channel_n_signals(text)
        assert len(signals) == 1 and signals[0].score == 4
    assert len(find_channel_n_signals("Уперше метод удосконалено.")) == 1


def test_gate_n_exact_heading_inside_pdf_sentence_scores_whole_donor() -> None:
    text = (
        "Наукова новизна одержаних результатів полягає в обґрунтуванні "
        "нової моделі правового регулювання суспільних відносин."
    )
    signal = find_channel_n_signals(text)[0]
    assert signal.rule_id == "N.novelty_heading"
    assert (signal.raw_start, signal.raw_end) == (0, len(text))
    assert signal.reason == "novelty_heading_inline"
    assert find_channel_n_signals(
        "Наукова новизна отриманих результатів сформульована окремо."
    ) == ()


def test_gate_n_first_time_guards_reject_confirmed_historical_contexts() -> None:
    for text in (
        "Визначення уперше робиться в Укладенні 1903 року.",
        "Покарання уперше з’явилося у кодексі 2001 року.",
        "Працівник поліції уперше звернувся до заявника.",
    ):
        assert find_channel_n_signals(text) == ()


def test_gate_b_strong_values_work_without_an_empirical_stem() -> None:
    for text in ("Частка становить 42%.", "Вартість становить 500 грн.", "Відстань становить 12 км."):
        assert sum(item.score for item in find_channel_b_signals(text)) >= 2


def test_gate_b_rejects_numeric_chart_axis_with_two_content_words() -> None:
    text = "2 % 1,8 1,8 1,8 квітень 1,6 липень 1,4 1,2 1 0,8 0,6 0,4 0,3 0,2 0,1"
    assert find_channel_b_signals(text) == ()


def test_gate_b_plain_number_has_exact_four_word_boundary() -> None:
    allowed = "100 один два три чотири респондентів взяли участь."
    rejected = "100 один два три чотири п'ять респондентів взяли участь."
    assert find_channel_b_signals(allowed)
    assert find_channel_b_signals(rejected) == ()


def test_gate_b_bonuses_and_cap() -> None:
    signals = find_channel_b_signals("У 2019–2021 роках опитано 100 та 200 респондентів, 50% відповіли.")
    assert sum(item.score for item in signals) == CHANNEL_B_MAX_SCORE
    assert {item.rule_id for item in signals} >= {"B.empirical_value", "B.date_range", "B.second_number"}


def test_gate_lone_year_and_article_number_are_not_b() -> None:
    assert find_channel_b_signals("У 2020 році ухвалено статтю 12 закону.") == ()


def test_gate_k_tiers_use_one_dictionary_and_expected_scores(monkeypatch) -> None:
    monkeypatch.setattr(calques, "CALQUES", (
        _rule("t1", r"\btierone\b", 1),
        _rule("t2", r"\btiertwo\b", 2),
        _rule("t3", r"\btierthree\b", 3),
    ))
    signals = find_channel_k_signals(_block("tierone tiertwo tierthree"))
    assert [item.score for item in signals] == [3, 1, 0]


def test_gate_k_tier2_without_author_tier1_scores_zero(monkeypatch) -> None:
    monkeypatch.setattr(calques, "CALQUES", (_rule("t2", r"\btiertwo\b", 2),))
    signal = find_channel_k_signals(_block("tiertwo"))[0]
    assert signal.score == 0


def test_gate_k_tier1_cap_is_six(monkeypatch) -> None:
    monkeypatch.setattr(calques, "CALQUES", (_rule("t1", r"\btierone\b", 1),))
    signals = find_channel_k_signals(_block("tierone tierone tierone"))
    assert sum(item.score for item in signals) == CHANNEL_K_TIER1_MAX_SCORE


def test_gate_k_excluded_zone_is_visible_but_scores_zero(monkeypatch) -> None:
    monkeypatch.setattr(calques, "CALQUES", (_rule("t1", r"\btierone\b", 1),))
    signal = find_channel_k_signals(_block("tierone", zone=TextZone.QUOTED_TEXT))[0]
    assert signal.score == 0
    assert signal.reason == REASON_K_EXCLUDED_ZONE


def test_gate_t_frequency_filters_and_order() -> None:
    block = _block(
        "унікальні довжелезне двічі двічі часте часте часте також zелений abc1def",
    )
    forms = rare_word_forms(_document((block,)))
    by_form = {item.form: item for item in forms}
    assert "унікальні" in by_form and "довжелезне" in by_form and "двічі" in by_form
    assert "часте" not in by_form
    assert "також" not in by_form
    assert "zелений" not in by_form
    assert "abc1def" not in by_form
    assert [item.form for item in forms[:2]] == ["довжелезне", "унікальні"]


def test_gate_t_ignores_non_author_zones() -> None:
    block = _block("надзвичайність", zone=TextZone.QUOTED_TEXT)
    assert rare_word_forms(_document((block,))) == ()


def test_gate_t_signal_preserves_source_coordinates() -> None:
    rare = rare_word_forms(_document((_block("Надзвичайність трапилась."),)))
    signal = find_channel_t_signals("Тут надзвичайність трапилась.", rare)[0]
    assert "Тут надзвичайність трапилась."[signal.raw_start:signal.raw_end].casefold() == "надзвичайність"


def test_gate_normative_heavy_needs_two_different_markers_and_no_primary_signal() -> None:
    one_kind = "Стаття 1 та стаття 2 встановлюють правило."
    two_kinds = "Стаття 1 Кримінального кодексу встановлює правило."
    assert len(normative_marker_ids(one_kind)) == 1
    assert not is_normative_heavy(one_kind)
    assert is_normative_heavy(two_kinds)
    assert not is_normative_heavy(two_kinds, channel_a_signals("Ми пропонуємо зміну."))


def test_gate_l_has_exact_eighteen_word_boundary() -> None:
    block = _block("слово " * 18)
    donor = _donor(block, words=LONG_MIN_WORDS)
    assert is_channel_l_candidate(donor, author_text=True, normative_heavy=False)
    assert not is_channel_l_candidate(replace(donor, author_word_count=17), author_text=True, normative_heavy=False)
    assert not is_channel_l_candidate(donor, author_text=False, normative_heavy=False)
    assert not is_channel_l_candidate(donor, author_text=True, normative_heavy=True)


def test_gate_evaluation_multiplies_intro_and_novelty() -> None:
    block = _block(
        "Ми пропонуємо уточнення правового механізму.",
        headings=("Наукова новизна одержаних результатів",),
    )
    result = evaluate_candidate(_donor(block), block, _section(SectionKind.INTRO))
    assert result.base_score == 6
    assert result.section_multiplier == 1.5
    assert result.novelty_multiplier == 2
    assert result.final_score == 18


def test_gate_evaluation_marks_normative_multiplier_and_rejection() -> None:
    block = _block("Стаття 1 Кримінального кодексу встановлює загальне правило застосування.")
    result = evaluate_candidate(_donor(block), block, _section())
    assert result.normative_heavy
    assert result.normative_multiplier == 0.2
    assert any(item.reason == "normative_heavy" for item in result.rejections)


def test_gate_non_content_section_has_zero_final_score_and_explicit_reason() -> None:
    block = _block("Ми пропонуємо та вважаємо це рішення доцільним.")
    result = evaluate_candidate(_donor(block), block, _section(SectionKind.ABSTRACT))
    assert result.base_score == 6
    assert result.final_score == 0
    assert any(item.reason == REASON_SECTION_NOT_CONTENT for item in result.rejections)


def test_gate_evaluation_is_deterministic() -> None:
    block = _block("Уперше ми пропонуємо дослідити 42% матеріалів.")
    donor = _donor(block)
    section = _section(SectionKind.CONCLUSIONS)
    assert evaluate_candidate(donor, block, section) == evaluate_candidate(donor, block, section)
