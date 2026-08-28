"""
Шлюз кроку 6 — `search/bibliography.py`: записи, заглавия, координати,
посилання, ID (`steps/step-06.md`).

Пишеться незалежно від реалізації, паралельний виконавець `search/bibliography.py`
не читається. Очікувані значення виведені з тексту пакета (розділи «Контракт»,
«Числа», «Ідентифікатори», «Заглавие», «Зв'язок згадки з записом», «Шлюз»,
«Відмови»), а не зі спостереженого виводу.

Синтетичні PDF збираються прямо в тестах через `fitz`, тим самим прийомом
`insert_htmlbox`, що вже перевірений і використаний у `tests/test_gate_step_05.py`
(кирилиця через html-рендер, латиниця/цифри — де мова вмісту не важлива).

Пункти 14 і 25 навмисно НЕ будуються повторним рендером PDF: `entry_id`
перевіряється на стійкість до зсуву block_id/block_index, а мʼякий перенос
(U+00AD) емпірично не переживає round-trip через `fitz.insert_text`/
`insert_htmlbox` (символ або відкидається, або підмінюється видимим дефісом
— перевірено окремо). Обидва тести беруть уже розібраний `SearchDocument`
(побудований звичайним `parse_search_document` на реальному PDF) і хірургічно
змінюють ОДИН блок через `dataclasses.replace`, перераховуючи
`normalized`/`tokens` тим самим `search.normalization`, яким користується сам
парсер кроку 5. Це зміна вхідних даних контракту `SearchDocument`, а не
читання реалізації `search/bibliography.py`.

Пункт 18 («немає звʼязку з донором») перевіряється диференційно через
контрактну `donor_ids_for_mention`: один `CitationMention` представляє місце
посилання і не дублюється на кожного донора, тому рахувати самі згадки тут
було б нечутливо до блокування звʼязку.

Пункти 28-31 йдуть по девʼяти реальних PDF з `examples/`; очікування — з
`tests/fixtures/search_corpus_expectations.json` (лише читання).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from parser.searchdoc import parse_search_document
from search.bibliography import (
    build_bibliography,
    build_citations,
    donor_ids_for_mention,
)
from search.normalization import normalize_text, tokenize
from search.types import CONTENT_SECTION_KINDS, Confidence, Language

# Числа пакета кроку 6 (розділ «Числа») — не вигадані, продубльовані тут,
# щоб тест не залежав від приватних констант `search/bibliography.py`.
TITLE_MIN_LETTER_TOKENS = 3
TITLE_MAX_LETTER_TOKENS = 30
ID_HEX_LENGTH = 16
YEAR_MIN = 1800
YEAR_MAX = 2100

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_corpus_expectations.json"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")

_HTML_RECT = fitz.Rect(72, 72, PAGE_WIDTH - 72, PAGE_HEIGHT - 72)


# ---------------------------------------------------------------------------
# Допоміжні будівники синтетичних PDF (той самий прийом, що й у кроці 5)
# ---------------------------------------------------------------------------


def _new_page(doc: "fitz.Document"):
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _finish(doc: "fitz.Document") -> bytes:
    data = doc.tobytes()
    doc.close()
    return data


def _insert_html(page, html: str, rect: "fitz.Rect | None" = None) -> None:
    page.insert_htmlbox(rect or _HTML_RECT, html)


def _make_bibliography_pdf_bytes(entries: list[str]) -> bytes:
    """PDF з одним заголовком «СПИСОК ЛІТЕРАТУРИ» і переданими записами."""
    doc = fitz.open()
    page = _new_page(doc)
    html = "<p>СПИСОК ЛІТЕРАТУРИ</p>" + "".join(f"<p>{e}</p>" for e in entries)
    _insert_html(page, html)
    return _finish(doc)


def _build_bibliography_document(entries: list[str]):
    return parse_search_document(_make_bibliography_pdf_bytes(entries))


def _make_citation_pdf_bytes(body_text: str, bib_entries: list[str]) -> bytes:
    """PDF з абзацом тіла (одним `<p>`, може містити кілька речень) на
    першому листі і бібліографією на другому."""
    doc = fitz.open()
    p1 = _new_page(doc)
    _insert_html(p1, f"<p>РОЗДІЛ 1</p><p>{body_text}</p>")
    p2 = _new_page(doc)
    bib_html = "<p>СПИСОК ЛІТЕРАТУРИ</p>" + "".join(f"<p>{e}</p>" for e in bib_entries)
    _insert_html(p2, bib_html)
    return _finish(doc)


def _build_citation_document(body_text: str, bib_entries: list[str]):
    return parse_search_document(_make_citation_pdf_bytes(body_text, bib_entries))


def _reconstruct(document, source) -> str:
    """Відновлює текст із `SourceSpan.parts`, зрізаючи `raw_text` блоків
    документа — незалежна перевірка того, що координати справді ведуть на
    реальні вихідні символи (шлюз, п.4 і п.25)."""
    block_by_id = {b.block_id: b for b in document.blocks}
    return "".join(
        block_by_id[part.block_id].raw_text[part.raw_start : part.raw_end]
        for part in source.parts
    )


BIB_1_3 = [
    "1. Іванов І. І. Перша праця. Київ, 2001. 90 с.",
    "2. Петров П. П. Друга праця. Львів, 2010. 110 с.",
    "3. Сидоренко С. С. Третя праця. Одеса, 2015. 95 с.",
]


# ---------------------------------------------------------------------------
# 1. Документ без зони BIBLIOGRAPHY: обидві функції дають (), без винятку.
# ---------------------------------------------------------------------------


def test_gate_01_a_document_without_a_bibliography_zone_gives_empty_tuples() -> None:
    """Документ лише з ВСТУПом, без заголовка списку літератури і без межі,
    яку міг би знайти `parser.bibliography.split_zones` — `build_bibliography`
    і `build_citations` дають `()`, без винятку (шлюз, п.1; «Контракт»)."""
    doc = fitz.open()
    page = _new_page(doc)
    filler = (
        "Дисертаційне дослідження присвячене актуальній проблемі сучасної "
        "науки та її практичному застосуванню в певній галузі знань."
    )
    _insert_html(page, f"<p>ВСТУП</p><p>{filler}</p>")
    pdf_bytes = _finish(doc)
    document = parse_search_document(pdf_bytes)

    entries = build_bibliography(document)
    assert entries == ()
    citations = build_citations(document, entries)
    assert citations == ()


# ---------------------------------------------------------------------------
# 2. Три пронумеровані записи дають три BibliographyEntry по порядку.
# ---------------------------------------------------------------------------


def test_gate_02_three_numbered_entries_give_three_entries_in_order() -> None:
    """Проста нумерована бібліографія з трьох записів дає три
    `BibliographyEntry` з `ordinal` 1, 2, 3 саме в цьому порядку (шлюз, п.2)."""
    document = _build_bibliography_document(BIB_1_3)

    entries = build_bibliography(document)

    assert len(entries) == 3
    assert [e.ordinal for e in entries] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Багаторядковий запис збирається в один.
# ---------------------------------------------------------------------------


def test_gate_03_a_multiline_entry_is_collected_into_a_single_record() -> None:
    """Запис №1 з дуже довгою назвою, що переноситься на другий рядок PDF,
    дає рівно один запис з ordinal=1, чий текст містить обидві частини назви;
    запис №2 лишається окремим і коротким (шлюз, п.3)."""
    long_title_entry = (
        "1. Іванов І. І. Дуже довга назва наукової праці яка обов'язково "
        "перенесеться на другий рядок аркуша документа через свою значну "
        "довжину і кількість слів у ній. Київ, 2005. 220 с."
    )
    document = _build_bibliography_document([long_title_entry, BIB_1_3[1]])

    entries = build_bibliography(document)

    ones = [e for e in entries if e.ordinal == 1]
    assert len(ones) == 1
    entry1 = ones[0]
    normalized_raw = " ".join(entry1.raw_text.split())
    assert "Дуже довга назва" in normalized_raw
    assert "перенесеться на другий рядок" in normalized_raw
    assert "Київ, 2005" in normalized_raw

    twos = [e for e in entries if e.ordinal == 2]
    assert len(twos) == 1
    assert "Дуже довга назва" not in twos[0].raw_text


# ---------------------------------------------------------------------------
# 4. raw_text запису відновлюється з координат source.parts.
# ---------------------------------------------------------------------------


def test_gate_04_entry_raw_text_matches_a_reconstruction_from_source_coordinates() -> None:
    """Для кожного запису `document.blocks[...].raw_text[start:end]`,
    зрізаний за `source.parts`, дає рівно `entry.raw_text` (шлюз, п.4)."""
    document = _build_bibliography_document(BIB_1_3)

    entries = build_bibliography(document)

    assert len(entries) == 3
    for entry in entries:
        assert _reconstruct(document, entry.source) == entry.raw_text


# ---------------------------------------------------------------------------
# 5. Заглавие в лапках дає HIGH і заглавие без лапок.
# ---------------------------------------------------------------------------


def test_gate_05_a_quoted_title_gives_high_confidence_and_strips_the_quotes() -> None:
    """Запис із заглавием у «...» дає `title_confidence == HIGH` і `title`
    без символів лапок (шлюз, п.5; §12.5, п.2)."""
    document = _build_bibliography_document(
        ["2. Петренко О. О. «Роль інформаційних технологій у сучасній освіті». Львів, 2015. 180 с."]
    )

    entries = build_bibliography(document)
    entry = next(e for e in entries if e.ordinal == 2)

    assert entry.title is not None
    assert entry.title.strip() == "Роль інформаційних технологій у сучасній освіті"
    assert "«" not in entry.title and "»" not in entry.title
    assert entry.title_confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 6. «Прізвище І. І. Заглавие. — Місто, рік» дає MEDIUM.
# ---------------------------------------------------------------------------


def test_gate_06_author_dash_title_structure_gives_medium_confidence() -> None:
    """Запис «Коваленко В. В. Формування ... фахівців. — Київ, 2005. 220 с.»
    (без лапок) дає `title_confidence == MEDIUM`, а `title` не містить
    прізвища й ініціалів автора (шлюз, п.6; §12.5, п.3)."""
    document = _build_bibliography_document(
        [
            "3. Коваленко В. В. Формування професійної компетентності "
            "майбутніх фахівців. — Київ, 2005. 220 с."
        ]
    )

    entries = build_bibliography(document)
    entry = next(e for e in entries if e.ordinal == 3)

    assert entry.title is not None
    assert entry.title_confidence == Confidence.MEDIUM
    assert "Коваленко" not in entry.title
    assert "В." not in entry.title


# ---------------------------------------------------------------------------
# 7. Заглавие коротше 3 буквенних токенів — title is None.
# ---------------------------------------------------------------------------


def test_gate_07_a_title_shorter_than_three_letter_tokens_gives_none() -> None:
    """Кандидат у заглавие «Огляд права» (2 буквенних токени <
    `TITLE_MIN_LETTER_TOKENS` = 3) дає `title is None`, сам запис лишається
    (шлюз, п.7; «Відмови», title_out_of_bounds)."""
    document = _build_bibliography_document(
        ["4. Сидоренко А. А. Огляд права. — Одеса, 2011. 100 с."]
    )

    entries = build_bibliography(document)
    entry = next(e for e in entries if e.ordinal == 4)

    assert entry is not None
    assert entry.title is None


# ---------------------------------------------------------------------------
# 8. Заглавие довше 30 буквенних токенів — title is None.
# ---------------------------------------------------------------------------


def test_gate_08_a_title_longer_than_thirty_letter_tokens_gives_none() -> None:
    """Кандидат у заглавие з 31 буквенного токена (>
    `TITLE_MAX_LETTER_TOKENS` = 30) перед роздільником "/" дає
    `title is None` (шлюз, п.8; «Відмови», title_out_of_bounds)."""
    long_words = " ".join(["дослідження"] * 31)
    entry_text = f"5. Морозов Д. Д. {long_words} / Вісник науки. 2018. № 3. С. 45-50."
    document = _build_bibliography_document([entry_text])

    entries = build_bibliography(document)
    entry = next(e for e in entries if e.ordinal == 5)

    assert entry.title is None


# ---------------------------------------------------------------------------
# 9. surnames містить прізвище й не містить ініціалів.
# ---------------------------------------------------------------------------


def test_gate_09_surnames_contains_the_surname_but_not_the_initials() -> None:
    """`surnames` запису містить «Кравченко» і не містить ініціалів «Т.»/«П.»
    (шлюз, п.9)."""
    document = _build_bibliography_document(
        ["6. Кравченко Т. П. Дослідження соціальних процесів у регіоні. Харків, 2012. 130 с."]
    )

    entries = build_bibliography(document)
    entry = next(e for e in entries if e.ordinal == 6)

    assert "Кравченко" in entry.surnames
    assert not any(s in ("Т.", "П.", "Т", "П", "Т.П.") for s in entry.surnames)


# ---------------------------------------------------------------------------
# 10. year: коректний рік vs 1799/2101 — не рік.
# ---------------------------------------------------------------------------


def test_gate_10_year_is_extracted_and_1799_and_2101_are_rejected() -> None:
    """Рік у межах [1800, 2100] витягується; 1799 (< YEAR_MIN) і 2101
    (> YEAR_MAX) роком не стають — `year is None` (шлюз, п.10)."""
    document = _build_bibliography_document(
        [
            "7. Гриценко Л. Л. Праця з коректним роком. Київ, 2005. 100 с.",
            "8. Бондаренко М. М. Праця зі старим роком. Одеса, 1799. 50 с.",
            "9. Литвиненко О. О. Праця з майбутнім роком. Харків, 2101. 60 с.",
        ]
    )

    entries = build_bibliography(document)
    by_ordinal = {e.ordinal: e for e in entries}

    assert by_ordinal[7].year == 2005
    assert by_ordinal[8].year is None
    assert by_ordinal[9].year is None


# ---------------------------------------------------------------------------
# 11. Мова не визначається — UNKNOWN і фіксована заглушка для всіх записів.
# ---------------------------------------------------------------------------


def test_gate_11_language_is_unknown_with_the_fixed_placeholder_for_every_entry() -> None:
    """У кожного запису `language == Language.UNKNOWN` і
    `language_evidence == "not_evaluated_until_step_7"` (шлюз, п.11;
    «Контракт»: територія кроку 7)."""
    document = _build_bibliography_document(BIB_1_3)

    entries = build_bibliography(document)

    assert len(entries) == 3
    for entry in entries:
        assert entry.language == Language.UNKNOWN
        assert entry.language_evidence == "not_evaluated_until_step_7"


# ---------------------------------------------------------------------------
# 12. entry_id — 16 hex-символів, унікальний у межах документа.
# ---------------------------------------------------------------------------


def test_gate_12_entry_id_is_sixteen_hex_chars_and_unique_within_the_document() -> None:
    """`entry_id` кожного запису — рівно 16 hex-символів (`ID_HEX_LENGTH`),
    і всі три записи документа мають різні id (шлюз, п.12)."""
    document = _build_bibliography_document(BIB_1_3)

    entries = build_bibliography(document)

    assert len(entries) == 3
    ids = [e.entry_id for e in entries]
    for entry_id in ids:
        assert len(entry_id) == ID_HEX_LENGTH
        assert _HEX16_RE.match(entry_id)
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# 13. entry_id детермінований: два прогони дають той самий id.
# ---------------------------------------------------------------------------


def test_gate_13_entry_id_is_deterministic_across_two_calls() -> None:
    """Два виклики `build_bibliography` на тому самому документі дають
    однакові `entry_id` в тому самому порядку (шлюз, п.13)."""
    document = _build_bibliography_document(BIB_1_3)

    entries_1 = build_bibliography(document)
    entries_2 = build_bibliography(document)

    assert [e.entry_id for e in entries_1] == [e.entry_id for e in entries_2]
    assert entries_1 == entries_2


# ---------------------------------------------------------------------------
# 14. entry_id стійкий до зсуву координат (block_id/block_index).
# ---------------------------------------------------------------------------


def test_gate_14_entry_id_is_stable_across_a_block_identity_shift() -> None:
    """`entry_id` НЕ залежить від координат (§18.2): якщо той самий текст
    запису з тим самим `ordinal` опиняється в блоці з іншим `block_id` і
    `block_index` (симуляція пересклад блоків), `entry_id` не змінюється,
    хоча координати `.source` — так (шлюз, п.14)."""
    document = _build_bibliography_document(
        ["1. Іванов І. І. Перша праця. Київ, 2005. 100 с."]
    )
    entries_before = build_bibliography(document)
    entry_before = next(e for e in entries_before if e.ordinal == 1)

    target_block_id = entry_before.source.parts[0].block_id
    old_block = next(b for b in document.blocks if b.block_id == target_block_id)
    shifted_block = dataclasses.replace(
        old_block, block_id="blk-shifted-99999", block_index=old_block.block_index + 1000
    )
    new_blocks = tuple(
        shifted_block if b.block_id == target_block_id else b for b in document.blocks
    )
    shifted_document = dataclasses.replace(document, blocks=new_blocks)

    entries_after = build_bibliography(shifted_document)
    entry_after = next(e for e in entries_after if e.ordinal == 1)

    assert entry_after.source.parts[0].block_id == "blk-shifted-99999"
    assert entry_after.source.parts[0].block_id != entry_before.source.parts[0].block_id
    assert entry_after.entry_id == entry_before.entry_id


# ---------------------------------------------------------------------------
# 15. citation_id — 16 hex, унікальний у межах документа.
# ---------------------------------------------------------------------------


def test_gate_15_citation_id_is_sixteen_hex_chars_and_unique_within_the_document() -> None:
    """Два незалежних посилання `[1]` і `[2]` дають дві `CitationMention` з
    різними `citation_id`, кожен — 16 hex-символів (шлюз, п.15)."""
    document = _build_citation_document(
        "Перше твердження підтверджується джерелом [1]. "
        "Друге твердження підтверджується іншим джерелом [2].",
        BIB_1_3,
    )

    entries = build_bibliography(document)
    citations = build_citations(document, entries)

    assert len(citations) >= 2
    ids = [c.citation_id for c in citations]
    for cid in ids:
        assert len(cid) == ID_HEX_LENGTH
        assert _HEX16_RE.match(cid)
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# 16. [2] у тому ж реченні, що й донор, дає HIGH numeric.
# ---------------------------------------------------------------------------


def test_gate_16_a_numeric_reference_in_the_same_sentence_as_the_donor_gives_high() -> None:
    """Одне речення з авторським текстом і посиланням `[2]` разом дає
    `CitationMention` з `kind == "numeric"`, `confidence == HIGH`, чий
    `entry_ids` містить `entry_id` другого запису (шлюз, п.16; §12.6, п.1)."""
    document = _build_citation_document(
        "Дане положення прямо підтверджується в роботі автора [2].", BIB_1_3
    )

    entries = build_bibliography(document)
    entry2 = next(e for e in entries if e.ordinal == 2)
    citations = build_citations(document, entries)

    hits = [
        c for c in citations
        if c.kind == "numeric" and c.confidence == Confidence.HIGH and entry2.entry_id in c.entry_ids
    ]
    assert hits


# ---------------------------------------------------------------------------
# 17. [2] у тому ж абзаці, іншому реченні, без проміжного — HIGH.
# ---------------------------------------------------------------------------


def test_gate_17_a_numeric_reference_in_a_later_sentence_of_the_same_paragraph_gives_high() -> None:
    """Абзац із двох речень: перше — без посилання, друге — з `[2]`; між
    ними немає жодного іншого речення, тож звʼязок все одно `HIGH`
    (шлюз, п.17; §12.6, п.2)."""
    document = _build_citation_document(
        "Проблема широко висвітлена в науковій літературі. "
        "Джерело [2] це підтверджує.",
        BIB_1_3,
    )

    entries = build_bibliography(document)
    entry2 = next(e for e in entries if e.ordinal == 2)
    citations = build_citations(document, entries)

    hits = [
        c for c in citations
        if c.kind == "numeric" and c.confidence == Confidence.HIGH and entry2.entry_id in c.entry_ids
    ]
    assert hits


# ---------------------------------------------------------------------------
# 18. Проміжне речення з власним посиланням блокує звʼязок з донором.
# ---------------------------------------------------------------------------


def test_gate_18_an_intervening_sentence_with_its_own_reference_reduces_the_link_count() -> None:
    """Якщо між реченням-донором (без посилання) і реченням з `[2]` в тому
    ж абзаці зʼявляється ЩЕ ОДНЕ завершене речення зі своїм посиланням `[7]`
    (§12.6, п.2, `SAME_PARAGRAPH_MAX_INTERVENING` = 0), кількість
    HIGH-звʼязків з другим записом строго менша, ніж у документі без такого
    проміжного речення — параграф-рівнева прив'язка донора блокується,
    хоча власне речення з `[2]` (маючи власний авторський текст) і без того
    дає окрему HIGH-згадку в обох документах (шлюз, п.18)."""
    bib_with_seven = BIB_1_3 + ["7. Захарченко З. З. Сьома праця. Суми, 2007. 70 с."]

    control_document = _build_citation_document(
        "Проблема широко висвітлена в науковій літературі. "
        "Джерело [2] це підтверджує.",
        bib_with_seven,
    )
    test_document = _build_citation_document(
        "Проблема широко висвітлена в науковій літературі. "
        "Інше джерело це підтверджує [7]. "
        "Джерело [2] це підтверджує.",
        bib_with_seven,
    )

    control_entries = build_bibliography(control_document)
    control_entry2 = next(e for e in control_entries if e.ordinal == 2)
    control_citations = build_citations(control_document, control_entries)
    control_mention = next(
        c for c in control_citations
        if c.kind == "numeric" and c.confidence == Confidence.HIGH
        and control_entry2.entry_id in c.entry_ids
    )
    control_count = len(donor_ids_for_mention(control_document, control_mention))

    test_entries = build_bibliography(test_document)
    test_entry2 = next(e for e in test_entries if e.ordinal == 2)
    test_citations = build_citations(test_document, test_entries)
    test_mention = next(
        c for c in test_citations
        if c.kind == "numeric" and c.confidence == Confidence.HIGH
        and test_entry2.entry_id in c.entry_ids
    )
    test_count = len(donor_ids_for_mention(test_document, test_mention))

    assert control_count >= 1
    assert test_count < control_count


# ---------------------------------------------------------------------------
# 19. Діапазон [5-7] дає три entry_ids через expand_bracket.
# ---------------------------------------------------------------------------


def test_gate_19_a_dash_range_reference_resolves_to_three_entry_ids() -> None:
    """Посилання `[5-7]` дає `entry_ids`, що містить рівно записи 5, 6 і 7
    (через `parser.citations.expand_bracket`, не власну реалізацію діапазонів)
    (шлюз, п.19)."""
    document = _build_citation_document(
        "Це підтверджується декількома дослідженнями [5-7].",
        [
            "5. Автор А. А. Пʼята праця. Київ, 2001. 80 с.",
            "6. Автор Б. Б. Шоста праця. Львів, 2002. 85 с.",
            "7. Автор В. В. Сьома праця. Одеса, 2003. 90 с.",
        ],
    )

    entries = build_bibliography(document)
    by_ordinal = {e.ordinal: e for e in entries}
    citations = build_citations(document, entries)

    expected_ids = {by_ordinal[5].entry_id, by_ordinal[6].entry_id, by_ordinal[7].entry_id}
    hits = [c for c in citations if c.kind == "numeric" and expected_ids.issubset(set(c.entry_ids))]
    assert hits
    assert set(hits[0].entry_ids) == expected_ids


# ---------------------------------------------------------------------------
# 20. Посилання на номер поза розібраною бібліографією — без CitationMention.
# ---------------------------------------------------------------------------


def test_gate_20_a_reference_to_an_ordinal_outside_the_bibliography_gives_no_mention() -> None:
    """Єдине посилання документа `[9]` не резолвиться, оскільки в
    бібліографії лише записи 1-3 — `CitationMention` не будується (шлюз,
    п.20; «Відмови», unresolved_source_number)."""
    document = _build_citation_document(
        "Ця думка вже висловлювалась раніше [9].", BIB_1_3
    )

    entries = build_bibliography(document)
    citations = build_citations(document, entries)

    assert citations == ()


# ---------------------------------------------------------------------------
# 21. Номер більший за MAX_SOURCE_NUM ссылкою не вважається.
# ---------------------------------------------------------------------------


def test_gate_21_a_number_above_max_source_num_is_not_treated_as_a_reference() -> None:
    """Єдине число в дужках документа `[1500]` (> `MAX_SOURCE_NUM` = 999) не
    вважається номером джерела — `CitationMention` не будується (шлюз, п.21;
    «Відмови», source_number_too_large)."""
    document = _build_citation_document(
        "Джерело трактує це по-іншому [1500].", BIB_1_3
    )

    entries = build_bibliography(document)
    citations = build_citations(document, entries)

    assert citations == ()


# ---------------------------------------------------------------------------
# 22. Просте сусідство блоків недостатнє.
# ---------------------------------------------------------------------------


def test_gate_22_plain_block_adjacency_alone_does_not_create_a_citation_mention() -> None:
    """Донор в одному блоці, `[2]` — в наступному (геометрично відокремленому
    великим зазором) блоці без інших ознак звʼязку: `CitationMention` для
    цього донора не будується (шлюз, п.22; «Відмови», block_adjacency_only)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, "<p>РОЗДІЛ 1</p>", rect=fitz.Rect(72, 72, PAGE_WIDTH - 72, 110))
    _insert_html(
        page,
        "<p>Проблема широко висвітлена в науковій літературі.</p>",
        rect=fitz.Rect(72, 140, PAGE_WIDTH - 72, 220),
    )
    _insert_html(page, "<p>[2]</p>", rect=fitz.Rect(72, 650, PAGE_WIDTH - 72, 700))
    page2 = _new_page(doc)
    bib_html = "<p>СПИСОК ЛІТЕРАТУРИ</p>" + "".join(f"<p>{e}</p>" for e in BIB_1_3)
    _insert_html(page2, bib_html)
    pdf_bytes = _finish(doc)
    document = parse_search_document(pdf_bytes)

    entries = build_bibliography(document)
    entry2 = next(e for e in entries if e.ordinal == 2)
    citations = build_citations(document, entries)

    assert not any(entry2.entry_id in c.entry_ids for c in citations)


# ---------------------------------------------------------------------------
# 23. Унікальне прізвище дає MEDIUM surname.
# ---------------------------------------------------------------------------


def test_gate_23_a_unique_surname_present_in_the_donor_gives_medium_surname_confidence() -> None:
    """Прізвище «Соколов», унікальне в бібліографії й присутнє в авторському
    реченні без жодного числового посилання, дає `CitationMention` з
    `kind == "surname"`, `confidence == MEDIUM` (шлюз, п.23; §12.6, п.4)."""
    document = _build_citation_document(
        "У дослідженні Соколов доводить наступне твердження.",
        [
            "1. Соколов І. І. Перша праця. Київ, 2010. 100 с.",
            "2. Петров П. П. Друга праця. Львів, 2012. 90 с.",
            "3. Іванов І. І. Третя праця. Одеса, 2015. 80 с.",
        ],
    )

    entries = build_bibliography(document)
    entry1 = next(e for e in entries if e.ordinal == 1)
    citations = build_citations(document, entries)

    hits = [
        c for c in citations
        if c.kind == "surname" and c.confidence == Confidence.MEDIUM and entry1.entry_id in c.entry_ids
    ]
    assert hits


# ---------------------------------------------------------------------------
# 24. Неунікальне прізвище звʼязку не дає.
# ---------------------------------------------------------------------------


def test_gate_24_a_surname_repeated_in_the_bibliography_gives_no_surname_link() -> None:
    """Прізвище «Соколов» трапляється у бібліографії двічі (різні записи,
    різні ініціали) — звʼязок по прізвищу не будується взагалі (шлюз, п.24;
    «Відмови», surname_not_unique)."""
    document = _build_citation_document(
        "У дослідженні Соколов доводить наступне твердження.",
        [
            "1. Соколов А. А. Перша праця. Одеса, 2003. 90 с.",
            "2. Соколов Б. Б. Друга праця. Харків, 2008. 95 с.",
            "3. Петров П. П. Третя праця. Львів, 2011. 85 с.",
        ],
    )

    entries = build_bibliography(document)
    citations = build_citations(document, entries)

    assert not any(c.kind == "surname" for c in citations)


# ---------------------------------------------------------------------------
# 25. Мʼякий перенос усередині номера ссылки: координати ведуть на raw.
# ---------------------------------------------------------------------------


def test_gate_25_a_soft_hyphen_inside_a_reference_number_maps_back_to_raw_characters() -> None:
    """У блоці з мʼяким переносом (U+00AD) усередині номера `[1­2]`
    `source` цитати вказує на РЕАЛЬНІ вихідні символи (разом з мʼяким
    переносом), а не на нормалізовані зміщення, застосовані напряму до
    `raw_text` (заборонено пакетом) (шлюз, п.25; §12.6)."""
    document = _build_citation_document(
        "Проблема доведена в роботі [12].",
        [
            "1. Іванов І. І. Перша праця. Київ, 2005. 100 с.",
            "12. Петров П. П. Дванадцята праця. Львів, 2012. 90 с.",
        ],
    )

    target_block = next(b for b in document.blocks if "[12]" in b.raw_text)
    assert len(target_block.zone_spans) == 1, "тест розрахований на блок з одним zone_span"

    bracket_index = target_block.raw_text.index("[12]")
    insert_at = bracket_index + 2  # позиція між "1" і "2" номера посилання
    new_raw = target_block.raw_text[:insert_at] + "­" + target_block.raw_text[insert_at:]
    new_normalized = normalize_text(new_raw)
    new_tokens = tokenize(new_raw, new_normalized)

    def _shift(offset: int) -> int:
        return offset + 1 if offset > insert_at else offset

    old_span = target_block.zone_spans[0]
    new_zone_spans = (
        dataclasses.replace(
            old_span,
            raw_start=_shift(old_span.raw_start),
            raw_end=_shift(old_span.raw_end),
        ),
    )
    new_block = dataclasses.replace(
        target_block,
        raw_text=new_raw,
        normalized=new_normalized,
        tokens=new_tokens,
        zone_spans=new_zone_spans,
    )
    new_blocks = tuple(
        new_block if b.block_id == target_block.block_id else b for b in document.blocks
    )
    shifted_document = dataclasses.replace(document, blocks=new_blocks)

    entries = build_bibliography(shifted_document)
    entry12 = next(e for e in entries if e.ordinal == 12)
    citations = build_citations(shifted_document, entries)

    numeric_hits = [
        c for c in citations if c.kind == "numeric" and entry12.entry_id in c.entry_ids
    ]
    assert numeric_hits, "цитата [1­2] з мʼяким переносом усередині номера не розпізнана"

    reconstructed = _reconstruct(shifted_document, numeric_hits[0].source)
    assert reconstructed == "[1­2]"


# ---------------------------------------------------------------------------
# 26. parse_search_document наповнює bibliography і citations.
# ---------------------------------------------------------------------------


def test_gate_26_parse_search_document_fills_bibliography_and_citations() -> None:
    """На документі з бібліографією й посиланням у тілі `document.bibliography`
    і `document.citations`, що повертає `parse_search_document`, більше не
    порожні (шлюз, п.26)."""
    pdf_bytes = _make_citation_pdf_bytes(
        "Дане положення підтверджується джерелом [1].", BIB_1_3
    )
    document = parse_search_document(pdf_bytes)

    assert len(document.bibliography) >= 1
    assert len(document.citations) >= 1


# ---------------------------------------------------------------------------
# 27. Документ без бібліографії дає порожні кортежі й не падає.
# ---------------------------------------------------------------------------


def test_gate_27_a_document_without_a_bibliography_still_gives_empty_tuples() -> None:
    """`parse_search_document` на документі без списку літератури дає
    `bibliography == ()` і `citations == ()`, без винятку (шлюз, п.27)."""
    doc = fitz.open()
    page = _new_page(doc)
    filler = (
        "Дисертаційне дослідження присвячене актуальній проблемі сучасної "
        "науки та її практичному застосуванню в певній галузі знань."
    )
    _insert_html(page, f"<p>ВСТУП</p><p>{filler}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert document.bibliography == ()
    assert document.citations == ()


# ---------------------------------------------------------------------------
# Спільна фікстура для пунктів 28-31: дев'ять реальних PDF корпусу.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus_documents():
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        payload = json.load(f)
    docs = {}
    for entry in payload["documents"]:
        path = EXAMPLES_DIR / entry["file"]
        pdf_bytes = path.read_bytes()
        document = parse_search_document(pdf_bytes)
        docs[entry["file"]] = (entry, pdf_bytes, document)
    return docs


# ---------------------------------------------------------------------------
# 28. Дев'ять PDF: build_bibliography відпрацьовує без винятку.
# ---------------------------------------------------------------------------


def test_gate_28_build_bibliography_runs_without_exception_on_all_nine_corpus_pdfs(
    corpus_documents,
) -> None:
    """`build_bibliography` не піднімає винятку на жодному з девʼяти реальних
    PDF корпусу (шлюз, п.28)."""
    for fname, (_entry, _pdf_bytes, document) in corpus_documents.items():
        build_bibliography(document)  # не повинно кидати виняток; fname у AssertionError нижче не потрібен


# ---------------------------------------------------------------------------
# 29. Дев'ять PDF: зібрано >=80% від спостереженої кількості записів.
# ---------------------------------------------------------------------------


def test_gate_29_collected_entry_count_is_at_least_eighty_percent_of_the_observed_count(
    corpus_documents,
) -> None:
    """Для документів з `bibliography.status == "filled"` число зібраних
    `BibliographyEntry` не менше 80% від `bibliography_entry_count_observed`
    фікстури (шлюз, п.29)."""
    for fname, (entry, _pdf_bytes, document) in corpus_documents.items():
        bib = entry["sections"]["bibliography"]
        if bib["status"] != "filled":
            continue
        entries = build_bibliography(document)
        observed = bib["bibliography_entry_count_observed"]
        assert len(entries) >= 0.8 * observed, fname


# ---------------------------------------------------------------------------
# 30. Дев'ять PDF: citation_example знайдено, entry_id детермінований.
# ---------------------------------------------------------------------------


def test_gate_30_citation_example_ordinal_is_found_and_its_entry_id_is_deterministic(
    corpus_documents,
) -> None:
    """Запис з номером `citation_example.source_ordinal` фікстури знайдений
    у зібраній бібліографії, і його `entry_id` однаковий між двома
    незалежними розборами того самого PDF (шлюз, п.30)."""
    for fname, (entry, pdf_bytes, document) in corpus_documents.items():
        bib = entry["sections"]["bibliography"]
        if bib["status"] != "filled":
            continue
        ce = bib.get("citation_example")
        if ce is None:
            continue

        entries_1 = build_bibliography(document)
        match_1 = next((e for e in entries_1 if e.ordinal == ce["source_ordinal"]), None)
        assert match_1 is not None, fname

        document_2 = parse_search_document(pdf_bytes)
        entries_2 = build_bibliography(document_2)
        match_2 = next((e for e in entries_2 if e.ordinal == ce["source_ordinal"]), None)
        assert match_2 is not None, fname

        assert match_1.entry_id == match_2.entry_id, fname


# ---------------------------------------------------------------------------
# 31. Дев'ять PDF: записи не потрапляють у тіло роботи.
# ---------------------------------------------------------------------------


def test_gate_31_no_bibliography_entry_source_points_into_a_content_section(
    corpus_documents,
) -> None:
    """Ні в одного запису жодного з девʼяти PDF `source` не вказує на блок
    розділу з `CONTENT_SECTION_KINDS` (INTRO/CHAPTER/CONCLUSIONS) (шлюз,
    п.31)."""
    for fname, (_entry, _pdf_bytes, document) in corpus_documents.items():
        entries = build_bibliography(document)
        kind_by_section_id = {s.section_id: s.kind for s in document.sections}
        section_by_block_id = {b.block_id: b.section_id for b in document.blocks}
        for e in entries:
            for part in e.source.parts:
                section_id = section_by_block_id.get(part.block_id)
                kind = kind_by_section_id.get(section_id)
                assert kind not in CONTENT_SECTION_KINDS, (fname, e.entry_id, kind)


# ---------------------------------------------------------------------------
# 32. Детермінізм: два розбори тих самих байтів дають рівні результати.
# ---------------------------------------------------------------------------


def test_gate_32_two_parses_of_the_same_bytes_give_equal_bibliography_and_citations_in_order() -> None:
    """Два виклики `parse_search_document` на тих самих байтах PDF дають
    рівні `bibliography` і `citations` у тому самому порядку (шлюз, п.32)."""
    pdf_bytes = _make_citation_pdf_bytes(
        "Дане положення підтверджується джерелом [2].", BIB_1_3
    )

    document_1 = parse_search_document(pdf_bytes)
    document_2 = parse_search_document(pdf_bytes)

    assert document_1.bibliography == document_2.bibliography
    assert document_1.citations == document_2.citations
