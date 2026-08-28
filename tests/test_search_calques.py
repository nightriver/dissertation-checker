"""
Модульні тести словника калькованих зворотів `search/calques.py` (§8):
результат аудиту 55 правил, контекстні захисти, зони, склейка компонент і
щільність рівня 1.

Тут перевіряється те, що належить самому кроку: узгодженість ручної
фікстури аудиту зі словником, поведінка окремих проаудитованих правил
(«у якості свідка», «прийнятна міра», «на протязі» біля вікна) і те, що
`CALQUES` лишається єдиним джерелом істини — жодна похідна структура не
живе окремою копією.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from search.calques import (
    CONTEXT_WINDOW_TOKENS,
    DENSITY_ELEVATED,
    DENSITY_PER_WORDS,
    DENSITY_PROMINENT,
    DICT_VERSION,
    LOCAL_MIN_DENSITY,
    LOCAL_MIN_TIER1_HITS,
    LOCAL_MIN_WORDS,
    REASON_FORBIDDEN_CONTEXT_PRESENT,
    REASON_REQUIRED_CONTEXT_MISSING,
    CALQUES,
    CalqueHit,
    CalqueRule,
    collapse_components,
    compile_pattern,
    compiled_pattern,
    compute_metrics,
    density_band,
    excluded_rule_ids,
    find_calques,
    find_calques_with_rejections,
    resolve_zone,
    rule_by_id,
    section_is_locally_dense,
    tier2_is_scorable,
)
from search.normalization import normalize_text, tokenize
from search.types import (
    Confidence,
    SearchBlock,
    SearchDocument,
    TextZone,
    ZoneSpan,
)

AUDIT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "calque_audit.json"

# Правила рівня 1, яких немає в жодному з дев'яти PDF: саме вони мусять
# мати власний перевірений приклад (§8.2, «непроаудитованого рівня 1 не
# буває»).
CORPUS_ABSENT_TIER1 = ("sliduiuchyi", "pryimaty_miry", "miropryiemstvo")

AUDIT_RECORD_FIELDS = {
    "rule_id",
    "tier_before",
    "tier_after",
    "status",
    "rationale",
    "evidence_refs",
    "required_context",
    "forbidden_context",
    "positive_example",
}


# ---------------------------------------------------------------------------
# Допоміжна збірка блоків і документів
# ---------------------------------------------------------------------------

def make_block(
    raw: str,
    zone_spans: tuple[ZoneSpan, ...] | None = None,
    block_id: str = "b1",
) -> SearchBlock:
    """Блок з реальної нормалізації й токенізації, зона — увесь текст."""
    normalized = normalize_text(raw)
    if zone_spans is None:
        zone_spans = (
            ZoneSpan(0, len(raw), TextZone.AUTHOR_TEXT, Confidence.HIGH, "test"),
        )
    return SearchBlock(
        block_id=block_id,
        raw_text=raw,
        normalized=normalized,
        tokens=tokenize(raw, normalized),
        section_id="s1",
        heading_path=(),
        physical_page=1,
        block_index=0,
        zone_spans=zone_spans,
    )


def make_document(*blocks: SearchBlock) -> SearchDocument:
    return SearchDocument(
        document_sha256="0" * 64,
        parser_version="test",
        n_pages=1,
        pages=(),
        expected_body_pages=1,
        extractable_body_pages=1,
        coverage_ratio=1.0,
        blocks=tuple(blocks),
        sections=(),
        sentences=(),
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.HIGH,
        applied_overrides=(),
    )


def hit(
    rule_id: str,
    tier: int,
    raw_start: int,
    raw_end: int,
    zone: TextZone = TextZone.AUTHOR_TEXT,
) -> CalqueHit:
    return CalqueHit(
        rule_id=rule_id,
        tier=tier,
        raw_start=raw_start,
        raw_end=raw_end,
        matched_text="x" * (raw_end - raw_start),
        zone=zone,
    )


def rule_ids(hits) -> list[str]:
    return [item.rule_id for item in hits]


@pytest.fixture(scope="module")
def audit() -> dict:
    return json.loads(AUDIT_FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Словник і фікстура аудиту
# ---------------------------------------------------------------------------

def test_dict_version_marks_the_completed_audit():
    assert DICT_VERSION != "calques-dict-2026-08-25"
    assert "audit" in DICT_VERSION


def test_audit_fixture_has_the_documented_top_level_shape(audit):
    assert audit["schema_version"] == 1
    assert audit["reviewer"]
    assert audit["date"]
    assert isinstance(audit["rules"], list)
    assert len(audit["rules"]) == len(CALQUES) == 55


def test_every_audit_record_carries_all_nine_fields(audit):
    for record in audit["rules"]:
        assert set(record) == AUDIT_RECORD_FIELDS, record["rule_id"]


def test_audit_tier_after_status_and_guards_repeat_the_dictionary(audit):
    by_id = {record["rule_id"]: record for record in audit["rules"]}
    for rule in CALQUES:
        record = by_id[rule.rule_id]
        assert record["tier_after"] == rule.tier, rule.rule_id
        assert record["status"] == rule.status, rule.rule_id
        assert tuple(record["required_context"]) == rule.required_context
        assert tuple(record["forbidden_context"]) == rule.forbidden_context
        assert record["rationale"].strip()
        assert record["evidence_refs"]


def test_audit_result_is_18_tier1_27_tier2_9_tier3_and_one_excluded():
    tiers = [rule.tier for rule in CALQUES if rule.status == "active"]
    assert tiers.count(1) == 18
    assert tiers.count(2) == 27
    assert tiers.count(3) == 9
    assert excluded_rule_ids() == ("yavlyaie_soboiu",)


def test_no_active_rule_lost_its_tier_and_no_excluded_rule_kept_one():
    for rule in CALQUES:
        if rule.status == "active":
            assert rule.tier in (1, 2, 3), rule.rule_id
        else:
            assert rule.tier is None, rule.rule_id


def test_corpus_absent_tier1_rules_have_a_manual_positive_example(audit):
    by_id = {record["rule_id"]: record for record in audit["rules"]}
    for rule_id in CORPUS_ABSENT_TIER1:
        assert rule_by_id(rule_id).tier == 1
        assert by_id[rule_id]["positive_example"].strip(), rule_id


def test_every_non_empty_positive_example_really_fires_its_own_rule(audit):
    # Урок кроку 2: приклад, на якому правило мовчить, нічого не доводить.
    examples = [
        (record["rule_id"], record["positive_example"])
        for record in audit["rules"]
        if record["positive_example"].strip()
    ]
    assert examples
    for rule_id, text in examples:
        assert rule_id in rule_ids(find_calques(make_block(text))), rule_id


# ---------------------------------------------------------------------------
# CALQUES — єдине джерело істини
# ---------------------------------------------------------------------------

def test_find_calques_follows_a_substituted_dictionary(monkeypatch):
    # Похідних кешів за `rule_id` бути не може: підміна словника має
    # діяти одразу, інакше кеш — друга копія списку.
    probe = CalqueRule(
        rule_id="probe_rule",
        pattern=r"тестова\s+калька",
        ru_origin="тестовая калька",
        uk_norm="—",
        status="active",
        tier=1,
        group="test",
        rationale="штучне правило тесту",
    )
    monkeypatch.setattr("search.calques.CALQUES", (probe,))
    hits = find_calques(make_block("Тут є тестова калька для перевірки."))
    assert rule_ids(hits) == ["probe_rule"]
    assert rule_by_id("probe_rule") is probe
    assert compiled_pattern("probe_rule").pattern == probe.pattern
    with pytest.raises(KeyError):
        rule_by_id("yavlyaietsya")


def test_excluded_rule_ids_follows_a_substituted_dictionary(monkeypatch):
    probe = CalqueRule(
        rule_id="probe_excluded",
        pattern=r"нічого",
        ru_origin="ничего",
        uk_norm="—",
        status="excluded",
        tier=None,
        group="test",
        rationale="штучне правило тесту",
    )
    monkeypatch.setattr("search.calques.CALQUES", (probe,))
    assert excluded_rule_ids() == ("probe_excluded",)


def test_compile_pattern_is_cached_by_pattern_text_not_by_rule_id():
    first = compile_pattern(r"кальк\w+")
    second = compile_pattern(r"кальк\w+")
    assert first is second


def test_rule_by_id_raises_for_an_unknown_identifier():
    with pytest.raises(KeyError):
        rule_by_id("no_such_rule")


def test_excluded_rule_never_fires_although_its_pattern_matches():
    text = "Метод являє собою сукупність прийомів дослідження."
    assert compiled_pattern("yavlyaie_soboiu").search(normalize_text(text).text)
    assert find_calques(make_block(text)) == ()


# ---------------------------------------------------------------------------
# Окремі проаудитовані правила
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Це явище являється типовим для галузі.", True),
        ("У роботі з'являється новий підхід до аналізу.", False),
        ("Документ пред'являється на вимогу комісії.", False),
        ("Ця закономірність виявляється в кожному розділі.", False),
        ("Ефект проявляється поступово протягом року.", False),
    ],
)
def test_yavlyaietsya_respects_the_apostrophe_and_the_word_boundary(text, expected):
    assert ("yavlyaietsya" in rule_ids(find_calques(make_block(text)))) is expected


def test_u_yakosti_skips_the_procedural_formula_but_catches_the_role():
    formula = make_block("Особу допитано у якості свідка у справі.")
    role = make_block("Показник узято у якості критерію оцінювання.")
    assert "u_yakosti" not in rule_ids(find_calques(formula))
    assert "u_yakosti" in rule_ids(find_calques(role))


def test_u_yakosti_is_silent_on_the_literal_meaning_of_quality():
    block = make_block("Різниця у якості питної води є істотною.")
    hits, rejections = find_calques_with_rejections(block)
    assert "u_yakosti" not in rule_ids(hits)
    assert any(
        rejection.rule_id == "u_yakosti"
        and rejection.reason == REASON_FORBIDDEN_CONTEXT_PRESENT
        for rejection in rejections
    )


def test_pryimaty_miry_ignores_the_adjective_pryiniatnyi():
    # «прийнятна міра ризику» — не калька: це нормативний іменник «міра»
    # з прикметником, а не дієслівна сполука «приймати міри».
    block = make_block("Прийнятна міра ризику визначається експертом.")
    hits, rejections = find_calques_with_rejections(block)
    assert "pryimaty_miry" not in rule_ids(hits)
    assert "pryimaty_miry" not in [rejection.rule_id for rejection in rejections]


def test_pryimaty_miry_is_suppressed_by_the_legal_term_of_punishment():
    block = make_block("Приймаючи міри покарання, суд урахував обставини справи.")
    hits, rejections = find_calques_with_rejections(block)
    assert "pryimaty_miry" not in rule_ids(hits)
    assert any(
        rejection.rule_id == "pryimaty_miry"
        and rejection.reason == REASON_FORBIDDEN_CONTEXT_PRESENT
        for rejection in rejections
    )


def test_vidzyv_needs_the_dissertation_context_and_reports_its_absence():
    without = make_block("Сторона подала відзив у встановлений строк.")
    with_context = make_block("Опонент надіслав відзив на дисертацію вчасно.")
    hits, rejections = find_calques_with_rejections(without)
    assert "vidzyv" not in rule_ids(hits)
    assert any(
        rejection.rule_id == "vidzyv"
        and rejection.reason == REASON_REQUIRED_CONTEXT_MISSING
        for rejection in rejections
    )
    assert "vidzyv" in rule_ids(find_calques(with_context))


def test_na_protyazi_forbidden_context_ends_after_five_word_tokens():
    near = make_block("На протязі року стояло відчинене вікно кабінету.")
    far = make_block(
        "На протязі року ми досліджували процес, а потім замовили нові вікна."
    )
    assert "na_protyazi" not in rule_ids(find_calques(near))
    assert "na_protyazi" in rule_ids(find_calques(far))


def test_context_window_is_measured_in_word_tokens_not_in_punctuation():
    block = make_block("На протязі року, — як зазначено, — вікно було відчинене.")
    assert "na_protyazi" not in rule_ids(find_calques(block))


def test_find_calques_on_an_empty_block_returns_two_empty_tuples():
    assert find_calques_with_rejections(make_block("")) == ((), ())


def test_hits_are_ordered_by_start_then_rule_id():
    block = make_block(
        "Мета роботи заключається в тому, що дослідження проводиться "
        "у відповідності до вимог, а результат являється новим."
    )
    hits = find_calques(block)
    keys = [(h.raw_start, h.rule_id) for h in hits]
    assert keys == sorted(keys)
    assert {"zakliuchaietsya", "u_vidpovidnosti", "yavlyaietsya"} <= set(rule_ids(hits))


# ---------------------------------------------------------------------------
# Зони
# ---------------------------------------------------------------------------

def test_resolve_zone_prefers_the_larger_overlap():
    spans = (
        ZoneSpan(0, 10, TextZone.QUOTED_TEXT, Confidence.HIGH, "test"),
        ZoneSpan(10, 100, TextZone.AUTHOR_TEXT, Confidence.HIGH, "test"),
    )
    assert resolve_zone(spans, 8, 30) == TextZone.AUTHOR_TEXT
    assert resolve_zone(spans, 0, 9) == TextZone.QUOTED_TEXT


def test_resolve_zone_breaks_an_equal_overlap_by_zone_priority():
    spans = (
        ZoneSpan(0, 10, TextZone.AUTHOR_TEXT, Confidence.HIGH, "test"),
        ZoneSpan(10, 20, TextZone.BIBLIOGRAPHY, Confidence.HIGH, "test"),
    )
    assert resolve_zone(spans, 5, 15) == TextZone.BIBLIOGRAPHY


def test_resolve_zone_returns_uncertain_for_an_uncovered_interval():
    spans = (ZoneSpan(0, 5, TextZone.AUTHOR_TEXT, Confidence.HIGH, "test"),)
    assert resolve_zone(spans, 40, 50) == TextZone.UNCERTAIN
    # Порожній півінтервал розтягується на один символ, а не зникає.
    assert resolve_zone(spans, 2, 2) == TextZone.AUTHOR_TEXT


def test_hit_zone_comes_from_the_character_interval_of_the_match():
    raw = "Автор пише: «Явище являється типовим», і це явище являється фактом."
    quote_end = raw.index("»") + 1
    spans = (
        ZoneSpan(0, quote_end, TextZone.QUOTED_TEXT, Confidence.HIGH, "test"),
        ZoneSpan(quote_end, len(raw), TextZone.AUTHOR_TEXT, Confidence.HIGH, "test"),
    )
    zones = [h.zone for h in find_calques(make_block(raw, spans))]
    assert zones == [TextZone.QUOTED_TEXT, TextZone.AUTHOR_TEXT]


# ---------------------------------------------------------------------------
# Компоненти
# ---------------------------------------------------------------------------

def test_collapse_components_on_an_empty_input_is_empty():
    assert collapse_components(()) == ()


def test_overlapping_hits_leave_the_longest_span():
    hits = (hit("short", 1, 10, 16), hit("long", 2, 12, 30))
    assert rule_ids(collapse_components(hits)) == ["long"]


def test_equal_length_prefers_the_smaller_tier_then_the_smaller_rule_id():
    by_tier = (hit("b_rule", 2, 0, 10), hit("a_rule", 1, 3, 13))
    assert rule_ids(collapse_components(by_tier)) == ["a_rule"]
    by_id = (hit("b_rule", 1, 0, 10), hit("a_rule", 1, 3, 13))
    assert rule_ids(collapse_components(by_id)) == ["a_rule"]


def test_touching_but_not_overlapping_hits_stay_separate():
    hits = (hit("first", 1, 0, 10), hit("second", 1, 10, 20))
    assert rule_ids(collapse_components(hits)) == ["first", "second"]


def test_the_caller_keeps_the_hits_that_did_not_represent_a_component():
    hits = (hit("short", 1, 10, 16), hit("long", 2, 12, 30), hit("apart", 1, 50, 60))
    collapsed = collapse_components(hits)
    assert len(collapsed) == 2 < len(hits)
    assert "short" in rule_ids(hits)


def test_tier2_scores_only_when_tier1_is_present_in_the_author_text():
    tier2_only = (hit("t2", 2, 0, 5),)
    assert tier2_is_scorable(tier2_only) is False
    with_tier1 = tier2_only + (hit("t1", 1, 10, 15),)
    assert tier2_is_scorable(with_tier1) is True
    quoted_tier1 = tier2_only + (hit("t1", 1, 10, 15, TextZone.QUOTED_TEXT),)
    assert tier2_is_scorable(quoted_tier1) is False


# ---------------------------------------------------------------------------
# Щільність
# ---------------------------------------------------------------------------

def test_empty_document_gives_zeroes_and_zero_density():
    metrics = compute_metrics(make_document())
    assert metrics.author_words == 0
    assert metrics.tier1_hits == 0
    assert metrics.tier1_density == 0.0


def test_density_follows_the_formula_on_a_thousand_words():
    sentence = "Явище являється типовим. "
    filler = "слово " * 100
    block = make_block(sentence + filler)
    metrics = compute_metrics(make_document(block))
    assert metrics.tier1_hits == 1
    assert metrics.tier1_density == pytest.approx(
        DENSITY_PER_WORDS * metrics.tier1_hits / metrics.author_words
    )


def test_numeric_tokens_do_not_inflate_the_denominator():
    with_numbers = compute_metrics(make_document(make_block("слово 123 456 слово")))
    assert with_numbers.author_words == 2


def test_quoted_words_and_hits_stay_out_of_the_density_but_are_tracked():
    raw = "Цитата явля" + "ється прикладом" + ". Авторський текст без калік."
    quote_end = raw.index(".") + 1
    spans = (
        ZoneSpan(0, quote_end, TextZone.QUOTED_TEXT, Confidence.HIGH, "test"),
        ZoneSpan(quote_end, len(raw), TextZone.AUTHOR_TEXT, Confidence.HIGH, "test"),
    )
    metrics = compute_metrics(make_document(make_block(raw, spans)))
    assert metrics.tier1_hits == 0
    assert metrics.tier1_density == 0.0
    # Знаменник рахує лише авторські слова: «Авторський текст без калік».
    assert metrics.author_words == 4
    tracked = dict(metrics.excluded_zone_hits)
    assert tracked[TextZone.QUOTED_TEXT] == 1
    # Нульові лічильники видно завжди (CLAUDE.md, правило №3).
    assert tracked[TextZone.BIBLIOGRAPHY] == 0
    assert TextZone.AUTHOR_TEXT not in tracked


def test_tier3_is_counted_but_never_reaches_the_density():
    block = make_block("Таким чином, у цілому робота виконана послідовно.")
    metrics = compute_metrics(make_document(block))
    assert metrics.tier3_hits >= 1
    assert metrics.tier1_hits == 0
    assert metrics.tier1_density == 0.0


@pytest.mark.parametrize(
    "density, band",
    [
        (0.0, "neutral"),
        (0.79, "neutral"),
        (DENSITY_ELEVATED, "elevated"),
        (1.59, "elevated"),
        (DENSITY_PROMINENT, "prominent"),
        (12.0, "prominent"),
    ],
)
def test_density_band_boundaries(density, band):
    assert density_band(density) == band


@pytest.mark.parametrize(
    "words, hits, density, expected",
    [
        (LOCAL_MIN_WORDS, LOCAL_MIN_TIER1_HITS, LOCAL_MIN_DENSITY, True),
        (LOCAL_MIN_WORDS - 1, LOCAL_MIN_TIER1_HITS, LOCAL_MIN_DENSITY, False),
        (LOCAL_MIN_WORDS, LOCAL_MIN_TIER1_HITS - 1, LOCAL_MIN_DENSITY, False),
        (LOCAL_MIN_WORDS, LOCAL_MIN_TIER1_HITS, LOCAL_MIN_DENSITY - 0.01, False),
    ],
)
def test_section_is_locally_dense_requires_all_three_conditions(
    words, hits, density, expected
):
    assert section_is_locally_dense(words, hits, density) is expected


def test_numbers_of_the_step_are_the_numbers_of_the_plan():
    assert CONTEXT_WINDOW_TOKENS == 5
    assert DENSITY_PER_WORDS == 1000
    assert (DENSITY_ELEVATED, DENSITY_PROMINENT) == (0.8, 1.6)
    assert (LOCAL_MIN_WORDS, LOCAL_MIN_TIER1_HITS, LOCAL_MIN_DENSITY) == (
        1000,
        3,
        1.6,
    )
