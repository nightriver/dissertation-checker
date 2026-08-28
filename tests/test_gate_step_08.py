"""
Шлюз кроку 8a — `search/calques.py`: словник CALQUES (55 правил), аудит,
захисти вікна контексту та щільність tier 1 (`steps/step-08.md`).

Пишеться незалежно від реалізації — лише за розділами «Контракт», «Числа»,
«Seed», «Аудит», «Захисти», «Щільність» і «Шлюз» пакета кроку. Файл `search/
calques.py` та `tests/fixtures/calque_audit.json` цей тест НЕ читає окремо —
працює лише через публічний контракт (`CALQUES`, `CalqueRule`, `CalqueHit`,
`find_calques`, `collapse_components`, `compute_metrics`, `density_band`,
`section_is_locally_dense`, `DICT_VERSION`, `DENSITY_ELEVATED`,
`DENSITY_PROMINENT`).

Для правил, чия доля (required_context/forbidden_context, конкретний
rule_id) вирішується аудитом і невідома цьому тесту заздалегідь (п.10-13,
18-25, 30), тест monkeypatch'ить `search.calques.CALQUES` синтетичним
правилом, побудованим із того самого публічного `CalqueRule` — це перевіряє
саме механіку контракту, а не конкретні рішення аудиту 55 записів.

`SearchBlock` для цих синтетичних правил будується через СПРАВЖНІ
`search.normalization.normalize_text` / `tokenize` (крок 4, вже закритий) —
так само, як це робив би реальний `parser.searchdoc`, а не вигаданим
наближенням цього тесту.

Нумерація `test_gate_NN_*` відповідає пунктам розділу «Шлюз» пакета
(1-30). Рядки таблиці «Відмови», не покриті жодним із цих 30 пунктів
дослівно (tier2-без-tier1, зона BIBLIOGRAPHY), винесені окремими
`test_reject_*`.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

import search.calques as calques
from search.calques import (
    CALQUES,
    CalqueHit,
    CalqueRule,
    DENSITY_ELEVATED,
    DENSITY_PROMINENT,
    DICT_VERSION,
    collapse_components,
    compute_metrics,
    density_band,
    find_calques,
    section_is_locally_dense,
)
from search.normalization import normalize_text, tokenize
from search.types import Confidence, SearchBlock, SearchDocument, TextZone, ZoneSpan
from tools.measure_calques import CALQUES as SEED_CALQUES

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
AUDIT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "calque_audit.json"

# Значення DICT_VERSION до аудиту (контракт кроку 8a) — після аудиту повинно
# змінитись обов'язково.
_PREVIOUS_DICT_VERSION = "calques-dict-2026-08-25"

# Дев'ять PDF корпусу — той самий набір, що й у
# tests/fixtures/search_corpus_expectations.json (крок 2).
NINE_CORPUS_PDFS = (
    "Работа май-docx-2.pdf",
    "Гончарова-Парфьонова_дисертація.pdf",
    "DISSERTAZIYA.doc.pdf",
    "diss-doc.pdf",
    "diskor-корецька.pdf",
    "diser.pdf",
    "dis2005_bayar_kandidat.PDF",
    "dis.doc-КОЦЮБА.pdf",
    "Dis-doc-марченко.pdf",
)


# ---------------------------------------------------------------------------
# Допоміжні будівники
# ---------------------------------------------------------------------------


def _rule(
    rule_id: str,
    pattern: str,
    *,
    tier: int | None = 1,
    status: str = "active",
    required_context: tuple[str, ...] = (),
    forbidden_context: tuple[str, ...] = (),
) -> CalqueRule:
    """Синтетичне правило для тестів механіки — не одне з 55 продуктових."""
    return CalqueRule(
        rule_id=rule_id,
        pattern=pattern,
        ru_origin="тест-джерело",
        uk_norm="тест-норма",
        status=status,
        tier=tier,
        group="gate-test",
        required_context=required_context,
        forbidden_context=forbidden_context,
        rationale="синтетичне правило шлюзового тесту кроку 8a",
        evidence_refs=(),
    )


def _hit(
    rule_id: str,
    tier: int,
    start: int,
    end: int,
    *,
    text: str = "x",
    zone: TextZone = TextZone.AUTHOR_TEXT,
) -> CalqueHit:
    return CalqueHit(
        rule_id=rule_id, tier=tier, raw_start=start, raw_end=end,
        matched_text=text, zone=zone,
    )


def _make_block(
    raw_text: str,
    *,
    zone: TextZone = TextZone.AUTHOR_TEXT,
    zone_spans: tuple[ZoneSpan, ...] | None = None,
    block_id: str = "b1",
    section_id: str = "s1",
    physical_page: int = 1,
    block_index: int = 0,
) -> SearchBlock:
    """Справжня нормалізація/токенізація кроку 4 — не імітація цього тесту."""
    normalized = normalize_text(raw_text)
    tokens = tokenize(raw_text, normalized)
    if zone_spans is None:
        zone_spans = (ZoneSpan(0, len(raw_text), zone, Confidence.HIGH, "gate-test"),)
    return SearchBlock(
        block_id=block_id,
        raw_text=raw_text,
        normalized=normalized,
        tokens=tokens,
        section_id=section_id,
        heading_path=(),
        physical_page=physical_page,
        block_index=block_index,
        zone_spans=zone_spans,
    )


def _make_document(blocks: tuple[SearchBlock, ...]) -> SearchDocument:
    return SearchDocument(
        document_sha256="0" * 64,
        parser_version="gate-test-parser",
        n_pages=1,
        pages=(),
        expected_body_pages=0,
        extractable_body_pages=0,
        coverage_ratio=0.0,
        blocks=blocks,
        sections=(),
        sentences=(),
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.LOW,
        applied_overrides=(),
    )


def _load_audit_records() -> list[dict]:
    """
    Пакет не фіксує форму верхнього рівня tests/fixtures/calque_audit.json
    (лише перелік полів на запис) — приймаємо або "голий" список записів,
    або словник із першим списковим значенням під одним із очікуваних
    ключів. Якщо жодне не підійшло — явна помилка, а не мовчазний False.
    """
    with AUDIT_FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("rules", "records", "entries", "audits", "audit"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        for value in data.values():
            if isinstance(value, list):
                return value
    raise AssertionError(
        "tests/fixtures/calque_audit.json: не вдалося визначити список записів "
        "аудиту — пакет кроку 8a не фіксує форму верхнього рівня JSON."
    )


# ---------------------------------------------------------------------------
# Синтетичний сценарій щільності для пунктів 18-22, 25, 30
# ---------------------------------------------------------------------------

_TIER1_MARKER = "vtmarkerone"
_TIER2_MARKER = "vtmarkertwo"
_TIER3_MARKER = "vtmarkerthree"

_FILLER_COUNT = 44
_EXPECTED_AUTHOR_WORDS = 50  # 44 заповнювачі + 3 tier1 + 2 tier2 + 1 tier3
_EXPECTED_TIER1_HITS = 3
_EXPECTED_TIER2_HITS = 2
_EXPECTED_TIER3_HITS = 1

_QUOTED_BLOCK_TEXT = "цитата слово слово слово слово " + _TIER1_MARKER
_QUOTED_BLOCK_WORD_COUNT = 6  # якби рахувався в знаменнику — зіпсував би п.20


def _patch_density_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calques,
        "CALQUES",
        (
            _rule("test_density_tier1", rf"\b{_TIER1_MARKER}\b", tier=1),
            _rule("test_density_tier2", rf"\b{_TIER2_MARKER}\b", tier=2),
            _rule("test_density_tier3", rf"\b{_TIER3_MARKER}\b", tier=3),
        ),
    )


def _density_scenario_document() -> SearchDocument:
    author_parts = (
        ["слово"] * _FILLER_COUNT
        + [_TIER1_MARKER] * 3
        + [_TIER2_MARKER] * 2
        + [_TIER3_MARKER] * 1
    )
    author_block = _make_block(
        " ".join(author_parts), zone=TextZone.AUTHOR_TEXT, block_id="author"
    )
    quoted_block = _make_block(
        _QUOTED_BLOCK_TEXT, zone=TextZone.QUOTED_TEXT, block_id="quoted"
    )
    footnote_block = _make_block(
        "слово слово слово", zone=TextZone.FOOTNOTE_TEXT, block_id="footnote"
    )
    return _make_document((author_block, quoted_block, footnote_block))


# ---------------------------------------------------------------------------
# 1. CALQUES — рівно 55 записів, унікальні rule_id.
# ---------------------------------------------------------------------------


def test_gate_01_calques_has_exactly_55_unique_rule_ids() -> None:
    assert len(CALQUES) == 55
    assert len({rule.rule_id for rule in CALQUES}) == 55


# ---------------------------------------------------------------------------
# 2. Множина rule_id збігається з tools/measure_calques.py.
# ---------------------------------------------------------------------------


def test_gate_02_rule_id_set_matches_the_tools_measure_calques_seed() -> None:
    seed_ids = {cid for cid, *_rest in SEED_CALQUES}
    assert {rule.rule_id for rule in CALQUES} == seed_ids


# ---------------------------------------------------------------------------
# 3. Кожне правило має рівно один запис аудиту — і навпаки.
# ---------------------------------------------------------------------------


def test_gate_03_every_calque_rule_has_exactly_one_audit_record_and_no_extras() -> None:
    records = _load_audit_records()
    record_ids = [record["rule_id"] for record in records]
    assert len(record_ids) == len(set(record_ids)), "дублікати rule_id у фікстурі аудиту"
    assert set(record_ids) == {rule.rule_id for rule in CALQUES}


# ---------------------------------------------------------------------------
# 4. status — active або excluded.
# ---------------------------------------------------------------------------


def test_gate_04_every_rule_status_is_active_or_excluded() -> None:
    for rule in CALQUES:
        assert rule.status in ("active", "excluded"), rule.rule_id


# ---------------------------------------------------------------------------
# 5. Активне правило має tier 1/2/3; виключене — tier is None.
# ---------------------------------------------------------------------------


def test_gate_05_active_rules_have_a_tier_and_excluded_rules_have_none() -> None:
    for rule in CALQUES:
        if rule.status == "active":
            assert rule.tier in (1, 2, 3), rule.rule_id
        else:
            assert rule.tier is None, rule.rule_id


# ---------------------------------------------------------------------------
# 6. Непорожній rationale в кожного правила.
# ---------------------------------------------------------------------------


def test_gate_06_every_rule_has_a_non_empty_rationale() -> None:
    empty = [rule.rule_id for rule in CALQUES if not rule.rationale.strip()]
    assert empty == []


# ---------------------------------------------------------------------------
# 7. tier_before фікстури == tier seed'а; розподіл seed 20/29/6.
# ---------------------------------------------------------------------------


def test_gate_07_tier_before_matches_the_seed_and_seed_distribution_is_20_29_6() -> None:
    seed_tier_by_id = {cid: tier for cid, _pattern, _ru, _uk, tier in SEED_CALQUES}
    records = _load_audit_records()
    for record in records:
        assert record["tier_before"] == seed_tier_by_id[record["rule_id"]], record["rule_id"]

    distribution = Counter(seed_tier_by_id.values())
    assert distribution == Counter({1: 20, 2: 29, 3: 6})


# ---------------------------------------------------------------------------
# 8. Виключене правило не спрацьовує ніколи, навіть коли текст відповідає
#    його регулярці.
# ---------------------------------------------------------------------------


def test_gate_08_an_excluded_rule_never_fires_even_when_its_pattern_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule("excluded_probe", r"\bexcludedword\b", tier=None, status="excluded")
    monkeypatch.setattr(calques, "CALQUES", (rule,))
    block = _make_block("тут стоїть excludedword у реченні.")
    assert find_calques(block) == ()


# ---------------------------------------------------------------------------
# 9. Апостроф: з'являється / пред'являється не спрацьовують; являється — так.
# ---------------------------------------------------------------------------


def test_gate_09_apostrophe_boundary_protects_yavlyaietsya_from_compound_forms() -> None:
    rule = next((r for r in CALQUES if r.rule_id == "yavlyaietsya"), None)
    assert rule is not None, "правило yavlyaietsya мусить лишитись у CALQUES (сохраняются дословно)"
    assert rule.status == "active", "пакет обіцяє: 'являється' — дає спрацювання"

    def _fires(text: str) -> bool:
        hits = find_calques(_make_block(text))
        return any(h.rule_id == "yavlyaietsya" for h in hits)

    assert not _fires("це з'являється у роботі.")
    assert not _fires("це пред'являється судом.")
    assert _fires("це поняття являється основним.")


# ---------------------------------------------------------------------------
# 10. required_context: без контекстного слова у вікні — немає збігу, з ним
#     — є.
# ---------------------------------------------------------------------------


def test_gate_10_required_context_gates_the_match_within_the_five_word_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule("ctx_required", r"\bctxmarker\b", tier=1, required_context=("neededword",))
    monkeypatch.setattr(calques, "CALQUES", (rule,))

    far_text = "ctxmarker " + " ".join(["filler"] * 10) + " neededword"
    near_text = "neededword ctxmarker"

    far_hits = find_calques(_make_block(far_text))
    near_hits = find_calques(_make_block(near_text))

    assert not any(h.rule_id == "ctx_required" for h in far_hits)
    assert any(h.rule_id == "ctx_required" for h in near_hits)


# ---------------------------------------------------------------------------
# 11. forbidden_context: заборонене слово у вікні гасить збіг.
# ---------------------------------------------------------------------------


def test_gate_11_forbidden_context_suppresses_the_match_within_the_five_word_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule("ctx_forbidden", r"\bctxmarker\b", tier=1, forbidden_context=("badword",))
    monkeypatch.setattr(calques, "CALQUES", (rule,))

    clean_text = "ctxmarker тут немає заборонених слів поруч."
    poisoned_text = "badword ctxmarker"

    clean_hits = find_calques(_make_block(clean_text))
    poisoned_hits = find_calques(_make_block(poisoned_text))

    assert any(h.rule_id == "ctx_forbidden" for h in clean_hits)
    assert not any(h.rule_id == "ctx_forbidden" for h in poisoned_hits)


# ---------------------------------------------------------------------------
# 12. Вікно контексту рахує лише словесні токени — пунктуація бюджет не
#     витрачає.
# ---------------------------------------------------------------------------


def test_gate_12_context_window_counts_word_tokens_only_not_punctuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule("ctx_window", r"\bctxmarker\b", tier=1, required_context=("target",))
    monkeypatch.setattr(calques, "CALQUES", (rule,))

    # target — 3-й СЛОВЕСНИЙ токен від збігу (alpha, beta, target), тобто в
    # межах вікна ±5; але 6-й СИРИЙ токен разом з комами (,) — якщо вікно
    # хибно рахує пунктуацію, target опиниться "поза вікном" і збігу не буде.
    text = "ctxmarker , alpha , beta , target"
    hits = find_calques(_make_block(text))
    assert any(h.rule_id == "ctx_window" for h in hits)


# ---------------------------------------------------------------------------
# 13. Зона визначається інтервалом символів збігу, а не міткою всього блоку.
# ---------------------------------------------------------------------------


def test_gate_13_hit_zone_is_taken_from_the_character_interval_not_the_whole_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule("zone_probe", r"\bzoneword\b", tier=1)
    monkeypatch.setattr(calques, "CALQUES", (rule,))

    raw_text = "автор пише zoneword та ще раз zoneword тут."
    first = raw_text.index("zoneword")
    second = raw_text.index("zoneword", first + 1)
    boundary = second

    zone_spans = (
        ZoneSpan(0, boundary, TextZone.AUTHOR_TEXT, Confidence.HIGH, "gate-test"),
        ZoneSpan(boundary, len(raw_text), TextZone.QUOTED_TEXT, Confidence.HIGH, "gate-test"),
    )
    block = _make_block(raw_text, zone_spans=zone_spans)

    hits_by_start = {h.raw_start: h for h in find_calques(block)}
    assert hits_by_start[first].zone == TextZone.AUTHOR_TEXT
    assert hits_by_start[second].zone == TextZone.QUOTED_TEXT


# ---------------------------------------------------------------------------
# 14. Компонента: пересічні збіги дають одного представника — довший span.
# ---------------------------------------------------------------------------


def test_gate_14_overlapping_hits_collapse_to_the_longer_span() -> None:
    short = _hit("short_rule", 1, 0, 10)
    long_ = _hit("long_rule", 1, 2, 15)
    result = collapse_components((short, long_))
    assert result == (long_,)


# ---------------------------------------------------------------------------
# 15. Однакова довжина -> менший tier; рівні довжина й tier -> менший
#     rule_id.
# ---------------------------------------------------------------------------


def test_gate_15_equal_length_ties_break_by_tier_then_by_rule_id() -> None:
    tier2 = _hit("zzz_rule", 2, 0, 10)
    tier1 = _hit("aaa_rule", 1, 0, 10)
    by_tier = collapse_components((tier2, tier1))
    assert by_tier == (tier1,)

    rule_b = _hit("bbb_rule", 1, 0, 10)
    rule_a = _hit("aaa_rule", 1, 0, 10)
    by_id = collapse_components((rule_b, rule_a))
    assert by_id == (rule_a,)


# ---------------------------------------------------------------------------
# 16. Непересічні збіги компонентою не стають — обидва лишаються.
# ---------------------------------------------------------------------------


def test_gate_16_non_overlapping_hits_do_not_become_a_component_both_remain() -> None:
    first = _hit("first_rule", 1, 0, 5)
    second = _hit("second_rule", 1, 10, 15)
    result = collapse_components((first, second))
    assert set(result) == {first, second}
    assert len(result) == 2


# ---------------------------------------------------------------------------
# 17. Повний список збігів довший за результат collapse_components.
# ---------------------------------------------------------------------------


def test_gate_17_the_full_hit_list_stays_available_and_is_longer_than_the_collapsed_result() -> None:
    short = _hit("short_rule", 1, 0, 10)
    long_ = _hit("long_rule", 1, 2, 15)
    hits = (short, long_)
    collapsed = collapse_components(hits)
    assert len(hits) > len(collapsed)
    assert short in hits and long_ in hits


# ---------------------------------------------------------------------------
# 18. Щільність за формулою 1000 * tier1_hits / author_words.
# ---------------------------------------------------------------------------


def test_gate_18_density_matches_the_1000_times_tier1_over_author_words_formula(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_density_rules(monkeypatch)
    metrics = compute_metrics(_density_scenario_document())
    assert metrics.author_words == _EXPECTED_AUTHOR_WORDS
    assert metrics.tier1_hits == _EXPECTED_TIER1_HITS
    assert metrics.tier1_density == pytest.approx(
        1000 * _EXPECTED_TIER1_HITS / _EXPECTED_AUTHOR_WORDS
    )


# ---------------------------------------------------------------------------
# 19. author_words == 0 дає tier1_density == 0.0 без винятку.
# ---------------------------------------------------------------------------


def test_gate_19_zero_author_words_gives_zero_density_without_a_crash() -> None:
    empty_document = _make_document(())
    metrics = compute_metrics(empty_document)
    assert metrics.author_words == 0
    assert metrics.tier1_density == 0.0


# ---------------------------------------------------------------------------
# 20. Знаменник рахує лише AUTHOR_TEXT.
# ---------------------------------------------------------------------------


def test_gate_20_the_denominator_counts_only_author_text_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_density_rules(monkeypatch)
    metrics = compute_metrics(_density_scenario_document())
    assert metrics.author_words == _EXPECTED_AUTHOR_WORDS
    # Якби слова цитатного блоку (ще 6 токенів) враховувались у знаменнику,
    # сума була б іншою — явний контраст, а не той самий підрахунок, що в п.18.
    assert metrics.author_words != _EXPECTED_AUTHOR_WORDS + _QUOTED_BLOCK_WORD_COUNT


# ---------------------------------------------------------------------------
# 21. Числитель рахує лише AUTHOR_TEXT: tier1 у цитаті — не в tier1_hits, але
#     в excluded_zone_hits.
# ---------------------------------------------------------------------------


def test_gate_21_tier1_hits_inside_a_quote_are_excluded_from_the_numerator_but_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_density_rules(monkeypatch)
    metrics = compute_metrics(_density_scenario_document())
    assert metrics.tier1_hits == _EXPECTED_TIER1_HITS  # без внеску цитатного блоку
    excluded = dict(metrics.excluded_zone_hits)
    assert excluded[TextZone.QUOTED_TEXT] == 1


# ---------------------------------------------------------------------------
# 22. excluded_zone_hits показує нульовий рахунок для зони без збігів.
# ---------------------------------------------------------------------------


def test_gate_22_excluded_zone_hits_lists_a_zero_count_for_a_zone_with_no_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_density_rules(monkeypatch)
    metrics = compute_metrics(_density_scenario_document())
    excluded = dict(metrics.excluded_zone_hits)
    assert TextZone.FOOTNOTE_TEXT in excluded
    assert excluded[TextZone.FOOTNOTE_TEXT] == 0


# ---------------------------------------------------------------------------
# 23. density_band на межах 0.8 і 1.6.
# ---------------------------------------------------------------------------


def test_gate_23_density_band_boundaries_at_0_8_and_1_6() -> None:
    assert density_band(0.79) == "neutral"
    assert density_band(0.8) == "elevated"
    assert density_band(1.59) == "elevated"
    assert density_band(1.6) == "prominent"


# ---------------------------------------------------------------------------
# 24. section_is_locally_dense вимагає всіх трьох умов одночасно.
# ---------------------------------------------------------------------------


def test_gate_24_locally_dense_requires_all_three_conditions_at_once() -> None:
    assert section_is_locally_dense(author_words=999, tier1_hits=2, density=1.59) is False
    assert section_is_locally_dense(author_words=1000, tier1_hits=3, density=1.6) is True
    # Ізоляція кожної умови окремо: інші дві на прохідних значеннях, одна —
    # рівно на межі провалу (ті самі числа пакета, лише перекомбіновані).
    assert section_is_locally_dense(author_words=999, tier1_hits=3, density=1.6) is False
    assert section_is_locally_dense(author_words=1000, tier1_hits=2, density=1.6) is False
    assert section_is_locally_dense(author_words=1000, tier1_hits=3, density=1.59) is False


# ---------------------------------------------------------------------------
# 25. tier3 не дає балу: рахується в tier3_hits, у tier1_density не входить.
# ---------------------------------------------------------------------------


def test_gate_25_tier3_is_counted_but_never_contributes_to_the_density(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_density_rules(monkeypatch)
    metrics = compute_metrics(_density_scenario_document())
    assert metrics.tier3_hits == _EXPECTED_TIER3_HITS
    assert metrics.tier1_density == pytest.approx(
        1000 * _EXPECTED_TIER1_HITS / _EXPECTED_AUTHOR_WORDS
    )


# ---------------------------------------------------------------------------
# 26. Неперевірений tier 1 заборонений: кожне активне tier1-правило
#     зустрічається у дев'яти PDF або має evidence_refs (позитивний приклад).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nine_pdf_normalized_texts() -> tuple[str, ...]:
    from tools.measure_calques import normalize as legacy_normalize

    texts: list[str] = []
    for fname in NINE_CORPUS_PDFS:
        path = EXAMPLES_DIR / fname
        document = fitz.open(str(path))
        try:
            raw = "\n".join(page.get_text("text") for page in document)
        finally:
            document.close()
        texts.append(legacy_normalize(raw))
    return tuple(texts)


def test_gate_26_every_active_tier1_rule_is_seen_in_the_corpus_or_has_a_manual_example(
    nine_pdf_normalized_texts: tuple[str, ...],
) -> None:
    """
    Непроверенный tier 1 запрещён (§8.2). Пакет не називає окреме поле
    фікстури для "вручну перевіреного позитивного прикладу" серед десяти
    перелічених полів запису аудиту — трактуємо непорожній `evidence_refs`
    самого правила `CALQUES` як таку ознаку (задокументовано у звіті
    gate-writer як прогалина пакета кроку 8a).
    """
    unverified = []
    for rule in CALQUES:
        if rule.status != "active" or rule.tier != 1:
            continue
        found_in_corpus = any(
            re.search(rule.pattern, text) for text in nine_pdf_normalized_texts
        )
        has_manual_example = len(rule.evidence_refs) > 0
        if not found_in_corpus and not has_manual_example:
            unverified.append(rule.rule_id)
    assert unverified == [], (
        f"неперевірені tier 1 правила (не зустрілись у 9 PDF і не мають "
        f"evidence_refs): {unverified}"
    )


# ---------------------------------------------------------------------------
# 27. DICT_VERSION — непорожній рядок, відмінний від базового значення.
# ---------------------------------------------------------------------------


def test_gate_27_dict_version_is_nonempty_and_bumped_from_the_frozen_baseline() -> None:
    assert isinstance(DICT_VERSION, str)
    assert DICT_VERSION != ""
    assert DICT_VERSION != _PREVIOUS_DICT_VERSION


# ---------------------------------------------------------------------------
# 28. Фікстуру аудиту не перезаписує (і навіть не згадує) продуктовий код.
# ---------------------------------------------------------------------------

_SCANNED_PRODUCT_ROOTS = ("search", "parser", "compare")
_SCANNED_PRODUCT_FILES = ("app.py", "ui_helpers.py")


def test_gate_28_the_audit_fixture_is_never_mentioned_by_product_code() -> None:
    offenders: list[str] = []
    for root_name in _SCANNED_PRODUCT_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "calque_audit" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    for fname in _SCANNED_PRODUCT_FILES:
        path = REPO_ROOT / fname
        if path.is_file() and "calque_audit" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(fname)
    assert offenders == [], f"продуктовий код згадує фікстуру аудиту: {offenders}"


# ---------------------------------------------------------------------------
# 29. Пороги 0.8 і 1.6 не змінені.
# ---------------------------------------------------------------------------


def test_gate_29_density_thresholds_are_exactly_0_8_and_1_6() -> None:
    assert DENSITY_ELEVATED == 0.8
    assert DENSITY_PROMINENT == 1.6


# ---------------------------------------------------------------------------
# 30. Детермінізм: два прогони дають рівний результат і той самий порядок.
# ---------------------------------------------------------------------------


def test_gate_30_two_runs_of_find_calques_collapse_and_metrics_agree_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_density_rules(monkeypatch)
    document = _density_scenario_document()

    hits_1 = find_calques(document.blocks[0])
    hits_2 = find_calques(document.blocks[0])
    assert hits_1 == hits_2

    collapsed_1 = collapse_components(hits_1)
    collapsed_2 = collapse_components(hits_2)
    assert collapsed_1 == collapsed_2

    metrics_1 = compute_metrics(document)
    metrics_2 = compute_metrics(document)
    assert metrics_1 == metrics_2


# ---------------------------------------------------------------------------
# Рядки «Відмови», не покриті пунктами 1-30 дослівно.
# ---------------------------------------------------------------------------


def test_reject_bibliography_zone_hits_are_excluded_from_density_but_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Відмова 'excluded_zone' для зони BIBLIOGRAPHY окремо від QUOTED_TEXT
    (п.21 перевіряє лише цитату)."""
    rule = _rule("biblio_probe", r"\bbibmarker\b", tier=1)
    monkeypatch.setattr(calques, "CALQUES", (rule,))
    author_block = _make_block("слово слово слово", zone=TextZone.AUTHOR_TEXT, block_id="a")
    biblio_block = _make_block("bibmarker", zone=TextZone.BIBLIOGRAPHY, block_id="b")
    document = _make_document((author_block, biblio_block))
    metrics = compute_metrics(document)
    assert metrics.tier1_hits == 0
    excluded = dict(metrics.excluded_zone_hits)
    assert excluded[TextZone.BIBLIOGRAPHY] == 1


def test_reject_tier2_hit_without_tier1_in_the_block_is_still_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Відмова 'tier2_without_tier1': балу не дає (у tier1_density не
    входить ніколи), але tier2_hits показується завжди."""
    rule = _rule("tier2_only_probe", r"\bt2only\b", tier=2)
    monkeypatch.setattr(calques, "CALQUES", (rule,))
    block = _make_block("t2only з'явилося без сусіднього tier 1.", zone=TextZone.AUTHOR_TEXT)
    document = _make_document((block,))
    metrics = compute_metrics(document)
    assert metrics.tier1_hits == 0
    assert metrics.tier2_hits == 1
