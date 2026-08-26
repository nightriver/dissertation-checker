"""
Шлюз кроку 5 — `parser/searchdoc.py`: блоки, колонки, колонтитули, зони,
розділи, охоплення (`steps/step-05.md`).

Пишеться незалежно від реалізації, лише за контрактом і числами пакета.
Реалізація `parser/searchdoc.py` вже лежить на диску (паралельний виконавець),
але цей файл її НЕ читає і НЕ відкриває — очікувані значення виведені з
тексту пакета (розділи «Контракт», «Числа», «Розділи: шаблони заголовків»,
«Охоплення», «Стани сторінки», «Шлюз»), а не зі спостереженого виводу.

Синтетичні PDF збираються прямо в тестах через `fitz`:
- геометричні тести (склейка/розрив рядків, колонки, колонтитули, зноски)
  використовують латиницю через `page.insert_text(..., fontname="helv")` —
  мова вмісту тут не важлива, важлива лише геометрія;
- тести шаблонів заголовків і зон (кирилиця) використовують
  `page.insert_htmlbox(...)` — базовий шрифт Helvetica не має кириличних
  гліфів і дає "�" при екстракції тексту (підтверджено емпірично і тим самим
  прийомом, що вже використаний у `tests/test_search_thin_slice_integration.py`).

Нумерація тестів `test_gate_NN_*` відповідає пунктам розділу «Шлюз» пакета
`steps/step-05.md`. Пункти розділу «Відмови» пакета вже покриті цими самими
45 пунктами (перевірено відповідність рядок-у-рядок), окремих `test_reject_*`
тут немає.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from parser.searchdoc import NoTextLayerError, PARSER_VERSION, parse_search_document
from search.types import (
    CONTENT_SECTION_KINDS,
    Confidence,
    PageTextState,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
    TextZone,
)

PREVIOUS_PARSER_VERSION = "searchdoc-parser-2026-08-25"  # контракт кроку 5

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_corpus_expectations.json"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

# Стандартний прямокутник для insert_htmlbox — з великим запасом на висоту.
_HTML_RECT = fitz.Rect(72, 72, PAGE_WIDTH - 72, PAGE_HEIGHT - 72)


# ---------------------------------------------------------------------------
# Допоміжні будівники синтетичних PDF
# ---------------------------------------------------------------------------


def _new_page(doc: "fitz.Document"):
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _finish(doc: "fitz.Document") -> bytes:
    data = doc.tobytes()
    doc.close()
    return data


def _insert_html(page, html: str, rect: "fitz.Rect | None" = None) -> None:
    page.insert_htmlbox(rect or _HTML_RECT, html)


def _raster_png_bytes(width: int = 100, height: int = 100, color=(180, 180, 180)) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


def _insert_half_page_raster(page, ratio: float = 0.55) -> None:
    """Растр площею ratio * площа листа (>=0.5 з запасом — §5.4)."""
    rect = fitz.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT * ratio)
    page.insert_image(rect, stream=_raster_png_bytes())


def _cyrillic_filler(min_chars: int) -> str:
    """Кириличний наповнювач довжиною не менше min_chars символів."""
    words = [
        "Слово", "текст", "аналіз", "дослідження", "матеріал",
        "приклад", "речення", "частина", "робота", "автор",
    ]
    out: list[str] = []
    total = 0
    i = 0
    while total < min_chars:
        w = words[i % len(words)]
        out.append(w)
        total += len(w) + 1
        i += 1
    return " ".join(out) + "."


def _block_by_exact_text(document, text: str):
    for b in document.blocks:
        if b.raw_text.strip() == text:
            return b
    return None


def _blocks_containing(document, needle: str):
    return [b for b in document.blocks if needle in b.raw_text]


def _sections_of_kind(document, kind):
    return [s for s in document.sections if s.kind == kind]


def _heading_block(document, section):
    """Блок, що починає розділ — перший блок діапазону section.block_start.

    Виведено з контракту: SectionOverride.heading_block_id адресує саме той
    блок, з якого розділ починається; SectionInfo.block_start — індекс
    (block_index) цього блоку.
    """
    for b in document.blocks:
        if b.block_index == section.block_start:
            return b
    raise AssertionError(f"не знайдено блок з block_index == {section.block_start}")


# ---------------------------------------------------------------------------
# Повторно використовувані фрагменти кирилиці
# ---------------------------------------------------------------------------

INTRO_PARA = "Вступна частина роботи обґрунтовує актуальність теми та формулює мету дослідження."
CHAPTER_PARA = "Перший розділ роботи розкриває теоретичні засади предмета дослідження та основні поняття."
CONCLUSIONS_PARA = "Загальні висновки узагальнюють результати проведеного дослідження та окреслюють перспективи."
ABSTRACT_PARA = "Короткий виклад основного змісту роботи для попереднього ознайомлення читача з результатами."
APPENDIX_PARA = "Додаткові матеріали містять таблиці та схеми, що ілюструють основні результати дослідження."
TOC_PARA = "Перелік розділів та підрозділів роботи наведено нижче для зручності навігації."
TITLE_PARA_1 = "Дисертація подається на здобуття наукового ступеня кандидата наук."
TITLE_PARA_2 = "Тема дослідження та її актуальність розкриваються у вступній частині."
UNKNOWN_PARA = (
    "Цей текст розташований на третій сторінці, до першого розпізнаного "
    "заголовка, і тому вважається нерозпізнаним фрагментом документа."
)
BIBLIO_ENTRY_LINE = "1. Іванов І. І. Назва наукової праці. Київ, 2020. 200 с."
QUOTED_SENTENCE = "Автор стверджує: «цитата з джерела», і далі продовжує думку."


# ---------------------------------------------------------------------------
# 1. Склейка рядків: три рядки нормального інтервалу — один блок.
# ---------------------------------------------------------------------------


def test_gate_01_three_normally_spaced_lines_merge_into_one_block() -> None:
    """Абзац із трьох рядків зі звичайним міжрядковим інтервалом (пітч 14pt
    при кеглі 11) дає рівно один SearchBlock, чий raw_text містить усі три
    рядки (§5.2, п.1-3)."""
    doc = fitz.open()
    page = _new_page(doc)
    page.insert_text((72, 100), "Line one of the paragraph text.", fontsize=11, fontname="helv")
    page.insert_text((72, 114), "continues here with more words.", fontsize=11, fontname="helv")
    page.insert_text((72, 128), "and finally ends the paragraph.", fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.blocks) == 1
    raw = document.blocks[0].raw_text
    assert "Line one of the paragraph text." in raw
    assert "continues here with more words." in raw
    assert "and finally ends the paragraph." in raw


# ---------------------------------------------------------------------------
# 2. Розрив за інтервалом: зазор більший за 1,4 x медіанної висоти рядка.
# ---------------------------------------------------------------------------


def test_gate_02_a_large_vertical_gap_splits_two_lines_into_two_blocks() -> None:
    """Два рядки того самого кеглю, розділені зазором ~170pt (при висоті
    рядка ~15pt для кеглю 11 — набагато більше за 1,4x = ~21pt), дають два
    блоки (§5.2, п.4; LINE_MERGE_GAP_FACTOR = 1.4)."""
    doc = fitz.open()
    page = _new_page(doc)
    page.insert_text((72, 100), "First paragraph line for the gap test.", fontsize=11, fontname="helv")
    page.insert_text((72, 300), "Second paragraph line far below.", fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.blocks) == 2
    assert all(b.physical_page == 1 for b in document.blocks)


# ---------------------------------------------------------------------------
# 3. Розрив за кеглем: різниця кеглю більша за 25 %.
# ---------------------------------------------------------------------------


def test_gate_03_a_large_font_size_change_splits_two_lines_into_two_blocks() -> None:
    """Рядок кеглем 11 і сусідній рядок кеглем 20 (різниця 81,8 % >> 25 %),
    розташовані з мінімальним (навіть від'ємним — bbox перекриваються)
    вертикальним зазором, все одно дають два блоки — розрив спричинений саме
    зміною кеглю, а не зазором (§5.2, п.4; FONT_SIZE_BREAK_RATIO = 0.25)."""
    doc = fitz.open()
    page = _new_page(doc)
    page.insert_text((72, 100), "Small font line.", fontsize=11, fontname="helv")
    page.insert_text((72, 116), "BIG FONT LINE HERE", fontsize=20, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.blocks) == 2


# ---------------------------------------------------------------------------
# 4. Крапка сама по собі не розриває блок.
# ---------------------------------------------------------------------------


def test_gate_04_period_at_end_of_line_does_not_split_the_block() -> None:
    """Перший рядок закінчується крапкою, другий починається з великої
    літери, обидва — звичайний інтервал: залишаються одним блоком (§5.2,
    п.4: "Конец предложения сам по себе блок не разрывает")."""
    doc = fitz.open()
    page = _new_page(doc)
    page.insert_text((72, 100), "First sentence ends here.", fontsize=11, fontname="helv")
    page.insert_text((72, 114), "Second sentence continues normally.", fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.blocks) == 1
    raw = document.blocks[0].raw_text
    assert "First sentence ends here." in raw
    assert "Second sentence continues normally." in raw


# ---------------------------------------------------------------------------
# 5. Короткий заголовок зберігається як блок.
# ---------------------------------------------------------------------------


def test_gate_05_short_heading_line_is_kept_as_a_block() -> None:
    """Рядок "ВСТУП" сам по собі — короткий блок, який не відкидається за
    довжиною і розпізнається як заголовок розділу INTRO (§5.2, п.6)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, "<p>ВСТУП</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    heading = _block_by_exact_text(document, "ВСТУП")
    assert heading is not None
    assert any(s.kind == SectionKind.INTRO for s in document.sections)


# ---------------------------------------------------------------------------
# 6. Дві колонки: спершу вся ліва зверху вниз, потім уся права.
# ---------------------------------------------------------------------------


def test_gate_06_two_columns_are_read_left_column_first_then_right_column() -> None:
    """Ліва колонка (x0=72) і права (x0=350) розділені зазором ~278pt, що
    набагато більше за 0,25 x ширини листа (~149pt); блоки читаються
    "спочатку вся ліва колонка зверху вниз, потім уся права" — block_index
    лівих блоків менший за block_index правих (§5.2, п.5)."""
    doc = fitz.open()
    page = _new_page(doc)
    for y in (100, 114, 128):
        page.insert_text((72, y), "LEFTMARK column line of text.", fontsize=11, fontname="helv")
    for y in (100, 114, 128):
        page.insert_text((350, y), "RIGHTMARK column line of text.", fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    left_blocks = _blocks_containing(document, "LEFTMARK")
    right_blocks = _blocks_containing(document, "RIGHTMARK")
    assert left_blocks and right_blocks
    assert max(b.block_index for b in left_blocks) < min(b.block_index for b in right_blocks)


# ---------------------------------------------------------------------------
# 7. Через границю листа абзац не склеюється.
# ---------------------------------------------------------------------------


def test_gate_07_text_across_a_page_boundary_is_not_merged_into_one_block() -> None:
    """Текст, обірваний на кінці листа 1 і продовжений на листі 2, дає два
    блоки з різними physical_page (§5.2, п.7)."""
    doc = fitz.open()
    page1 = _new_page(doc)
    page1.insert_text((72, 750), "This sentence begins on the first physical page and",
                       fontsize=11, fontname="helv")
    page2 = _new_page(doc)
    page2.insert_text((72, 100), "continues here on the second physical page of the file.",
                       fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    block_a = next(b for b in document.blocks if "begins on the first physical page" in b.raw_text)
    block_b = next(b for b in document.blocks if "continues here on the second physical page" in b.raw_text)
    assert block_a.block_id != block_b.block_id
    assert block_a.physical_page == 1
    assert block_b.physical_page == 2


# ---------------------------------------------------------------------------
# 8. Колонтитул: однаковий верхній рядок на п'яти сторінках.
# ---------------------------------------------------------------------------


def test_gate_08_a_repeating_top_line_on_five_pages_is_marked_header_footer() -> None:
    """Документ із п'яти сторінок з однаковим верхнім рядком на кожній:
    цей рядок не входить у жоден блок із зоною AUTHOR_TEXT, а позначений
    HEADER_FOOTER (§5.3; HEADER_FOOTER_BAND=0.12, HEADER_FOOTER_MIN_PAGE_RATIO=0.60,
    HEADER_FOOTER_MIN_PAGES=3)."""
    doc = fitz.open()
    for _ in range(5):
        page = _new_page(doc)
        page.insert_text((72, 40), "HEADERLINE", fontsize=11, fontname="helv")
        _insert_html(page, f"<p>{_cyrillic_filler(250)}</p>", rect=fitz.Rect(72, 90, PAGE_WIDTH - 72, PAGE_HEIGHT - 72))
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    header_blocks = [b for b in document.blocks if b.raw_text.strip() == "HEADERLINE"]
    assert len(header_blocks) == 5
    for b in header_blocks:
        assert b.zone_spans
        assert all(zs.zone == TextZone.HEADER_FOOTER for zs in b.zone_spans)


# ---------------------------------------------------------------------------
# 9. Колонтитул з номером: цифри нормалізуються до placeholder.
# ---------------------------------------------------------------------------


def test_gate_09_a_repeating_bottom_page_number_is_recognized_as_one_header_footer() -> None:
    """Нижній рядок вигляду "10", "11", ... на п'яти різних сторінках
    розпізнається як один і той самий колонтитул (номери нормалізуються до
    placeholder) — усі блоки-номери отримують зону HEADER_FOOTER (§5.3)."""
    doc = fitz.open()
    for i, number in enumerate(("10", "11", "12", "13", "14")):
        page = _new_page(doc)
        _insert_html(page, f"<p>{_cyrillic_filler(250)}</p>", rect=fitz.Rect(72, 72, PAGE_WIDTH - 72, PAGE_HEIGHT * 0.7))
        page.insert_text((72, 800), number, fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    number_blocks = [b for b in document.blocks if b.raw_text.strip() in ("10", "11", "12", "13", "14")]
    assert len(number_blocks) == 5
    for b in number_blocks:
        assert b.zone_spans
        assert all(zs.zone == TextZone.HEADER_FOOTER for zs in b.zone_spans)


# ---------------------------------------------------------------------------
# 10. Поріг 60 %: рядок на 2 з 5 сторінок колонтитулом не вважається.
# ---------------------------------------------------------------------------


def test_gate_10_a_line_repeating_on_only_two_of_five_pages_stays_author_text() -> None:
    """Рядок, що зустрічається лише на 2 з 5 сторінок (40 % < 60 %),
    колонтитулом не вважається і лишається в авторському тексті (§5.3,
    HEADER_FOOTER_MIN_PAGE_RATIO = 0.60)."""
    doc = fitz.open()
    for i in range(5):
        page = _new_page(doc)
        if i < 2:
            page.insert_text((72, 40), "REPEATLINE", fontsize=11, fontname="helv")
        _insert_html(page, f"<p>{_cyrillic_filler(250)}</p>", rect=fitz.Rect(72, 90, PAGE_WIDTH - 72, PAGE_HEIGHT - 72))
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    marker_blocks = [b for b in document.blocks if b.raw_text.strip() == "REPEATLINE"]
    assert len(marker_blocks) == 2
    for b in marker_blocks:
        assert b.zone_spans
        assert all(zs.zone == TextZone.AUTHOR_TEXT for zs in b.zone_spans)


# ---------------------------------------------------------------------------
# 11. Менше трьох підхожих сторінок — колонтитули не шукаються.
# ---------------------------------------------------------------------------


def test_gate_11_fewer_than_three_suitable_pages_disables_header_footer_search() -> None:
    """Двосторінковий документ з однаковим верхнім рядком на обох сторінках:
    оскільки підхожих сторінок менше трьох, колонтитули не шукаються взагалі
    — жоден блок не позначений HEADER_FOOTER (§5.3; HEADER_FOOTER_MIN_PAGES=3,
    рішення оркестратора)."""
    doc = fitz.open()
    for _ in range(2):
        page = _new_page(doc)
        page.insert_text((72, 40), "HDR", fontsize=11, fontname="helv")
        _insert_html(page, f"<p>{_cyrillic_filler(250)}</p>", rect=fitz.Rect(72, 90, PAGE_WIDTH - 72, PAGE_HEIGHT - 72))
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert not any(zs.zone == TextZone.HEADER_FOOTER for b in document.blocks for zs in b.zone_spans)


# ---------------------------------------------------------------------------
# 12. Стани сторінок: 0, 50, 400 змістовних символів.
# ---------------------------------------------------------------------------


def test_gate_12_pages_with_zero_low_and_full_content_get_the_right_states() -> None:
    """Три сторінки з 0, ~60 і ~320 змістовними символами дають NO_TEXT,
    LOW_TEXT, TEXT_OK відповідно; у перших двох reason непорожній (§5.4;
    TEXT_OK_MIN_CHARS=200)."""
    # Навмисно >8 слів, щоб не потрапити під правило коротких блоків
    # (SHORT_BLOCK_MAX_WORDS=8, §5.4) і не стати EXPECTED_SPARSE замість LOW_TEXT.
    low_text_para = (
        "Це речення навмисно містить значно більше ніж вісім слів, "
        "щоб не потрапити під правило коротких блоків без растру."
    )
    assert len(low_text_para.split()) > 8
    assert 1 <= len(low_text_para) < 200

    doc = fitz.open()
    _new_page(doc)  # порожня сторінка — NO_TEXT
    page2 = _new_page(doc)
    _insert_html(page2, f"<p>{low_text_para}</p>")
    page3 = _new_page(doc)
    _insert_html(page3, f"<p>{_cyrillic_filler(320)}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.pages) == 3
    p1, p2, p3 = document.pages
    assert p1.state == PageTextState.NO_TEXT
    assert p1.reason != ""
    assert p2.state == PageTextState.LOW_TEXT
    assert p2.reason != ""
    assert p3.state == PageTextState.TEXT_OK


# ---------------------------------------------------------------------------
# 13. EXPECTED_SPARSE замість LOW_TEXT через растр.
# ---------------------------------------------------------------------------


def test_gate_13_large_raster_with_a_short_caption_gives_expected_sparse() -> None:
    """Лист із зображенням на ~55 % площі й короткою підписом отримує
    EXPECTED_SPARSE, а не LOW_TEXT; large_raster_ratio >= 0.5 (§5.4;
    SPARSE_RASTER_RATIO = 0.50)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_half_page_raster(page)
    page.insert_text((72, 500), "Fig. 1. Diagram.", fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.pages) == 1
    page_info = document.pages[0]
    assert page_info.state == PageTextState.EXPECTED_SPARSE
    assert page_info.large_raster_ratio >= 0.5


# ---------------------------------------------------------------------------
# 14. EXPECTED_SPARSE через короткі блоки без растру.
# ---------------------------------------------------------------------------


def test_gate_14_two_short_blocks_without_raster_give_expected_sparse() -> None:
    """Лист із двома блоками по <=8 слів і без растру отримує
    EXPECTED_SPARSE (§5.4; SPARSE_MAX_SHORT_BLOCKS=2, SHORT_BLOCK_MAX_WORDS=8)."""
    doc = fitz.open()
    page = _new_page(doc)
    page.insert_text((72, 100), "Figure one process diagram.", fontsize=11, fontname="helv")
    page.insert_text((72, 500), "Source own research data.", fontsize=11, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.pages) == 1
    page_info = document.pages[0]
    assert page_info.state == PageTextState.EXPECTED_SPARSE
    assert page_info.large_raster_ratio < 0.5


# ---------------------------------------------------------------------------
# 15. EXPECTED_SPARSE не підміняє NO_TEXT і TEXT_OK.
# ---------------------------------------------------------------------------


def test_gate_15_expected_sparse_never_overrides_no_text_or_text_ok() -> None:
    """Лист із растром >=50 % і 320 символами тексту лишається TEXT_OK; лист
    з растром і нулем символів лишається NO_TEXT (§5.4)."""
    doc = fitz.open()
    page_ok = _new_page(doc)
    _insert_half_page_raster(page_ok)
    _insert_html(page_ok, f"<p>{_cyrillic_filler(320)}</p>", rect=fitz.Rect(72, PAGE_HEIGHT * 0.6, PAGE_WIDTH - 72, PAGE_HEIGHT - 20))
    page_no_text = _new_page(doc)
    _insert_half_page_raster(page_no_text)
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.pages) == 2
    assert document.pages[0].state == PageTextState.TEXT_OK
    assert document.pages[1].state == PageTextState.NO_TEXT


# ---------------------------------------------------------------------------
# 16. Немає текстового шару — NoTextLayerError, OCR не викликається.
# ---------------------------------------------------------------------------


def test_gate_16_a_pdf_with_zero_text_characters_raises_no_text_layer_error() -> None:
    """PDF з єдиного зображення без жодного текстового символу піднімає
    NoTextLayerError, і в тексті винятку є вказівка на текстовий шар (§5.4)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_half_page_raster(page)
    pdf_bytes = _finish(doc)

    with pytest.raises(NoTextLayerError) as exc_info:
        parse_search_document(pdf_bytes)
    assert "текст" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 17. Гібридний PDF розбирається без винятку.
# ---------------------------------------------------------------------------


def test_gate_17_a_hybrid_pdf_with_text_only_on_two_of_four_pages_parses_fine() -> None:
    """Документ, де текст є лише на двох листах із чотирьох, розбирається
    без винятку; pages містить усі чотири записи в правильному порядку
    (§5.4)."""
    doc = fitz.open()
    _new_page(doc)  # без тексту
    page2 = _new_page(doc)
    _insert_html(page2, f"<p>{_cyrillic_filler(300)}</p>")
    _new_page(doc)  # без тексту
    page4 = _new_page(doc)
    _insert_html(page4, f"<p>{_cyrillic_filler(300)}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert len(document.pages) == 4
    assert [p.physical_page for p in document.pages] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 18. Формула охоплення.
# ---------------------------------------------------------------------------


def test_gate_18_coverage_ratio_matches_the_extractable_over_expected_formula() -> None:
    """Розділ CHAPTER на 5 сторінках: 2 з реальним текстом (extractable),
    3 растрові без тексту (expected, але не extractable) — coverage_ratio =
    2/5 = 0.4, строго між 0 і 0.9, відображено і в розділі, і в документі
    (§5.4)."""
    doc = fitz.open()
    page1 = _new_page(doc)
    _insert_html(page1, f"<p>РОЗДІЛ 1</p><p>{_cyrillic_filler(200)}</p>")
    page2 = _new_page(doc)
    _insert_html(page2, f"<p>{_cyrillic_filler(200)}</p>")
    for _ in range(3):
        page = _new_page(doc)
        _insert_half_page_raster(page)
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    assert chapter.expected_body_pages == 5
    assert chapter.extractable_body_pages == 2
    assert chapter.coverage_ratio == pytest.approx(0.4)
    assert 0 < chapter.coverage_ratio < 0.9

    assert document.expected_body_pages == 5
    assert document.extractable_body_pages == 2
    assert document.coverage_ratio == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# 19. Нульовий знаменник охоплення.
# ---------------------------------------------------------------------------


def test_gate_19_a_document_without_content_sections_has_zero_coverage_without_crashing() -> None:
    """Документ без жодного змістовного розділу (INTRO/CHAPTER/CONCLUSIONS)
    — лише титульний текст на першій сторінці — дає coverage_ratio == 0.0 і
    не піднімає ZeroDivisionError (§5.4)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>{TITLE_PARA_1}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert document.expected_body_pages == 0
    assert document.coverage_ratio == 0.0


# ---------------------------------------------------------------------------
# 20. Гранична сторінка двох розділів.
# ---------------------------------------------------------------------------


def test_gate_20_a_page_shared_by_intro_and_chapter_belongs_to_both_sections() -> None:
    """Лист, на якому закінчується ВСТУП і починається РОЗДІЛ 1, входить у
    physical_pages ОБОХ розділів; сума розділів з документним охопленням не
    порівнюється (§5.4; §22 крок 5)."""
    doc = fitz.open()
    page1 = _new_page(doc)
    html = f"<p>ВСТУП</p><p>{INTRO_PARA}</p><p>РОЗДІЛ 1</p><p>{CHAPTER_PARA}</p>"
    _insert_html(page1, html)
    page2 = _new_page(doc)
    _insert_html(page2, f"<p>{_cyrillic_filler(200)}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    intro = next(s for s in document.sections if s.kind == SectionKind.INTRO)
    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    assert 1 in intro.physical_pages
    assert 1 in chapter.physical_pages


# ---------------------------------------------------------------------------
# 21. Усі дев'ять типів розділів.
# ---------------------------------------------------------------------------


def test_gate_21_all_nine_section_kinds_appear_exactly_once() -> None:
    """Синтетичний PDF з десятьма сторінками: TITLE (1-2), UNKNOWN (3, до
    першого заголовка, за межею TITLE_MAX_PAGE=2), TOC, ABSTRACT, INTRO,
    CHAPTER, CONCLUSIONS, BIBLIO, APPENDIX — по одному розділу кожного типу
    (§6.1, таблиця шаблонів; TITLE_MAX_PAGE=2)."""
    doc = fitz.open()
    p1 = _new_page(doc)
    _insert_html(p1, f"<p>{TITLE_PARA_1}</p>")
    p2 = _new_page(doc)
    _insert_html(p2, f"<p>{TITLE_PARA_2}</p>")
    p3 = _new_page(doc)
    _insert_html(p3, f"<p>{UNKNOWN_PARA}</p>")
    p4 = _new_page(doc)
    _insert_html(p4, f"<p>ЗМІСТ</p><p>{TOC_PARA}</p>")
    p5 = _new_page(doc)
    _insert_html(p5, f"<p>АНОТАЦІЯ</p><p>{ABSTRACT_PARA}</p>")
    p6 = _new_page(doc)
    _insert_html(p6, f"<p>ВСТУП</p><p>{INTRO_PARA}</p>")
    p7 = _new_page(doc)
    _insert_html(p7, f"<p>РОЗДІЛ 1</p><p>{CHAPTER_PARA}</p>")
    p8 = _new_page(doc)
    _insert_html(p8, f"<p>ВИСНОВКИ</p><p>{CONCLUSIONS_PARA}</p>")
    p9 = _new_page(doc)
    _insert_html(p9, f"<p>СПИСОК ЛІТЕРАТУРИ</p><p>{BIBLIO_ENTRY_LINE}</p>")
    p10 = _new_page(doc)
    _insert_html(p10, f"<p>ДОДАТОК</p><p>{APPENDIX_PARA}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    kinds = [s.kind for s in document.sections]
    assert len(document.sections) == 9
    assert set(kinds) == set(SectionKind)


# ---------------------------------------------------------------------------
# 22. Римський номер розділу.
# ---------------------------------------------------------------------------


def test_gate_22_a_roman_chapter_number_gives_the_correct_arabic_ordinal() -> None:
    """"РОЗДІЛ IV" дає kind == CHAPTER і ordinal == 4 (§6.1)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>РОЗДІЛ IV</p><p>{CHAPTER_PARA}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    assert chapter.ordinal == 4


# ---------------------------------------------------------------------------
# 23. CHAPTER без номера заголовком не стає.
# ---------------------------------------------------------------------------


def test_gate_23_chapter_keyword_without_a_number_is_not_a_heading() -> None:
    """Блок "РОЗДІЛ" без номера заголовком не стає — жоден розділ документа
    не отримує kind == CHAPTER (§6.1)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>РОЗДІЛ</p><p>{CHAPTER_PARA}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert not any(s.kind == SectionKind.CHAPTER for s in document.sections)


# ---------------------------------------------------------------------------
# 24. Заголовок довший за 12 слів заголовком не стає.
# ---------------------------------------------------------------------------


def test_gate_24_a_heading_longer_than_twelve_words_is_not_recognized() -> None:
    """Рядок "РОЗДІЛ 1 ..." з 18 слів (MAX_HEADING_WORDS=12) заголовком не
    вважається — жоден розділ не отримує kind == CHAPTER (рішення
    оркестратора, крок 5)."""
    long_heading = (
        "РОЗДІЛ 1 Дуже довга назва розділу яка містить більше ніж "
        "дванадцять слів і тому не повинна вважатися заголовком"
    )
    assert len(long_heading.split()) == 18
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>{long_heading}</p><p>{CHAPTER_PARA}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert not any(s.kind == SectionKind.CHAPTER for s in document.sections)


# ---------------------------------------------------------------------------
# 25. Записи змісту в карту розділів не потрапляють; перемагає пізніше входження.
# ---------------------------------------------------------------------------


def test_gate_25_toc_entries_with_dot_leaders_do_not_create_sections_late_heading_wins() -> None:
    """Сторінка ЗМІСТ містить рядки "ВСТУП .......... 5" і "РОЗДІЛ 1. ...
    .......... 12" (крапкові лідери + номер сторінки в кінці) — вони не
    стають заголовками. Реальні ВСТУП і РОЗДІЛ 1 пізніше в документі
    формують розділи, чиї physical_pages не містять сторінки змісту (§6.1)."""
    doc = fitz.open()
    page1 = _new_page(doc)
    html = "<p>ЗМІСТ</p><p>ВСТУП .......... 5</p><p>РОЗДІЛ 1. Тема дослідження .......... 12</p>"
    _insert_html(page1, html)
    page2 = _new_page(doc)
    _insert_html(page2, f"<p>ВСТУП</p><p>{INTRO_PARA}</p>")
    page3 = _new_page(doc)
    _insert_html(page3, f"<p>РОЗДІЛ 1</p><p>{CHAPTER_PARA}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    toc = next(s for s in document.sections if s.kind == SectionKind.TOC)
    intro = next(s for s in document.sections if s.kind == SectionKind.INTRO)
    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)

    assert 1 in toc.physical_pages
    assert 1 not in intro.physical_pages
    assert 1 not in chapter.physical_pages


# ---------------------------------------------------------------------------
# 26. Зони інтервалами: цитата всередині авторського речення.
# ---------------------------------------------------------------------------


def test_gate_26_a_quote_inside_a_sentence_gets_its_own_interval_not_the_whole_block() -> None:
    """Блок "Автор стверджує: «цитата з джерела», і далі продовжує думку."
    дає zone_spans, де цитата — QUOTED_TEXT, а текст до і після —
    AUTHOR_TEXT; жоден спан не покриває весь блок цілком (§4.1, §6.2)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>{QUOTED_SENTENCE}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    block = _block_by_exact_text(document, QUOTED_SENTENCE)
    assert block is not None
    n = len(block.raw_text)

    quoted_spans = [zs for zs in block.zone_spans if zs.zone == TextZone.QUOTED_TEXT]
    assert quoted_spans
    assert any("цитата з джерела" in block.raw_text[zs.raw_start:zs.raw_end] for zs in quoted_spans)

    assert any(zs.zone == TextZone.AUTHOR_TEXT and zs.raw_start == 0 for zs in block.zone_spans)
    assert any(zs.zone == TextZone.AUTHOR_TEXT and zs.raw_end == n for zs in block.zone_spans)
    assert not any((zs.raw_start, zs.raw_end) == (0, n) for zs in block.zone_spans)


# ---------------------------------------------------------------------------
# 27. Незакрита лапка виключає лише поточний блок.
# ---------------------------------------------------------------------------


def test_gate_27_unclosed_quote_only_affects_the_current_block_not_the_next() -> None:
    """Блок з відкриваючою "«" без закриваючої робить UNCERTAIN/QUOTED_TEXT
    лише цей блок; наступний блок лишається звичайним AUTHOR_TEXT (§6.2).

    Два фрагменти навмисно рознесені по вертикалі великим зазором (як у
    тесті 2) — щоб гарантовано отримати два окремі блоки геометрією, а не
    покладатися на природний відступ між <p> (емпірично перевірено: два
    сусідні <p> без великого зазору можуть злитись в один блок)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, "<p>Автор пише « відкриту цитату без завершення.</p>",
                 rect=fitz.Rect(72, 72, PAGE_WIDTH - 72, 160))
    _insert_html(page, "<p>Наступний блок залишається звичайним авторським текстом.</p>",
                 rect=fitz.Rect(72, 600, PAGE_WIDTH - 72, 760))
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    unclosed = next(b for b in document.blocks if "відкриту цитату без завершення" in b.raw_text)
    following = next(b for b in document.blocks if "звичайним авторським текстом" in b.raw_text)

    assert any(zs.zone in (TextZone.UNCERTAIN, TextZone.QUOTED_TEXT) for zs in unclosed.zone_spans)
    assert following.zone_spans
    assert all(zs.zone == TextZone.AUTHOR_TEXT for zs in following.zone_spans)


# ---------------------------------------------------------------------------
# 28. Короткий текст у лапках — теж QUOTED_TEXT.
# ---------------------------------------------------------------------------


def test_gate_28_a_short_quoted_word_is_also_quoted_text() -> None:
    """Один термін у парних лапках "«термін»" дає zone_spans із зоною
    QUOTED_TEXT — алгоритм не вирішує, термін це чи цитата (§6.2)."""
    doc = fitz.open()
    page = _new_page(doc)
    sentence = "Тут є «термін» у реченні."
    _insert_html(page, f"<p>{sentence}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    block = _block_by_exact_text(document, sentence)
    assert block is not None
    quoted = [zs for zs in block.zone_spans if zs.zone == TextZone.QUOTED_TEXT]
    assert quoted
    assert any("термін" in block.raw_text[zs.raw_start:zs.raw_end] for zs in quoted)


# ---------------------------------------------------------------------------
# 29. Зноска: нижня чверть листа, менший кегль.
# ---------------------------------------------------------------------------


def test_gate_29_a_block_in_the_bottom_quarter_with_a_smaller_font_is_a_footnote() -> None:
    """Блок у нижній чверті листа (y > 0,75 * висота) кеглем 9 при
    медіанному кеглі сторінки 14 (ratio = 0,643 <= 0,9) отримує
    FOOTNOTE_TEXT, а не (лише) AUTHOR_TEXT (§6.2; FOOTNOTE_BAND=0.25,
    FOOTNOTE_FONT_RATIO=0.90, рішення оркестратора)."""
    doc = fitz.open()
    page = _new_page(doc)
    for i, y in enumerate((100, 116, 132, 148)):
        page.insert_text((72, y), f"Body line number {i} of normal text.", fontsize=14, fontname="helv")
    page.insert_text((72, 760), "Footnote reference text goes here.", fontsize=9, fontname="helv")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    footnote_block = next(b for b in document.blocks if "Footnote reference text" in b.raw_text)
    assert any(zs.zone == TextZone.FOOTNOTE_TEXT for zs in footnote_block.zone_spans)


# ---------------------------------------------------------------------------
# 30. Пріоритет зон: BIBLIOGRAPHY перемагає QUOTED_TEXT.
# ---------------------------------------------------------------------------


def test_gate_30_bibliography_zone_wins_over_quoted_text_on_overlap() -> None:
    """Рядок бібліографічного запису з цитатою всередині ("«Назва статті»")
    — інтервал, що потрапляє під BIBLIOGRAPHY і QUOTED_TEXT одночасно,
    отримує BIBLIOGRAPHY (search.types.ZONE_PRIORITY; §4.1, §6.2)."""
    doc = fitz.open()
    page = _new_page(doc)
    entry = "1. Іванов І. І. «Назва статті» // Журнал. 2020. С. 5-10."
    _insert_html(page, f"<p>СПИСОК ЛІТЕРАТУРИ</p><p>{entry}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    block = next(b for b in document.blocks if "Назва статті" in b.raw_text)
    quote = "«Назва статті»"
    q_start = block.raw_text.find(quote)
    q_end = q_start + len(quote)
    assert q_start >= 0

    overlapping = [
        zs for zs in block.zone_spans
        if zs.raw_start < q_end and zs.raw_end > q_start
    ]
    assert overlapping
    assert all(zs.zone == TextZone.BIBLIOGRAPHY for zs in overlapping)
    assert not any(zs.zone == TextZone.QUOTED_TEXT for zs in overlapping)


# ---------------------------------------------------------------------------
# 31. author_words рахується лише по AUTHOR_TEXT.
# ---------------------------------------------------------------------------


def test_gate_31_section_author_words_excludes_quoted_word_tokens() -> None:
    """SectionInfo.author_words розділу з цитатою менше за загальну
    кількість словесних токенів усіх його блоків рівно на число слів
    цитати — перевірка через реальні токени й зони цього ж документа, не
    через жорстко закладену цифру (§6.2)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>РОЗДІЛ 1</p><p>{QUOTED_SENTENCE}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    section_blocks = [b for b in document.blocks if b.section_id == chapter.section_id]
    assert section_blocks

    total_word_tokens = sum(1 for b in section_blocks for t in b.tokens if t.is_word)

    quote_word_count = 0
    for b in section_blocks:
        quoted_spans = [zs for zs in b.zone_spans if zs.zone == TextZone.QUOTED_TEXT]
        for t in b.tokens:
            if not t.is_word:
                continue
            if any(zs.raw_start <= t.raw_start and t.raw_end <= zs.raw_end for zs in quoted_spans):
                quote_word_count += 1

    assert quote_word_count > 0
    assert chapter.author_words == total_word_tokens - quote_word_count
    assert chapter.author_words < total_word_tokens


# ---------------------------------------------------------------------------
# 32. body_biblio_confidence == HIGH за шаблоном заголовка.
# ---------------------------------------------------------------------------


def test_gate_32_body_biblio_confidence_is_high_when_the_heading_matches_a_template() -> None:
    """Заголовок бібліографії, що збігається із шаблоном "СПИСОК ЛІТЕРАТУРИ",
    дає body_biblio_confidence == HIGH (§6.1)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>СПИСОК ЛІТЕРАТУРИ</p><p>{BIBLIO_ENTRY_LINE}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert document.body_biblio_confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 33. body_biblio_confidence == LOW без заголовка і без межі split_zones.
# ---------------------------------------------------------------------------


def test_gate_33_body_biblio_confidence_is_low_when_no_bibliography_boundary_is_found() -> None:
    """Документ без заголовка бібліографії за жодним шаблоном і без межі,
    яку міг би знайти parser.bibliography.split_zones, дає
    body_biblio_confidence == LOW; SearchDocument все одно побудований, без
    винятку, розділу BIBLIO немає (§6.1)."""
    doc = fitz.open()
    page = _new_page(doc)
    filler = "Дисертаційне дослідження присвячене актуальній проблемі сучасної науки та її практичному застосуванню в певній галузі."
    _insert_html(page, f"<p>ВСТУП</p><p>{filler}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert document.body_biblio_confidence == Confidence.LOW
    assert not any(s.kind == SectionKind.BIBLIO for s in document.sections)


# ---------------------------------------------------------------------------
# 34. Бібліографія і цитати — валідні порожні колекції для будь-якого входу.
# ---------------------------------------------------------------------------


def test_gate_34_bibliography_and_citations_are_always_empty_at_this_step() -> None:
    """Для будь-якого входу document.bibliography == () і document.citations
    == () — це роботa кроку 6 (§22 крок 5)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>ВСТУП</p><p>{INTRO_PARA}</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert document.bibliography == ()
    assert document.citations == ()


# ---------------------------------------------------------------------------
# 35. Override SET_KIND.
# ---------------------------------------------------------------------------


def test_gate_35_set_kind_override_reclassifies_an_unknown_section_and_recomputes_coverage() -> None:
    """Розділ UNKNOWN (текст на 3-й сторінці, до першого заголовка, за
    межею TITLE_MAX_PAGE) після override з його heading_block_id отримує
    заданий тип; expected_body_pages документа зростає рівно на одну
    сторінку — доказ того, що охоплення справді перераховане, а не просто
    змінена мітка (§6.1, контракт override)."""
    doc = fitz.open()
    p1 = _new_page(doc)
    _insert_html(p1, f"<p>{TITLE_PARA_1}</p>")
    p2 = _new_page(doc)
    _insert_html(p2, f"<p>{TITLE_PARA_2}</p>")
    p3 = _new_page(doc)
    _insert_html(p3, f"<p>{UNKNOWN_PARA}</p>")
    p4 = _new_page(doc)
    _insert_html(p4, f"<p>ВСТУП</p><p>{INTRO_PARA}</p>")
    p5 = _new_page(doc)
    _insert_html(p5, f"<p>РОЗДІЛ 1</p><p>{CHAPTER_PARA}</p>")
    p6 = _new_page(doc)
    _insert_html(p6, f"<p>ВИСНОВКИ</p><p>{CONCLUSIONS_PARA}</p>")
    pdf_bytes = _finish(doc)

    document_before = parse_search_document(pdf_bytes)
    unknown_section = next(s for s in document_before.sections if s.kind == SectionKind.UNKNOWN)
    heading_block = _heading_block(document_before, unknown_section)

    override = SectionOverride(
        action=SectionOverrideAction.SET_KIND,
        heading_block_id=heading_block.block_id,
        section_kind=SectionKind.CHAPTER,
    )
    document_after = parse_search_document(pdf_bytes, overrides=(override,))

    updated_section = next(
        s for s in document_after.sections if s.block_start == unknown_section.block_start
    )
    assert updated_section.kind == SectionKind.CHAPTER
    assert document_after.expected_body_pages == document_before.expected_body_pages + 1
    assert document_after.extractable_body_pages == document_before.extractable_body_pages + 1


# ---------------------------------------------------------------------------
# 36. Override EXCLUDE_HEADING.
# ---------------------------------------------------------------------------


def test_gate_36_exclude_heading_override_merges_text_and_reduces_section_count_by_one() -> None:
    """Виключення заголовка "РОЗДІЛ 1" через override прирощує його текст до
    попереднього розділу (ВСТУП) — кількість розділів зменшується рівно на
    один, розділу CHAPTER більше немає (§6.1, контракт override)."""
    doc = fitz.open()
    p1 = _new_page(doc)
    _insert_html(p1, f"<p>ВСТУП</p><p>{INTRO_PARA}</p>")
    p2 = _new_page(doc)
    _insert_html(p2, f"<p>РОЗДІЛ 1</p><p>{CHAPTER_PARA}</p>")
    p3 = _new_page(doc)
    _insert_html(p3, f"<p>ВИСНОВКИ</p><p>{CONCLUSIONS_PARA}</p>")
    pdf_bytes = _finish(doc)

    document_before = parse_search_document(pdf_bytes)
    chapter_section = next(s for s in document_before.sections if s.kind == SectionKind.CHAPTER)
    heading_block = _heading_block(document_before, chapter_section)
    assert heading_block.raw_text.strip() == "РОЗДІЛ 1"

    override = SectionOverride(
        action=SectionOverrideAction.EXCLUDE_HEADING,
        heading_block_id=heading_block.block_id,
        section_kind=None,
    )
    document_after = parse_search_document(pdf_bytes, overrides=(override,))

    assert len(document_after.sections) == len(document_before.sections) - 1
    assert not any(s.kind == SectionKind.CHAPTER for s in document_after.sections)


# ---------------------------------------------------------------------------
# 37. Override на невідомий block_id піднімає ValueError.
# ---------------------------------------------------------------------------


def test_gate_37_an_override_referencing_an_unknown_block_id_raises_value_error() -> None:
    """SectionOverride із неіснуючим heading_block_id піднімає ValueError, у
    тексті якого названо цей ідентифікатор (контракт)."""
    doc = fitz.open()
    page = _new_page(doc)
    _insert_html(page, f"<p>ВСТУП</p><p>{INTRO_PARA}</p>")
    pdf_bytes = _finish(doc)

    unknown_id = "nonexistent-block-id-xyz-12345"
    override = SectionOverride(
        action=SectionOverrideAction.SET_KIND,
        heading_block_id=unknown_id,
        section_kind=SectionKind.APPENDIX,
    )

    with pytest.raises(ValueError) as exc_info:
        parse_search_document(pdf_bytes, overrides=(override,))
    assert unknown_id in str(exc_info.value)


# ---------------------------------------------------------------------------
# 38. applied_overrides дорівнює переданому кортежу.
# ---------------------------------------------------------------------------


def test_gate_38_applied_overrides_equals_the_tuple_passed_in() -> None:
    """document.applied_overrides у результаті рівний саме переданому
    кортежу overrides (контракт)."""
    doc = fitz.open()
    p1 = _new_page(doc)
    _insert_html(p1, f"<p>{UNKNOWN_PARA}</p>")
    p2 = _new_page(doc)
    _insert_html(p2, f"<p>{UNKNOWN_PARA}</p>")
    p3 = _new_page(doc)
    _insert_html(p3, f"<p>{UNKNOWN_PARA}</p>")
    pdf_bytes = _finish(doc)

    document_before = parse_search_document(pdf_bytes)
    unknown_section = next(s for s in document_before.sections if s.kind == SectionKind.UNKNOWN)
    heading_block = _heading_block(document_before, unknown_section)

    overrides = (
        SectionOverride(
            action=SectionOverrideAction.SET_KIND,
            heading_block_id=heading_block.block_id,
            section_kind=SectionKind.APPENDIX,
        ),
    )
    document_after = parse_search_document(pdf_bytes, overrides=overrides)

    assert document_after.applied_overrides == overrides


# ---------------------------------------------------------------------------
# 39. Доноры пропускают сторінковий залишок.
# ---------------------------------------------------------------------------


def test_gate_39_the_unterminated_tail_of_the_last_block_on_a_page_is_not_a_donor() -> None:
    """Останній авторський блок сторінки обривається без термінальної
    пунктуації: цей хвіст не потрапляє в document.sentences, а завершене
    перше речення того самого блока — потрапляє (§10.1)."""
    doc = fitz.open()
    p1 = _new_page(doc)
    text = (
        "Перше речення завершено крапкою. "
        "Незавершений залишок без крапки в кінці сторінки"
    )
    _insert_html(p1, f"<p>РОЗДІЛ 1</p><p>{text}</p>")
    p2 = _new_page(doc)
    _insert_html(p2, "<p>Друге речення на новій сторінці, повністю завершене.</p>")
    pdf_bytes = _finish(doc)

    document = parse_search_document(pdf_bytes)

    assert any(d.raw_text.strip() == "Перше речення завершено крапкою." for d in document.sentences)
    assert not any("Незавершений залишок" in d.raw_text for d in document.sentences)


# ---------------------------------------------------------------------------
# 40. PARSER_VERSION.
# ---------------------------------------------------------------------------


def test_gate_40_parser_version_is_nonempty_and_bumped_from_the_frozen_baseline() -> None:
    """PARSER_VERSION — непорожній рядок, відмінний від попереднього
    зафіксованого значення "searchdoc-parser-2026-08-25" (контракт)."""
    assert isinstance(PARSER_VERSION, str)
    assert PARSER_VERSION != ""
    assert PARSER_VERSION != PREVIOUS_PARSER_VERSION


# ---------------------------------------------------------------------------
# Спільна фікстура для пунктів 41-44: дев'ять реальних PDF корпусу.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus_payload():
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def corpus_documents(corpus_payload):
    """{filename: (fixture_entry, pdf_bytes, SearchDocument)} — розбирає
    кожен із дев'яти реальних PDF рівно один раз для пунктів 41-44."""
    docs = {}
    for entry in corpus_payload["documents"]:
        path = EXAMPLES_DIR / entry["file"]
        pdf_bytes = path.read_bytes()
        docs[entry["file"]] = (entry, pdf_bytes, parse_search_document(pdf_bytes))
    return docs


def _first_page_of_kind(document, kind):
    pages = [
        min(s.physical_pages)
        for s in document.sections
        if s.kind == kind and s.physical_pages
    ]
    return min(pages) if pages else None


# ---------------------------------------------------------------------------
# 41. Дев'ять PDF: документ будується.
# ---------------------------------------------------------------------------


def test_gate_41_all_nine_corpus_pdfs_parse_with_matching_page_count_and_hash(corpus_documents) -> None:
    """Для кожного з дев'яти PDF корпусу parse_search_document відпрацьовує
    без винятку, n_pages збігається з expected_pages фікстури, а sha256
    файлу збігається із записаним (§22 крок 5)."""
    for fname, (entry, pdf_bytes, document) in corpus_documents.items():
        assert document.n_pages == entry["expected_pages"], fname
        actual_sha = hashlib.sha256(pdf_bytes).hexdigest()
        assert actual_sha == entry["sha256"], fname


# ---------------------------------------------------------------------------
# 42. Дев'ять PDF: сторінки заголовків структурних розділів.
# ---------------------------------------------------------------------------


def test_gate_42_corpus_structure_headings_match_the_fixture_within_tolerance(
    corpus_payload, corpus_documents
) -> None:
    """Для кожного документа з structure.status == "filled" перший лист
    розділів INTRO, першого CHAPTER, CONCLUSIONS і BIBLIO збігається з
    intro_page / first_chapter_page / conclusions_page / bibliography_page
    фікстури з допуском PAGE_TOLERANCE (з поля page_tolerance фікстури;
    §22 крок 5)."""
    tolerance = corpus_payload["page_tolerance"]
    for fname, (entry, _pdf_bytes, document) in corpus_documents.items():
        structure = entry["sections"]["structure"]
        if structure["status"] != "filled":
            continue
        headings = structure["headings"]

        intro_page = _first_page_of_kind(document, SectionKind.INTRO)
        chapter_page = _first_page_of_kind(document, SectionKind.CHAPTER)
        conclusions_page = _first_page_of_kind(document, SectionKind.CONCLUSIONS)
        biblio_page = _first_page_of_kind(document, SectionKind.BIBLIO)

        assert intro_page is not None, f"{fname}: немає розділу INTRO"
        assert chapter_page is not None, f"{fname}: немає розділу CHAPTER"
        assert conclusions_page is not None, f"{fname}: немає розділу CONCLUSIONS"
        assert biblio_page is not None, f"{fname}: немає розділу BIBLIO"

        assert abs(intro_page - headings["intro_page"]) <= tolerance, fname
        assert abs(chapter_page - headings["first_chapter_page"]) <= tolerance, fname
        assert abs(conclusions_page - headings["conclusions_page"]) <= tolerance, fname
        assert abs(biblio_page - headings["bibliography_page"]) <= tolerance, fname


# ---------------------------------------------------------------------------
# 43. Дев'ять PDF: зміст не підміняє реальні розділи.
# ---------------------------------------------------------------------------


def test_gate_43_corpus_intro_first_page_never_equals_the_toc_page(corpus_documents) -> None:
    """Ні в одному з дев'яти документів перший лист розділу INTRO не
    збігається з листом розділу TOC (§6.1: записи змісту не стають
    заголовками)."""
    for fname, (_entry, _pdf_bytes, document) in corpus_documents.items():
        intro_page = _first_page_of_kind(document, SectionKind.INTRO)
        toc_page = _first_page_of_kind(document, SectionKind.TOC)
        if intro_page is None or toc_page is None:
            continue
        assert intro_page != toc_page, fname


# ---------------------------------------------------------------------------
# 44. Дев'ять PDF: бібліографія не потрапляє в тіло роботи.
# ---------------------------------------------------------------------------


def test_gate_44_corpus_bibliography_zone_never_belongs_to_a_content_section(corpus_documents) -> None:
    """Ні в одного з дев'яти документів жоден блок із зоною BIBLIOGRAPHY не
    належить розділу з CONTENT_SECTION_KINDS (INTRO/CHAPTER/CONCLUSIONS)
    (§6.1, §6.2)."""
    for fname, (_entry, _pdf_bytes, document) in corpus_documents.items():
        kind_by_section_id = {s.section_id: s.kind for s in document.sections}
        for b in document.blocks:
            if not any(zs.zone == TextZone.BIBLIOGRAPHY for zs in b.zone_spans):
                continue
            section_kind = kind_by_section_id.get(b.section_id)
            assert section_kind not in CONTENT_SECTION_KINDS, (fname, b.block_id, section_kind)


# ---------------------------------------------------------------------------
# 45. Детермінізм.
# ---------------------------------------------------------------------------


def test_gate_45_two_runs_on_the_same_bytes_give_equal_results_in_the_same_order() -> None:
    """Два виклики parse_search_document на одних і тих самих байтах дають
    рівні blocks, sections, sentences, pages і той самий порядок;
    document_sha256 збігається (§22 крок 5)."""
    doc = fitz.open()
    p1 = _new_page(doc)
    _insert_html(p1, f"<p>РОЗДІЛ 1</p><p>{QUOTED_SENTENCE}</p>")
    p2 = _new_page(doc)
    _insert_html(p2, "<p>Друга сторінка містить ще один самостійний абзац тексту для перевірки стабільності розбору.</p>")
    pdf_bytes = _finish(doc)

    document_1 = parse_search_document(pdf_bytes)
    document_2 = parse_search_document(pdf_bytes)

    assert document_1.blocks == document_2.blocks
    assert document_1.sections == document_2.sections
    assert document_1.sentences == document_2.sentences
    assert document_1.pages == document_2.pages
    assert document_1.document_sha256 == document_2.document_sha256
    assert document_1 == document_2
