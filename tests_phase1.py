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

    def test_year_not_treated_as_entry(self):
        """Рядок '2005. URL: ...' не повинен стати джерелом #2005."""
        lines = _lines([
            "1. Конституція України: прийнята на п'ятій сесії Верховної Ради",
            "2005. URL: https://zakon.rada.gov.ua/laws/show/254к/96-вр",
            "2. Інше джерело.",
        ])
        result = parse_bibliography(lines)
        self.assertNotIn(2005, result)
        self.assertIn(1, result)
        self.assertIn(2, result)
        # рядок з роком має бути приєднаний до джерела #1
        self.assertIn("2005", result[1])

    def test_year_1882_not_treated_as_entry(self):
        lines = _lines([
            "5. Загальна теорія держави і права, видання",
            "1882. Харків: Право.",
            "6. Наступне джерело.",
        ])
        result = parse_bibliography(lines)
        self.assertNotIn(1882, result)
        self.assertIn(5, result)


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

    # --- Регресійні тести: баг 1+2 — U+2212 математичний мінус ---

    def test_range_minus_sign_u2212(self):
        """U+2212 '−' має розгортатись як діапазон."""
        self.assertEqual(_expand_bracket("15\u221218"), {15, 16, 17, 18})

    def test_bracket_with_u2212_range(self):
        """Скобка з U+2212 повинна матчитись і повертати всі джерела."""
        # [9; 40; 81; 134–136; 147; 194; 209−210; 213; 220]
        inner = "9; 40; 81; 134\u201336; 147; 194; 209\u2212210; 213; 220"
        result = _expand_bracket(inner)
        self.assertIn(9, result)
        self.assertIn(40, result)
        self.assertIn(209, result)
        self.assertIn(210, result)

    def test_find_citations_bracket_u2212(self):
        """find_citations повинен знайти всі номери зі скобки з U+2212."""
        body = _lines(["Текст [9; 40; 81; 134\u2013136; 209\u2212210; 213] кінець."])
        result = find_citations(body)
        self.assertIn(9, result)
        self.assertIn(40, result)
        self.assertIn(209, result)
        self.assertIn(210, result)
        self.assertIn(134, result)
        self.assertIn(136, result)


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


# ---------------------------------------------------------------------------
# Regression tests — Bug 1, 2, 3 (discovered on real dissertation)
# ---------------------------------------------------------------------------

class TestBug1MultilineCitations(unittest.TestCase):
    """Bug 1: citations split across two lines by PyMuPDF."""

    def test_split_two_numbers(self):
        body = [
            {"line": "[124; 149;", "page": 2},
            {"line": "179] text.", "page": 2},
        ]
        result = find_citations(body)
        self.assertEqual(result & {124, 149, 179}, {124, 149, 179})

    def test_split_many_numbers(self):
        # [4; 91; 115;\n133; 192; 214]
        body = [
            {"line": "text [4; 91; 115;", "page": 1},
            {"line": "133; 192; 214] text.", "page": 1},
        ]
        result = find_citations(body)
        for n in [4, 91, 115, 133, 192, 214]:
            self.assertIn(n, result)

    def test_split_with_range(self):
        # [9; 40; 81; 134-136; 147; 194; 209-210;\n213; 220]
        body = [
            {"line": "text [9; 40; 81; 134-136; 147; 194; 209-210;", "page": 1},
            {"line": "213; 220].", "page": 1},
        ]
        result = find_citations(body)
        for n in [9, 40, 81, 134, 135, 136, 147, 194, 209, 210, 213, 220]:
            self.assertIn(n, result, f"Missing {n}")

    def test_no_false_positives_on_join(self):
        body = [
            {"line": "text [5].", "page": 1},
            {"line": "[7] other text.", "page": 1},
        ]
        result = find_citations(body)
        self.assertIn(5, result)
        self.assertIn(7, result)
        # 57 must not appear from joining "5].[7"
        self.assertNotIn(57, result)


class TestBug2UnicodeMinus(unittest.TestCase):
    """Bug 2: U+2212 MINUS SIGN used as range separator in PDF output."""

    def test_range_u2212(self):
        body = [{"line": "text [209\u2212210].", "page": 5}]
        self.assertEqual(find_citations(body), {209, 210})

    def test_range_endash_still_works(self):
        body = [{"line": "text [134\u2013136].", "page": 5}]
        self.assertEqual(find_citations(body), {134, 135, 136})

    def test_mixed_dashes_same_bracket(self):
        body = [{"line": "text [134\u2013136; 209\u2212210].", "page": 3}]
        self.assertEqual(find_citations(body), {134, 135, 136, 209, 210})

    def test_u2212_multiline_range(self):
        # Real case: [9; 40; 81; 134-136; 147; 194; 209-210;\n213; 220]
        body = [
            {"line": "text [9; 40; 81; 134\u201336; 147; 194; 209\u221210;", "page": 1},
            {"line": "213; 220].", "page": 1},
        ]
        result = find_citations(body)
        for n in [9, 40, 81, 134, 135, 136, 147, 194, 209, 210, 213, 220]:
            self.assertIn(n, result, f"Missing {n}")


class TestBug3FalseYearEntries(unittest.TestCase):
    """Bug 3: lines starting with year+dot misidentified as new entry."""

    def _lines(self, texts):
        return [{"line": t, "page": 200} for t in texts]

    def test_year_continuation_ignored(self):
        lines = self._lines([
            "2. Author I. Title / I. Author // Journal. --",
            "2001. -- No 29. -- P. 2.",
            "3. Author N. Another title. -- M., 2000. -- 240 p.",
        ])
        result = parse_bibliography(lines)
        self.assertIn(2, result)
        self.assertIn(3, result)
        self.assertNotIn(2001, result)
        self.assertNotIn(2000, result)

    def test_document_number_in_continuation(self):
        lines = self._lines([
            "170. Decision of committee dated 15.03.07. No",
            "1882. -- Zh., 2007. -- 8 p.",
            "171. On approval of norms. -- K., 2005. -- 12 p.",
        ])
        result = parse_bibliography(lines)
        self.assertIn(170, result)
        self.assertIn(171, result)
        self.assertNotIn(1882, result)
        self.assertIn("1882", result[170])

    def test_valid_entries_999(self):
        lines = self._lines([
            "998. Author A. First title.",
            "999. Author B. Second title.",
        ])
        result = parse_bibliography(lines)
        self.assertIn(998, result)
        self.assertIn(999, result)

    def test_entry_1000_rejected(self):
        lines = self._lines(["1000. Not a source, just a year or document number."])
        result = parse_bibliography(lines)
        self.assertNotIn(1000, result)



class TestBug1Multiline(unittest.TestCase):
    def test_two_numbers_split(self):
        body = [{"line": "text [124; 149;", "page": 1},
                {"line": "179] end.", "page": 1}]
        self.assertEqual(find_citations(body) & {124, 149, 179}, {124, 149, 179})

    def test_range_split(self):
        body = [{"line": "text [9; 40; 134-136; 209-210;", "page": 1},
                {"line": "213; 220] end.", "page": 1}]
        result = find_citations(body)
        for n in [9, 40, 134, 135, 136, 209, 210, 213, 220]:
            self.assertIn(n, result)

    def test_single_line_unaffected(self):
        body = [{"line": "text [5; 7].", "page": 1}]
        self.assertEqual(find_citations(body), {5, 7})


class TestBug2U2212(unittest.TestCase):
    def test_u2212_range(self):
        body = [{"line": "text [209−210].", "page": 5}]
        self.assertEqual(find_citations(body), {209, 210})

    def test_endash_range(self):
        body = [{"line": "text [134–136].", "page": 5}]
        self.assertEqual(find_citations(body), {134, 135, 136})

    def test_mixed_dashes(self):
        body = [{"line": "text [134–136; 209−210].", "page": 3}]
        self.assertEqual(find_citations(body), {134, 135, 136, 209, 210})


class TestBug3WingdingsBrackets(unittest.TestCase):
    def test_pua_simple(self):
        body = [{"line": "94; 108 text.", "page": 46}]
        result = find_citations(body)
        self.assertIn(94, result)
        self.assertIn(108, result)

    def test_pua_range_u2212(self):
        body = [{"line": "107−108 text.", "page": 46}]
        self.assertEqual(find_citations(body), {107, 108})

    def test_pua_and_standard_mixed(self):
        body = [{"line": "see [1; 2] and 3; 4.", "page": 1}]
        self.assertEqual(find_citations(body), {1, 2, 3, 4})

    def test_pua_multiline(self):
        body = [{"line": "94; 97;", "page": 46},
                {"line": "107 text.", "page": 46}]
        result = find_citations(body)
        for n in [94, 97, 107]:
            self.assertIn(n, result)


class TestBug4CommaMode(unittest.TestCase):
    def test_three_sources_comma_only(self):
        body = [{"line": "text [3, 7, 11] end.", "page": 9}]
        self.assertEqual(find_citations(body), {3, 7, 11})

    def test_four_sources_comma_only(self):
        body = [{"line": "text [1, 5, 10, 12] end.", "page": 9}]
        self.assertEqual(find_citations(body), {1, 5, 10, 12})

    def test_comma_range(self):
        body = [{"line": "text [1, 5, 14, 16-17] end.", "page": 9}]
        self.assertEqual(find_citations(body), {1, 5, 14, 16, 17})

    def test_semicolon_mode_unaffected(self):
        body = [{"line": "text [1, 15; 3; 5, 20-25] end.", "page": 1}]
        self.assertEqual(find_citations(body), {1, 3, 5})

    def test_two_comma_sources(self):
        body = [{"line": "text [69,85] end.", "page": 9}]
        self.assertEqual(find_citations(body), {69, 85})


class TestBug5BibNumberOnOwnLine(unittest.TestCase):
    def _L(self, texts):
        return [{"line": t, "page": 200} for t in texts]

    def test_number_own_line(self):
        lines = self._L(["11.", "", "Baykulatova V. Title. Kyiv, 2010.",
                          "12.", "", "Baytsar R. Other. Kharkiv, 2012."])
        result = parse_bibliography(lines)
        self.assertIn(11, result)
        self.assertIn(12, result)
        self.assertIn("Baykulatova", result[11])

    def test_inline_still_works(self):
        lines = self._L(["1. Author A. Title.", "2. Author B. Another."])
        result = parse_bibliography(lines)
        self.assertIn(1, result)
        self.assertIn(2, result)

    def test_mixed_inline_and_own_line(self):
        lines = self._L(["10. Author A.", "11.", "", "Author B. Kyiv, 2015.", "12. Author C."])
        result = parse_bibliography(lines)
        for n in [10, 11, 12]:
            self.assertIn(n, result)


class TestBug3YearNumbers(unittest.TestCase):
    def _L(self, texts):
        return [{"line": t, "page": 200} for t in texts]

    def test_year_continuation(self):
        lines = self._L(["2. Author // Journal. --", "2001. -- No 29. -- P. 2.", "3. Another."])
        result = parse_bibliography(lines)
        self.assertIn(2, result)
        self.assertNotIn(2001, result)

    def test_document_number(self):
        lines = self._L(["170. Decision No", "1882. -- Zh., 2007. -- 8 p.", "171. Approval."])
        result = parse_bibliography(lines)
        self.assertIn(170, result)
        self.assertNotIn(1882, result)
        self.assertIn("1882", result[170])


if __name__ == '__main__':
    unittest.main(verbosity=2)
