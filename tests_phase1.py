"""
tests_phase1.py
Запуск: python tests_phase1.py
Залежностей за межами стандартної бібліотеки немає.
"""

import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Stub PyMuPDF and python-docx so tests run without heavy dependencies
# ---------------------------------------------------------------------------

# fitz stub
fitz_mod = types.ModuleType("fitz")
class _FakePage:
    def __init__(self, text): self._text = text
    def get_text(self, _): return self._text
class _FakeDoc:
    def __init__(self, pages): self._pages = pages
    def __iter__(self): return iter(self._pages)
    def close(self): pass
def _fitz_open(stream, filetype):
    return _FakeDoc([])
fitz_mod.open = _fitz_open
sys.modules["fitz"] = fitz_mod

# docx stub
docx_mod = types.ModuleType("docx")
class _FakePara:
    def __init__(self, text): self.text = text
class _FakeDocxDoc:
    def __init__(self, paras): self.paragraphs = paras
def _docx_document(f): return _FakeDocxDoc([])
docx_mod.Document = _docx_document
sys.modules["docx"] = docx_mod

# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------
from parser.bibliography import (
    split_zones, split_zones_manual, parse_bibliography,
    BibliographyNotFoundError,
)
from parser.citations import find_citations, compare, _expand_bracket


def _lines(texts, page=1):
    """Helper — wrap list[str] → list[{"line":..., "page":...}]"""
    return [{"line": t, "page": page} for t in texts]


# ---------------------------------------------------------------------------
# bibliography.py tests
# ---------------------------------------------------------------------------

class TestSplitZones(unittest.TestCase):

    def _make_doc(self):
        body = ["Вступ.", "Розділ 1.", "Текст тексту [1]. Ще текст [2; 3]."]
        biblio = ["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
                  "1. Автор А.А. Назва. Київ, 2020.",
                  "2. Автор Б.Б. Інша назва. Харків, 2021."]
        after = ["ДОДАТКИ", "Додаток А. Таблиці."]
        return _lines(body) + _lines(biblio) + _lines(after)

    def test_auto_split_body_length(self):
        doc = self._make_doc()
        result = split_zones(doc)
        self.assertEqual(len(result.body), 3)

    def test_auto_split_biblio_length(self):
        doc = self._make_doc()
        result = split_zones(doc)
        self.assertEqual(len(result.bibliography), 3)

    def test_auto_split_after_ignored(self):
        doc = self._make_doc()
        result = split_zones(doc)
        self.assertEqual(len(result.after), 2)

    def test_auto_split_header_captured(self):
        doc = self._make_doc()
        result = split_zones(doc)
        self.assertEqual(result.biblio_header_line, "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ")

    def test_not_found_raises(self):
        doc = _lines(["Текст без бібліографії."])
        with self.assertRaises(BibliographyNotFoundError):
            split_zones(doc)

    def test_no_stop_word_biblio_extends_to_end(self):
        doc = _lines(["Вступ."]) + _lines(["СПИСОК ЛІТЕРАТУРИ", "1. Джерело."])
        result = split_zones(doc)
        self.assertEqual(len(result.bibliography), 2)
        self.assertEqual(len(result.after), 0)

    def test_last_header_wins(self):
        """Якщо заголовок зустрічається двічі — береться останній."""
        doc = (
            _lines(["Ранній СПИСОК ЛІТЕРАТУРИ фейковий"])
            + _lines(["Текст між."])
            + _lines(["СПИСОК ЛІТЕРАТУРИ"])   # ← цей останній
            + _lines(["1. Джерело."])
        )
        result = split_zones(doc)
        # body повинен включати перший рядок і "Текст між."
        self.assertEqual(len(result.body), 2)

    def test_manual_split(self):
        doc = _lines(["Вступ."]) + _lines(["Мій розділ джерел", "1. Джерело."])
        result = split_zones_manual(doc, "Мій розділ джерел")
        self.assertEqual(len(result.body), 1)
        self.assertEqual(len(result.bibliography), 2)

    def test_manual_split_page_filter(self):
        lines = (
            [{"line": "Вступ.", "page": 1}]
            + [{"line": "Мій розділ джерел", "page": 5}]
            + [{"line": "1. Джерело.", "page": 5}]
        )
        result = split_zones_manual(lines, "Мій розділ джерел", start_page=5)
        self.assertEqual(len(result.body), 1)


class TestParseBibliography(unittest.TestCase):

    def test_simple_entries(self):
        lines = _lines([
            "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
            "1. Автор А.А. Назва. Київ, 2020.",
            "2. Автор Б.Б. Інша назва. Харків, 2021.",
        ])
        result = parse_bibliography(lines)
        self.assertEqual(len(result), 2)
        self.assertIn(1, result)
        self.assertIn(2, result)

    def test_multiline_entry(self):
        lines = _lines([
            "1. Автор А.А. Дуже довга",
            "назва книги, яка",
            "продовжується на наступному рядку. Київ, 2020.",
            "2. Автор Б.Б. Коротка назва.",
        ])
        result = parse_bibliography(lines)
        self.assertIn("Дуже довга", result[1])
        self.assertIn("продовжується", result[1])
        self.assertEqual(len(result), 2)

    def test_bracket_format(self):
        lines = _lines([
            "[1] Автор А.А. Назва.",
            "[2] Автор Б.Б. Інша.",
        ])
        result = parse_bibliography(lines)
        self.assertIn(1, result)
        self.assertIn(2, result)

    def test_mixed_format(self):
        lines = _lines([
            "1. Перше джерело.",
            "[2] Друге джерело.",
        ])
        result = parse_bibliography(lines)
        self.assertEqual(len(result), 2)

    def test_empty_section(self):
        lines = _lines(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ", ""])
        result = parse_bibliography(lines)
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# citations.py tests
# ---------------------------------------------------------------------------

class TestExpandBracket(unittest.TestCase):

    def test_single(self):
        self.assertEqual(_expand_bracket("1"), {1})

    def test_with_page(self):
        self.assertEqual(_expand_bracket("1, 15"), {1})

    def test_semicolon_list(self):
        self.assertEqual(_expand_bracket("1; 3; 7"), {1, 3, 7})

    def test_semicolon_with_pages(self):
        self.assertEqual(_expand_bracket("1, 15; 3, 20; 7"), {1, 3, 7})

    def test_range_hyphen(self):
        self.assertEqual(_expand_bracket("15-18"), {15, 16, 17, 18})

    def test_range_endash(self):
        self.assertEqual(_expand_bracket("15–18"), {15, 16, 17, 18})

    def test_complex(self):
        self.assertEqual(
            _expand_bracket("1, 15; 3; 5, 20-25; 40, 20-25, 100; 60, 145, 150"),
            {1, 3, 5, 40, 60},
        )

    def test_range_ignored_when_after_comma(self):
        # "5, 20-25" — 5 є джерелом, 20-25 є сторінками
        self.assertEqual(_expand_bracket("5, 20-25"), {5})


class TestFindCitations(unittest.TestCase):

    def test_basic(self):
        body = _lines(["Як зазначено [1; 3; 7], це важливо."])
        self.assertEqual(find_citations(body), {1, 3, 7})

    def test_multiple_brackets_one_line(self):
        body = _lines(["Текст [1, 10] і ще [2; 5, 20]."])
        self.assertEqual(find_citations(body), {1, 2, 5})

    def test_range_in_body(self):
        body = _lines(["Дивись [15-18] для деталей."])
        self.assertEqual(find_citations(body), {15, 16, 17, 18})

    def test_no_citations(self):
        body = _lines(["Текст без жодних посилань."])
        self.assertEqual(find_citations(body), set())

    def test_multiline_body(self):
        body = _lines(["Перший рядок [1].", "Другий рядок [2; 3]."])
        self.assertEqual(find_citations(body), {1, 2, 3})


class TestCompare(unittest.TestCase):

    def test_all_used(self):
        bib = {1: "A", 2: "B", 3: "C"}
        cit = {1, 2, 3}
        r = compare(bib, cit)
        self.assertEqual(r["orphans"], set())
        self.assertEqual(r["used"], {1, 2, 3})

    def test_some_orphans(self):
        bib = {1: "A", 2: "B", 3: "C"}
        cit = {1, 3}
        r = compare(bib, cit)
        self.assertEqual(r["orphans"], {2})

    def test_phantom_ignored(self):
        bib = {1: "A"}
        cit = {1, 99}
        r = compare(bib, cit)
        self.assertEqual(r["phantom"], {99})
        self.assertEqual(r["orphans"], set())

    def test_all_orphans(self):
        bib = {1: "A", 2: "B"}
        cit = set()
        r = compare(bib, cit)
        self.assertEqual(r["orphans"], {1, 2})
        self.assertEqual(r["used"], set())


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestSplitZones, TestParseBibliography,
        TestExpandBracket, TestFindCitations, TestCompare,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
