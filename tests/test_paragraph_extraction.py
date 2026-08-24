"""
Tests for the paragraph-extraction half of paragraph_analyzer.py — the part
that reads real DOCX/PDF bytes. Previously 0% covered.

Run: pytest tests/test_paragraph_extraction.py
"""
import unittest

import pytest

from parser.paragraph_analyzer import (
    ContentBoundsNotFoundError,
    analyze_paragraph_gaps,
    extract_paragraphs,
    find_content_bounds_in_texts,
    paragraph_has_citation,
    _is_toc_entry,
)

# Довгі абзаци: extract_paragraphs відкидає все коротше за MIN_BLOCK_CHARS (80).
BODY_A = ("Аналіз наявних підходів засвідчує суттєву розбіжність у трактуванні "
          "базових понять предметної області дослідження автором.")
BODY_B = ("Отримані результати підтверджують висунуту гіпотезу та узгоджуються "
          "з висновками попередніх досліджень у цій галузі знань.")
BODY_CITED = ("Наведена класифікація спирається на роботи попередників [12] та "
              "розвиває їхні положення стосовно предмета дослідження цієї праці.")
INTRO_TEXT = ("Актуальність теми зумовлена потребою вдосконалення наявних "
              "механізмів та browsing підходів до організації цієї діяльності.")


class TestTocEntryDetection(unittest.TestCase):
    def test_dot_leaders(self):
        self.assertTrue(_is_toc_entry("РОЗДІЛ 1. ОГЛЯД ...... 12"))

    def test_tab_leader(self):
        self.assertTrue(_is_toc_entry("РОЗДІЛ 1. ОГЛЯД\t12"))

    def test_space_alignment(self):
        self.assertTrue(_is_toc_entry("ВИСНОВКИ   180"))

    def test_plain_heading_is_not_toc(self):
        self.assertFalse(_is_toc_entry("РОЗДІЛ 1"))
        self.assertFalse(_is_toc_entry("ВИСНОВКИ"))

    def test_heading_with_single_space_number_is_not_toc(self):
        # "РОЗДІЛ 1" must survive — only 2+ spaces mean column alignment
        self.assertFalse(_is_toc_entry("РОЗДІЛ 2"))


class TestParagraphHasCitation(unittest.TestCase):
    def test_real_reference(self):
        self.assertTrue(paragraph_has_citation("як зазначено у [12]"))

    def test_array_index_is_not_a_citation(self):
        self.assertFalse(paragraph_has_citation("елемент масиву a[0] дорівнює"))

    def test_year_bracket_is_not_a_citation(self):
        self.assertFalse(paragraph_has_citation("у праці [2020] автор"))

    def test_author_year_bracket_is_not_a_citation(self):
        self.assertFalse(paragraph_has_citation("за [Іванов, 2020]"))

    def test_no_brackets(self):
        self.assertFalse(paragraph_has_citation("звичайний текст"))


class TestFindContentBoundsCore(unittest.TestCase):
    def test_skips_tab_leader_toc(self):
        texts = ["ЗМІСТ", "РОЗДІЛ 1. ОГЛЯД\t12", "ВИСНОВКИ\t180",
                 "ВСТУП", "текст вступу", "РОЗДІЛ 1", "текст", "ВИСНОВКИ"]
        start, end = find_content_bounds_in_texts(texts)
        self.assertEqual(start, 5)
        self.assertEqual(end, 6)

    def test_empty_input_raises(self):
        with self.assertRaises(ContentBoundsNotFoundError):
            find_content_bounds_in_texts([])


@pytest.mark.usefixtures("make_docx")
class TestDocxExtraction:
    """Regressions for the DOCX content zone opening at the table of contents."""

    def test_toc_with_tab_leaders_does_not_open_the_zone(self, make_docx):
        data = make_docx([
            "ЗМІСТ",
            "ВСТУП\t3",
            "РОЗДІЛ 1. ОГЛЯД ЛІТЕРАТУРИ\t12",
            "ВИСНОВКИ\t180",
            "ВСТУП",
            INTRO_TEXT,
            "РОЗДІЛ 1",
            BODY_A,
            BODY_B,
            "ВИСНОВКИ",
            "Текст загальних висновків, який не має потрапити до аналізу зовсім.",
        ])
        paras = extract_paragraphs(data, "d.docx")
        texts = [p.text for p in paras]

        assert BODY_A in texts
        assert BODY_B in texts
        assert INTRO_TEXT not in texts, "ВСТУП leaked into the content zone"
        assert not any("загальних висновків" in t for t in texts)

    def test_toc_without_page_numbers_does_not_empty_the_result(self, make_docx):
        # Mirror failure: a bare "ВИСНОВКИ" in the TOC used to break the
        # forward scan immediately and yield zero paragraphs, which the UI
        # then reported as a cheerful all-clear.
        data = make_docx([
            "ЗМІСТ",
            "ВСТУП",
            "РОЗДІЛ 1. ОГЛЯД ЛІТЕРАТУРИ",
            "ВИСНОВКИ",
            "ВСТУП",
            INTRO_TEXT,
            "РОЗДІЛ 1",
            BODY_A,
            BODY_B,
            "ВИСНОВКИ",
        ])
        paras = extract_paragraphs(data, "d.docx")
        assert len(paras) > 0, "content zone collapsed to nothing"
        assert BODY_A in [p.text for p in paras]

    def test_headings_are_not_counted_as_paragraphs(self, make_docx):
        data = make_docx([
            ("РОЗДІЛ 1", "Heading 1"),
            BODY_A,
            ("ВИСНОВКИ", "Heading 1"),
        ])
        paras = extract_paragraphs(data, "d.docx")
        assert [p.text for p in paras] == [BODY_A]

    def test_context_heading_recorded(self, make_docx):
        data = make_docx([
            ("РОЗДІЛ 1 ТЕОРЕТИЧНІ ЗАСАДИ", "Heading 1"),
            BODY_A,
            ("ВИСНОВКИ", "Heading 1"),
        ])
        paras = extract_paragraphs(data, "d.docx")
        assert paras[0].context_heading == "РОЗДІЛ 1 ТЕОРЕТИЧНІ ЗАСАДИ"

    def test_short_paragraphs_skipped(self, make_docx):
        data = make_docx(["РОЗДІЛ 1", "Коротко.", BODY_A, "ВИСНОВКИ"])
        paras = extract_paragraphs(data, "d.docx")
        assert [p.text for p in paras] == [BODY_A]

    def test_no_chapter_raises(self, make_docx):
        data = make_docx(["ВСТУП", INTRO_TEXT, "ВИСНОВКИ"])
        with pytest.raises(ContentBoundsNotFoundError):
            extract_paragraphs(data, "d.docx")


class TestPdfExtraction:
    """
    PDF fixtures use Latin text on purpose: PyMuPDF's built-in Base-14 fonts
    carry no Cyrillic glyphs, so inserted Cyrillic comes back out of
    get_text() as a row of bullets. What these tests exercise — page-range
    filtering, the MIN_BLOCK_CHARS cut-off, page numbering and paragraph
    merging by vertical gap — is script-independent. The Cyrillic-specific
    logic is covered by the DOCX tests and the pure unit tests above.
    """

    PDF_A = ("Analysis of the existing approaches reveals a substantial "
             "divergence in how the base concepts are interpreted here.")
    PDF_B = ("The obtained results confirm the stated hypothesis and agree "
             "with the conclusions of the earlier studies in this field.")

    def test_page_range_is_respected(self, make_pdf):
        data = make_pdf([
            ["TITLE PAGE OF THE DISSERTATION WHICH IS LONG ENOUGH TO PASS THE "
             "MIN BLOCK CHARS FILTER EASILY"],
            [self.PDF_A],
            [self.PDF_B],
            ["CONCLUSIONS"],
        ])
        paras = extract_paragraphs(data, "d.pdf", 2, 3)
        texts = " ".join(p.text for p in paras)
        assert "Analysis of the existing approaches" in texts
        assert "The obtained results confirm" in texts
        assert "TITLE PAGE" not in texts

    def test_short_blocks_skipped(self, make_pdf):
        data = make_pdf([["Short."]])
        assert extract_paragraphs(data, "d.pdf", 1, 1) == []

    def test_pages_carry_their_number(self, make_pdf):
        data = make_pdf([[self.PDF_A], [self.PDF_B]])
        paras = extract_paragraphs(data, "d.pdf", 1, 2)
        assert {p.page for p in paras} == {1, 2}

    def test_adjacent_lines_merge_into_one_paragraph(self, make_pdf):
        # Two consecutive lines with a normal leading are one paragraph
        data = make_pdf([["First half of a single logical paragraph that is",
                          "continued on the very next line without any gap."]])
        paras = extract_paragraphs(data, "d.pdf", 1, 1)
        assert len(paras) == 1
        assert "continued on the very next line" in paras[0].text

    def test_unknown_extension_returns_empty(self):
        assert extract_paragraphs(b"", "d.txt") == []


class TestAnalyzeParagraphGaps:
    def test_docx_end_to_end(self, make_docx):
        data = make_docx([
            "РОЗДІЛ 1", BODY_CITED, BODY_A, BODY_B, "ВИСНОВКИ",
        ])
        r = analyze_paragraph_gaps(data, "d.docx", [], None)
        assert r.docx_mode is True
        assert r.total_paragraphs == 3
        assert r.cited_paragraphs == 1
        assert r.clean_paragraphs == 2
        assert r.clean_pct == pytest.approx(200 / 3)

    def test_docx_ignores_the_lines_argument(self, make_docx):
        # The DOCX path must not depend on extract_content_bounds over `lines`:
        # passing lines that would make it raise must not break the analysis.
        data = make_docx(["РОЗДІЛ 1", BODY_A, "ВИСНОВКИ"])
        r = analyze_paragraph_gaps(data, "d.docx", [{"line": "junk", "page": None}], None)
        assert r.total_paragraphs == 1

    def test_pdf_end_to_end(self, make_pdf):
        # Bounds come from `lines` (real Cyrillic); the PDF body carries the
        # Latin filler the built-in fonts can actually render.
        body = ("Analysis of the existing approaches reveals a substantial "
                "divergence in how the base concepts are interpreted.")
        data = make_pdf([["INTRO"], ["CHAPTER 1"], [body], ["CONCLUSIONS"]])
        lines = [
            {"line": "ВСТУП", "page": 1},
            {"line": "РОЗДІЛ 1", "page": 2},
            {"line": body, "page": 3},
            {"line": "ВИСНОВКИ", "page": 4},
        ]
        r = analyze_paragraph_gaps(data, "d.pdf", lines, None)
        assert r.docx_mode is False
        assert r.total_paragraphs == 1
        assert r.clean_paragraphs == 1

    def test_suspicious_needs_enough_sentences(self, make_docx):
        long_clean = " ".join(
            f"Це речення номер {i} у цьому абзаці." for i in range(1, 8)
        )
        data = make_docx(["РОЗДІЛ 1", long_clean, "ВИСНОВКИ"])
        r = analyze_paragraph_gaps(data, "d.docx", [], None)
        assert len(r.suspicious) == 1
        assert r.suspicious[0]["sentence_count"] >= 5

    def test_cited_paragraph_never_suspicious(self, make_docx):
        long_cited = "Перше речення [7]. " + " ".join(
            f"Це речення номер {i} у цьому абзаці." for i in range(1, 8)
        )
        data = make_docx(["РОЗДІЛ 1", long_cited, "ВИСНОВКИ"])
        r = analyze_paragraph_gaps(data, "d.docx", [], None)
        assert r.suspicious == []


class TestConclusionsExcluded(unittest.TestCase):
    """
    Walking backwards must not stop at the bibliography heading: that is a
    POST-content section, and the real content end (ВИСНОВКИ) sits above it.
    Without the distinction the whole conclusions chapter counted as content.
    """

    def test_backward_scan_passes_over_bibliography_to_conclusions(self):
        texts = (["ВСТУП", "текст вступу", "РОЗДІЛ 1", "текст розділу"]
                 + ["ВИСНОВКИ", "текст висновків", "ще висновки"]
                 + ["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ", "1. Джерело."]
                 + ["ДОДАТКИ", "Додаток А"])
        start, end = find_content_bounds_in_texts(texts)
        self.assertEqual(start, 2)
        self.assertEqual(end, 3, "conclusions leaked into the content zone")

    def test_appendices_only_still_stops_before_them(self):
        texts = ["РОЗДІЛ 1", "текст", "ДОДАТКИ", "Додаток А"]
        _start, end = find_content_bounds_in_texts(texts)
        self.assertEqual(end, 1)


class TestDocxConclusionsExcluded:
    def test_conclusions_chapter_excluded(self, make_docx):
        conclusion = ("У результаті виконаного дослідження сформульовано низку "
                      "висновків, що мають наукову новизну та практичну цінність.")
        data = make_docx([
            "РОЗДІЛ 1", BODY_A,
            "ВИСНОВКИ", conclusion,
            "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ", "1. Джерело.",
        ])
        paras = extract_paragraphs(data, "d.docx")
        texts = [p.text for p in paras]
        assert BODY_A in texts
        assert conclusion not in texts
