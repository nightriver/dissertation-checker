"""
Модульні тести кроку 5 (§22 PLAN_SEARCH.md): структурна частина
`parser/searchdoc.py` — склейка рядків у блоки, колонки, колонтитули,
стани сторінок, зони інтервалами, карта розділів, охоплення й overrides.

PDF збираються прямо тут через `fitz`. Там, де важлива геометрія
(вертикальний розрив, кегль, колонки, смуга сторінки), використовується
`page.insert_text` з базовим шрифтом і латиницею: базовий Helvetica не має
кириличних гліфів, а геометрію блоків алфавіт не обходить. Там, де
важливий зміст (заголовки розділів, лапки, ЗМІСТ), використовується
`page.insert_htmlbox`, який коректно вбудовує кирилицю без зовнішнього
файлу шрифту (той самий прийом, що в тонкому зрізі кроку 3).
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from parser.searchdoc import NoTextLayerError, parse_search_document
from search.types import (
    Confidence,
    PageTextState,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
    TextZone,
)

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

# Рядок із десяти слів: довший за `_SHORT_BLOCK_MAX_WORDS`, тому сторінка з
# ним не стає «очікувано розрідженою».
TEN_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa"


def _new_page(doc):
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _write_lines(page, lines, *, x=72.0, fontsize=11.0):
    """Малює `(y, текст)` латиницею базовим шрифтом — точна геометрія."""
    for y, text in lines:
        page.insert_text((x, y), text, fontsize=fontsize)


def _html(page, markup, *, rect=None):
    """Кирилиця через `insert_htmlbox`: базовий шрифт її не має."""
    page.insert_htmlbox(rect or fitz.Rect(60, 60, 540, 780), markup)


def _finish(doc) -> bytes:
    data = doc.tobytes()
    doc.close()
    return data


def _blocks_on(document, physical_page):
    return [b for b in document.blocks if b.physical_page == physical_page]


def _zones_of(block):
    return {span.zone for span in block.zone_spans}


def _page(document, physical_page):
    return next(p for p in document.pages if p.physical_page == physical_page)


def _filler(words: int) -> str:
    return " ".join(["слово"] * words)


# ---------------------------------------------------------------------------
# §5.2 — склейка рядків у блоки
# ---------------------------------------------------------------------------


def test_paragraph_lines_with_normal_leading_merge_into_one_block():
    """Три рядки абзацу зі звичайним інтервалом дають один блок."""
    doc = fitz.open()
    page = _new_page(doc)
    _write_lines(
        page,
        [(100.0, "first line of the paragraph"),
         (114.0, "second line of the paragraph"),
         (128.0, "third line of the paragraph")],
    )
    document = parse_search_document(_finish(doc))

    blocks = _blocks_on(document, 1)
    assert len(blocks) == 1
    for fragment in ("first line", "second line", "third line"):
        assert fragment in blocks[0].raw_text


def test_a_vertical_gap_above_the_factor_starts_a_new_block():
    """Розрив більший за 1,4 × медіанної висоти рядка починає новий блок."""
    doc = fitz.open()
    page = _new_page(doc)
    _write_lines(
        page,
        [(100.0, "first line of the paragraph"),
         (114.0, "second line of the paragraph"),
         (400.0, "line after a wide vertical gap")],
    )
    document = parse_search_document(_finish(doc))

    blocks = _blocks_on(document, 1)
    assert len(blocks) == 2
    assert "wide vertical gap" in blocks[1].raw_text


def test_a_font_size_change_starts_a_new_block():
    """Зміна кегля більша за 25 % розриває блок навіть на звичайному інтервалі."""
    doc = fitz.open()
    page = _new_page(doc)
    _write_lines(page, [(100.0, "normal size line here")], fontsize=11.0)
    _write_lines(page, [(116.0, "much bigger size line")], fontsize=20.0)
    document = parse_search_document(_finish(doc))

    blocks = _blocks_on(document, 1)
    assert len(blocks) == 2


def test_a_sentence_period_does_not_break_a_block():
    """Кінець речення сам по собі блок не розриває (§5.2, п.4)."""
    doc = fitz.open()
    page = _new_page(doc)
    _write_lines(
        page,
        [(100.0, "the first sentence ends here."),
         (114.0, "Another sentence starts here")],
    )
    document = parse_search_document(_finish(doc))

    blocks = _blocks_on(document, 1)
    assert len(blocks) == 1
    assert "Another sentence" in blocks[0].raw_text


def test_two_columns_are_read_left_to_right():
    """Спершу вся ліва колонка згори вниз, потім уся права (§5.2, п.5)."""
    doc = fitz.open()
    page = _new_page(doc)
    _write_lines(page, [(100.0, "left column top"), (114.0, "left column body")], x=60.0)
    _write_lines(page, [(100.0, "right column top"), (114.0, "right column body")], x=360.0)
    document = parse_search_document(_finish(doc))

    blocks = _blocks_on(document, 1)
    left = [b for b in blocks if "left column" in b.raw_text]
    right = [b for b in blocks if "right column" in b.raw_text]
    assert left and right
    assert max(b.block_index for b in left) < min(b.block_index for b in right)


def test_text_is_not_merged_across_a_page_boundary():
    """Абзац, обірваний на кінці аркуша, дає два блоки (§5.2, п.7)."""
    doc = fitz.open()
    first = _new_page(doc)
    _write_lines(first, [(760.0, "the paragraph is interrupted by the")])
    second = _new_page(doc)
    _write_lines(second, [(100.0, "boundary of the physical page here")])
    document = parse_search_document(_finish(doc))

    pages = {b.physical_page for b in document.blocks}
    assert pages == {1, 2}
    assert len(document.blocks) == 2


def test_pieces_of_one_visual_line_are_merged_before_blocks_are_built():
    """
    Шматки одного візуального рядка (виключка розсовує слова) склеюються в
    один рядок: інакше слово «Висновки» посеред речення стає заголовком.
    """
    doc = fitz.open()
    page = _new_page(doc)
    # Відступ між шматками менший за `_COLUMN_GAP_FACTOR ×` ширини аркуша,
    # тому це саме розсунутий рядок, а не дві колонки.
    _write_lines(page, [(200.0, "the hearing ended.")], x=60.0)
    _write_lines(page, [(200.0, "conclusions of the court")], x=180.0)
    document = parse_search_document(_finish(doc))

    blocks = _blocks_on(document, 1)
    assert len(blocks) == 1
    assert "hearing ended. conclusions of the court" in blocks[0].raw_text


# ---------------------------------------------------------------------------
# §5.3 — колонтитули
# ---------------------------------------------------------------------------


def _document_with_running_title(pages: int, title_pages: int, title: str = "running title"):
    doc = fitz.open()
    for index in range(pages):
        page = _new_page(doc)
        if index < title_pages:
            _write_lines(page, [(40.0, title)])
        _write_lines(page, [(300.0, TEN_WORDS)])
    return _finish(doc)


def test_a_repeated_running_title_is_marked_and_leaves_the_author_text():
    """Колонтитул на всіх п'яти аркушах — зона HEADER_FOOTER, не AUTHOR_TEXT."""
    document = parse_search_document(_document_with_running_title(5, 5))

    marked = [b for b in document.blocks if TextZone.HEADER_FOOTER in _zones_of(b)]
    assert len(marked) == 5
    for block in marked:
        assert "running title" in block.raw_text
        assert TextZone.AUTHOR_TEXT not in _zones_of(block)


def test_running_title_page_numbers_are_normalized_to_a_placeholder():
    """Нижні рядки «12», «13», «14» — той самий колонтитул (§5.3)."""
    doc = fitz.open()
    for number in range(11, 16):
        page = _new_page(doc)
        _write_lines(page, [(300.0, TEN_WORDS)])
        _write_lines(page, [(800.0, str(number))])
    document = parse_search_document(_finish(doc))

    marked = [b for b in document.blocks if TextZone.HEADER_FOOTER in _zones_of(b)]
    assert len(marked) == 5
    assert {b.raw_text for b in marked} == {"11", "12", "13", "14", "15"}


def test_a_line_on_two_of_five_pages_is_not_a_running_title():
    """Поріг 60 % придатних аркушів: 2 з 5 колонтитулом не роблять."""
    document = parse_search_document(_document_with_running_title(5, 2))

    assert not any(TextZone.HEADER_FOOTER in _zones_of(b) for b in document.blocks)


def test_running_titles_are_not_searched_in_a_two_page_document():
    """Менше трьох придатних аркушів — колонтитули не шукаються взагалі."""
    document = parse_search_document(_document_with_running_title(2, 2))

    assert not any(TextZone.HEADER_FOOTER in _zones_of(b) for b in document.blocks)


# ---------------------------------------------------------------------------
# §5.4 — стани сторінок і охоплення
# ---------------------------------------------------------------------------


def _raster_page(page, ratio_height=0.7):
    """Малює суцільний растр на верхній частині аркуша."""
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 64, 64), 0)
    pix.clear_with(128)
    page.insert_image(fitz.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT * ratio_height), pixmap=pix)


def test_page_states_follow_the_content_character_thresholds():
    """0 символів — NO_TEXT, 1..199 — LOW_TEXT, ≥200 — TEXT_OK."""
    doc = fitz.open()
    _new_page(doc)
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    long_page = _new_page(doc)
    _write_lines(long_page, [(100.0 + 14.0 * i, TEN_WORDS) for i in range(8)])
    document = parse_search_document(_finish(doc))

    assert _page(document, 1).state == PageTextState.NO_TEXT
    assert _page(document, 2).state == PageTextState.LOW_TEXT
    assert _page(document, 3).state == PageTextState.TEXT_OK
    assert _page(document, 1).reason
    assert _page(document, 2).reason
    assert _page(document, 3).reason == ""


def test_a_large_raster_page_with_a_caption_is_expected_sparse():
    """Растр на ≥50 % площі дає EXPECTED_SPARSE замість LOW_TEXT."""
    doc = fitz.open()
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    raster = _new_page(doc)
    _raster_page(raster)
    _write_lines(raster, [(800.0, TEN_WORDS)])
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    document = parse_search_document(_finish(doc))

    info = _page(document, 2)
    assert info.state == PageTextState.EXPECTED_SPARSE
    assert info.large_raster_ratio >= 0.5
    assert info.reason


def test_two_short_blocks_without_a_raster_are_expected_sparse():
    """Не більше двох блоків до восьми слів кожен — очікувано розріджений аркуш."""
    doc = fitz.open()
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    sparse = _new_page(doc)
    _write_lines(sparse, [(100.0, "short caption here")])
    _write_lines(sparse, [(400.0, "another short caption")])
    document = parse_search_document(_finish(doc))

    info = _page(document, 2)
    assert info.state == PageTextState.EXPECTED_SPARSE
    assert info.large_raster_ratio < 0.5


def test_expected_sparse_never_replaces_text_ok_or_no_text():
    """EXPECTED_SPARSE підміняє лише LOW_TEXT (§5.4)."""
    doc = fitz.open()
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    full = _new_page(doc)
    _raster_page(full)
    _write_lines(full, [(600.0 + 14.0 * i, TEN_WORDS) for i in range(8)])
    empty = _new_page(doc)
    _raster_page(empty)
    document = parse_search_document(_finish(doc))

    assert _page(document, 2).state == PageTextState.TEXT_OK
    assert _page(document, 3).state == PageTextState.NO_TEXT


def test_a_pdf_without_any_text_raises_no_text_layer_error():
    """Скан без текстового шару: OCR не запускається, помилка називає шар."""
    doc = fitz.open()
    for _ in range(2):
        _raster_page(_new_page(doc))
    data = _finish(doc)

    with pytest.raises(NoTextLayerError) as info:
        parse_search_document(data)
    assert "текстов" in str(info.value).lower()


def test_a_hybrid_pdf_keeps_every_page_in_the_report():
    """Гібридний PDF розбирається по доступних аркушах, `pages` повний."""
    doc = fitz.open()
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    _new_page(doc)
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    _new_page(doc)
    document = parse_search_document(_finish(doc))

    assert document.n_pages == 4
    assert [p.physical_page for p in document.pages] == [1, 2, 3, 4]
    assert _page(document, 2).state == PageTextState.NO_TEXT


def test_coverage_ratio_is_zero_without_content_sections():
    """Знаменник нуль ⇒ охоплення 0.0 і жодного ZeroDivisionError."""
    doc = fitz.open()
    _write_lines(_new_page(doc), [(100.0, TEN_WORDS)])
    document = parse_search_document(_finish(doc))

    assert document.expected_body_pages == 0
    assert document.coverage_ratio == 0.0


def test_a_page_without_blocks_stays_inside_the_section_range():
    """
    Аркуш-ілюстрація всередині розділу лишається в `physical_pages` і
    підвищує знаменник охоплення, а не зникає (уточнення пакета до §5.4).
    """
    doc = fitz.open()
    first = _new_page(doc)
    _html(first, f"<p>РОЗДІЛ 1</p><p>{_filler(40)}</p>")
    _raster_page(_new_page(doc))
    document = parse_search_document(_finish(doc))

    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    assert chapter.physical_pages == (1, 2)
    assert chapter.expected_body_pages == 2
    assert chapter.extractable_body_pages == 1
    assert chapter.coverage_ratio == pytest.approx(0.5)
    assert document.coverage_ratio == pytest.approx(0.5)


def test_the_boundary_page_belongs_to_both_sections():
    """Аркуш, де кінчається ВСТУП і починається РОЗДІЛ 1, входить в обидва."""
    doc = fitz.open()
    first = _new_page(doc)
    _html(first, f"<p>ВСТУП</p><p>{_filler(30)}</p>")
    second = _new_page(doc)
    _html(second, f"<p>{_filler(20)}</p><p>РОЗДІЛ 1</p><p>{_filler(30)}</p>")
    document = parse_search_document(_finish(doc))

    intro = next(s for s in document.sections if s.kind == SectionKind.INTRO)
    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    assert 2 in intro.physical_pages
    assert 2 in chapter.physical_pages


# ---------------------------------------------------------------------------
# §6.1 — карта розділів
# ---------------------------------------------------------------------------


def _sections_by_kind(document, kind):
    return [s for s in document.sections if s.kind == kind]


def test_every_heading_template_gives_its_own_section():
    """Дев'ять типів розділів: вісім за шаблоном плюс TITLE до першого з них."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, "<p>Прізвище автора</p><p>ЗМІСТ</p><p>АНОТАЦІЯ</p><p>ВСТУП</p>")
    page2 = _new_page(doc)
    _html(
        page2,
        "<p>РОЗДІЛ 1. Назва</p><p>ВИСНОВКИ</p>"
        "<p>СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ</p><p>ДОДАТОК А</p>",
    )
    document = parse_search_document(_finish(doc))

    kinds = [s.kind for s in document.sections]
    for kind in (
        SectionKind.TITLE,
        SectionKind.TOC,
        SectionKind.ABSTRACT,
        SectionKind.INTRO,
        SectionKind.CHAPTER,
        SectionKind.CONCLUSIONS,
        SectionKind.BIBLIO,
        SectionKind.APPENDIX,
    ):
        assert kind in kinds, kind
    assert kinds[0] == SectionKind.TITLE
    assert document.body_biblio_confidence == Confidence.HIGH


def test_a_roman_chapter_number_becomes_an_arabic_ordinal():
    """«РОЗДІЛ IV» — CHAPTER з ordinal == 4."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>РОЗДІЛ IV</p><p>{_filler(20)}</p>")
    document = parse_search_document(_finish(doc))

    chapter = next(s for s in document.sections if s.kind == SectionKind.CHAPTER)
    assert chapter.ordinal == 4


def test_a_chapter_without_a_number_is_not_a_heading():
    """`РОЗДІЛ` без номера заголовком не стає."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>РОЗДІЛ</p><p>{_filler(20)}</p>")
    document = parse_search_document(_finish(doc))

    assert not _sections_by_kind(document, SectionKind.CHAPTER)


def test_a_heading_longer_than_twelve_words_is_not_a_heading():
    """Заголовок довший за дванадцять слів заголовком не вважається."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>ВСТУП {_filler(14)}</p>")
    document = parse_search_document(_finish(doc))

    assert not _sections_by_kind(document, SectionKind.INTRO)


def test_a_letter_spaced_heading_is_still_recognized():
    """Розрядка «В С Т У П» — той самий заголовок ВСТУП."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>В С Т У П</p><p>{_filler(20)}</p>")
    document = parse_search_document(_finish(doc))

    assert _sections_by_kind(document, SectionKind.INTRO)


def test_a_run_of_two_single_letter_tokens_is_not_treated_as_letter_spacing():
    """
    Серія рівно з двох однолітерних токенів розрядкою не вважається.

    Рядок «I V ВСТУП» — це два однолітерні токени поспіль перед словом.
    За порогом `_LETTER_SPACING_MIN_RUN = 3` вони не зводяться, рядок
    лишається «I V ВСТУП» і з шаблоном ВСТУПу не збігається. Якби поріг
    був 2, вийшло б «IV ВСТУП» — провідна нумерація плюс ключова форма, —
    і блок став би заголовком розділу INTRO. Тому цей тест відрізняє
    поріг 3 від порога 2, чого тест на «В С Т У П» зробити не може.
    """
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>I V ВСТУП</p><p>{_filler(20)}</p>")
    document = parse_search_document(_finish(doc))

    assert any("I V ВСТУП" in block.raw_text for block in document.blocks)
    assert not _sections_by_kind(document, SectionKind.INTRO)


def test_toc_entries_do_not_replace_the_real_sections():
    """
    Записи ЗМІСТу з крапковими лідерами розділів не створюють; справжні
    ВСТУП і РОЗДІЛ 1 знаходяться за пізнішими входженнями (§6.1).
    """
    doc = fitz.open()
    toc_page = _new_page(doc)
    _html(
        toc_page,
        "<p>ЗМІСТ</p>"
        "<p>ВСТУП .......................................... 5</p>"
        "<p>РОЗДІЛ 1. Назва ............................... 12</p>",
    )
    body = _new_page(doc)
    _html(body, f"<p>ВСТУП</p><p>{_filler(30)}</p>")
    third = _new_page(doc)
    _html(third, f"<p>РОЗДІЛ 1. Назва</p><p>{_filler(30)}</p>")
    document = parse_search_document(_finish(doc))

    intro = _sections_by_kind(document, SectionKind.INTRO)
    chapter = _sections_by_kind(document, SectionKind.CHAPTER)
    assert len(intro) == 1
    assert len(chapter) == 1
    assert 1 not in intro[0].physical_pages
    assert 1 not in chapter[0].physical_pages
    assert _sections_by_kind(document, SectionKind.TOC)[0].physical_pages[0] == 1


def test_a_document_without_a_bibliography_gets_low_confidence():
    """Ні заголовка бібліографії, ні межі від `split_zones` — LOW і без BIBLIO."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>РОЗДІЛ 1</p><p>{_filler(40)}</p>")
    document = parse_search_document(_finish(doc))

    assert document.body_biblio_confidence == Confidence.LOW
    assert not _sections_by_kind(document, SectionKind.BIBLIO)


def test_bibliography_and_citations_stay_empty_after_step_five():
    """Наповнення бібліографії і цитувань — крок 6, тут валідні порожні кортежі."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>РОЗДІЛ 1</p><p>{_filler(30)}</p><p>СПИСОК ЛІТЕРАТУРИ</p>")
    document = parse_search_document(_finish(doc))

    assert document.bibliography == ()
    assert document.citations == ()


# ---------------------------------------------------------------------------
# §6.2, §4.1 — зони інтервалами
# ---------------------------------------------------------------------------


QUOTED = "Автор стверджує: «цитата з джерела», і далі продовжує думку."


def test_a_quote_gets_its_own_interval_inside_the_block():
    """Цитата — окремий інтервал; жоден спан не покриває блок цілком."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>{QUOTED}</p>")
    document = parse_search_document(_finish(doc))

    block = next(b for b in document.blocks if "цитата з джерела" in b.raw_text)
    length = len(block.raw_text)
    quoted = [z for z in block.zone_spans if z.zone == TextZone.QUOTED_TEXT]
    assert quoted
    assert "«цитата з джерела»" in block.raw_text[quoted[0].raw_start : quoted[0].raw_end]
    assert not any((z.raw_start, z.raw_end) == (0, length) for z in block.zone_spans)


def test_an_unclosed_quote_touches_only_its_own_block():
    """Незакрита лапка виключає поточний блок, а не решту документа."""
    doc = fitz.open()
    _html(_new_page(doc), "<p>Автор пише «цитата без закриття далі текст</p>")
    _html(_new_page(doc), f"<p>{_filler(12)}</p>")
    document = parse_search_document(_finish(doc))

    broken = next(b for b in document.blocks if "без закриття" in b.raw_text)
    following = next(b for b in document.blocks if b.block_index > broken.block_index)
    assert _zones_of(broken) & {TextZone.UNCERTAIN, TextZone.QUOTED_TEXT}
    assert _zones_of(following) == {TextZone.AUTHOR_TEXT}


def test_a_short_quoted_term_is_still_quoted_text():
    """Короткий текст у парних лапках — теж QUOTED_TEXT (§6.2)."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, "<p>Поняття «право» вживається в широкому розумінні тут.</p>")
    document = parse_search_document(_finish(doc))

    block = next(b for b in document.blocks if "право" in b.raw_text)
    quoted = [z for z in block.zone_spans if z.zone == TextZone.QUOTED_TEXT]
    assert quoted
    assert "«право»" in block.raw_text[quoted[0].raw_start : quoted[0].raw_end]


def test_a_small_font_block_at_the_bottom_is_a_footnote():
    """Нижня чверть аркуша плюс кегль ≤0,9 медіанного — FOOTNOTE_TEXT."""
    doc = fitz.open()
    page = _new_page(doc)
    _write_lines(page, [(100.0 + 16.0 * i, TEN_WORDS) for i in range(10)], fontsize=12.0)
    _write_lines(page, [(700.0, "footnote text with a source reference")], fontsize=7.0)
    document = parse_search_document(_finish(doc))

    footnote = next(b for b in document.blocks if "footnote text" in b.raw_text)
    assert TextZone.FOOTNOTE_TEXT in _zones_of(footnote)
    assert TextZone.AUTHOR_TEXT not in _zones_of(footnote)


def test_author_words_ignore_the_quoted_interval():
    """Плотність рахується лише по AUTHOR_TEXT: слова цитати не враховані."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>РОЗДІЛ 1</p><p>{QUOTED}</p>")
    document = parse_search_document(_finish(doc))

    block = next(b for b in document.blocks if "цитата з джерела" in b.raw_text)
    section = next(s for s in document.sections if s.section_id == block.section_id)
    total_words = sum(
        1
        for member in document.blocks
        if member.section_id == section.section_id
        for token in member.tokens
        if token.is_word
    )
    # «цитата з джерела» — три словесні токени всередині лапок.
    assert section.author_words == total_words - 3


def test_bibliography_zone_wins_over_a_quote_inside_it():
    """Приоритет зон: перетин BIBLIOGRAPHY і QUOTED_TEXT дає BIBLIOGRAPHY."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(
        page,
        "<p>СПИСОК ЛІТЕРАТУРИ</p>"
        "<p>1. Автор А. А. «Назва праці» : монографія. Київ, 2020. 320 с.</p>",
    )
    document = parse_search_document(_finish(doc))

    entry = next(b for b in document.blocks if "монографія" in b.raw_text)
    assert _zones_of(entry) == {TextZone.BIBLIOGRAPHY}


# ---------------------------------------------------------------------------
# §6.1 — overrides
# ---------------------------------------------------------------------------


def _override_document():
    doc = fitz.open()
    page = _new_page(doc)
    _html(
        page,
        f"<p>Наукова новизна одержаних результатів</p><p>{_filler(20)}</p>"
        f"<p>ВСТУП</p><p>{_filler(20)}</p>",
    )
    return _finish(doc)


def test_set_kind_override_rebuilds_the_section_map():
    """SET_KIND: блок стає заголовком заданого типу, лічильники перераховані."""
    data = _override_document()
    before = parse_search_document(data)
    target = next(b for b in before.blocks if b.raw_text.startswith("Наукова новизна"))
    override = SectionOverride(
        action=SectionOverrideAction.SET_KIND,
        heading_block_id=target.block_id,
        section_kind=SectionKind.CHAPTER,
    )

    after = parse_search_document(data, overrides=(override,))

    assert not _sections_by_kind(before, SectionKind.CHAPTER)
    chapter = _sections_by_kind(after, SectionKind.CHAPTER)
    assert len(chapter) == 1
    assert chapter[0].author_words > 0
    assert after.applied_overrides == (override,)


def test_exclude_heading_override_merges_the_text_into_the_previous_section():
    """EXCLUDE_HEADING: розділів на один менше, текст приростає до попереднього."""
    data = _override_document()
    before = parse_search_document(data)
    intro_heading = next(b for b in before.blocks if b.raw_text.strip() == "ВСТУП")
    override = SectionOverride(
        action=SectionOverrideAction.EXCLUDE_HEADING,
        heading_block_id=intro_heading.block_id,
        section_kind=None,
    )

    after = parse_search_document(data, overrides=(override,))

    assert len(after.sections) == len(before.sections) - 1
    assert not _sections_by_kind(after, SectionKind.INTRO)


def test_an_override_with_an_unknown_block_id_raises_value_error():
    """Override на невідомий блок мовчки не ігнорується."""
    data = _override_document()
    override = SectionOverride(
        action=SectionOverrideAction.SET_KIND,
        heading_block_id="blk-99999",
        section_kind=SectionKind.CHAPTER,
    )

    with pytest.raises(ValueError) as info:
        parse_search_document(data, overrides=(override,))
    assert "blk-99999" in str(info.value)


# ---------------------------------------------------------------------------
# §10.1 — донори речень
# ---------------------------------------------------------------------------


def test_a_page_boundary_remainder_is_not_a_donor():
    """
    Хвіст останнього авторського блоку аркуша без термінальної пунктуації
    донором не стає, а завершені речення того ж блоку лишаються.
    """
    doc = fitz.open()
    page = _new_page(doc)
    _html(
        page,
        "<p>РОЗДІЛ 1</p>"
        "<p>Перше речення розділу завершене крапкою тут. Друга частина абзацу "
        "обривається межею аркуша без крапки і тому</p>",
    )
    document = parse_search_document(_finish(doc))

    texts = [donor.raw_text for donor in document.sentences]
    assert any(text.startswith("Перше речення") for text in texts)
    assert not any(text.endswith("і тому") for text in texts)


def test_a_heading_block_is_not_a_sentence_donor():
    """Заголовок розділу — межа розділу, а не авторське речення."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, "<p>РОЗДІЛ 1</p><p>Єдине завершене речення цього розділу тут.</p>")
    document = parse_search_document(_finish(doc))

    assert len(document.sentences) == 1
    assert document.sentences[0].raw_text.startswith("Єдине завершене")


def test_repeated_parsing_gives_an_identical_document():
    """Детермінізм: ті самі байти — той самий `SearchDocument`."""
    doc = fitz.open()
    page = _new_page(doc)
    _html(page, f"<p>РОЗДІЛ 1</p><p>{_filler(30)}</p>")
    data = _finish(doc)

    first = parse_search_document(data)
    second = parse_search_document(data)

    assert first == second
    assert first.document_sha256 == second.document_sha256
