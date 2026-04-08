"""
tests_phase1.py  —  Regression + unit tests for parser package.
Run: python tests_phase1.py
No dependencies beyond stdlib.
"""
import sys, types, unittest

# ── stubs ────────────────────────────────────────────────────────────────────
fitz_mod = types.ModuleType("fitz")
class _FD:
    def __iter__(self): return iter([])
    def close(self): pass
fitz_mod.open = lambda **kw: _FD()
sys.modules["fitz"] = fitz_mod

docx_mod = types.ModuleType("docx")
class _DD:
    paragraphs = []
docx_mod.Document = lambda f: _DD()
sys.modules["docx"] = docx_mod

# ── imports ──────────────────────────────────────────────────────────────────
from parser.bibliography import (
    split_zones, split_zones_manual, parse_bibliography,
    BibliographyNotFoundError,
)
from parser.citations import find_citations, compare, _expand_bracket

# ── helpers ──────────────────────────────────────────────────────────────────
def L(texts, page=1):
    return [{"line": t, "page": page} for t in texts]


def fc(body_lines) -> set:
    """fc() returns dict[int,str]; fc() returns just the set of keys."""
    return set(find_citations(body_lines).keys())


# ═══════════════════════════════════════════════════════════════════════════
#  bibliography.py
# ═══════════════════════════════════════════════════════════════════════════

class TestSplitZones(unittest.TestCase):
    def _doc(self):
        return (L(["Вступ.", "Розділ 1.", "Текст [1]. Ще [2; 3]."])
              + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
                   "1. Автор А. Київ, 2020.", "2. Автор Б. Харків, 2021."])
              + L(["ДОДАТКИ", "Додаток А."]))

    def test_body_len(self):       self.assertEqual(len(split_zones(self._doc()).body), 3)
    def test_biblio_len(self):     self.assertEqual(len(split_zones(self._doc()).bibliography), 3)
    def test_after_len(self):      self.assertEqual(len(split_zones(self._doc()).after), 2)
    def test_header_captured(self):
        self.assertEqual(split_zones(self._doc()).biblio_header_line,
                         "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ")
    def test_not_found_raises(self):
        with self.assertRaises(BibliographyNotFoundError):
            split_zones(L(["Текст без бібліографії."]))
    def test_no_stop_word(self):
        doc = L(["Вступ."]) + L(["СПИСОК ЛІТЕРАТУРИ", "1. Джерело."])
        r = split_zones(doc)
        self.assertEqual(len(r.bibliography), 2)
        self.assertEqual(len(r.after), 0)
    def test_last_header_wins(self):
        doc = (L(["Ранній СПИСОК ЛІТЕРАТУРИ фейк"])
             + L(["Між."])
             + L(["СПИСОК ЛІТЕРАТУРИ"])
             + L(["1. Джерело."]))
        self.assertEqual(len(split_zones(doc).body), 2)
    def test_manual(self):
        doc = L(["Вступ."]) + L(["Мій розділ", "1. Джерело."])
        r = split_zones_manual(doc, "Мій розділ")
        self.assertEqual(len(r.body), 1)
    def test_manual_page_filter(self):
        lines = ([{"line": "Вступ.", "page": 1}]
               + [{"line": "Мій розділ", "page": 5}]
               + [{"line": "1. Джерело.", "page": 5}])
        r = split_zones_manual(lines, "Мій розділ", start_page=5)
        self.assertEqual(len(r.body), 1)


class TestParseBibliography(unittest.TestCase):
    def test_simple(self):
        r = parse_bibliography(L(["1. Автор А. Київ.", "2. Автор Б. Харків."]))
        self.assertEqual(set(r), {1, 2})
    def test_multiline_entry(self):
        r = parse_bibliography(L(["1. Назва довга", "продовження.", "2. Коротка."]))
        self.assertIn("продовження", r[1])
        self.assertEqual(len(r), 2)
    def test_bracket_format(self):
        r = parse_bibliography(L(["[1] Автор А.", "[2] Автор Б."]))
        self.assertIn(1, r); self.assertIn(2, r)
    def test_mixed_format(self):
        self.assertEqual(len(parse_bibliography(L(["1. Перше.", "[2] Друге."]))), 2)
    def test_empty(self):
        self.assertEqual(parse_bibliography(L(["СПИСОК ДЖЕРЕЛ", ""])), {})
    def test_year_not_entry(self):
        r = parse_bibliography(L(["1. Назва видання",
                                   "2005. URL: https://zakon.rada.gov.ua",
                                   "2. Інше."]))
        self.assertNotIn(2005, r)
        self.assertIn("2005", r[1])
    # Bug 5 — number on own line (PyMuPDF artefact)
    def test_number_own_line(self):
        r = parse_bibliography(L(["11.", "", "Байкулатова В. Київ, 2010.",
                                   "12.", "", "Байцар Р. Харків."]))
        self.assertIn(11, r); self.assertIn(12, r)
        self.assertIn("Байкулатова", r[11])
    # Bug 3b — tab separator
    def test_tab_separator(self):
        r = parse_bibliography(L(["1.	Назва з табом.", "2.		Друга."]))
        self.assertIn(1, r); self.assertIn(2, r)
        self.assertIn("Назва", r[1])
    # Year guard
    def test_year_1882(self):
        r = parse_bibliography(L(["170. Decision No", "1882. Zh., 2007.", "171. Next."]))
        self.assertNotIn(1882, r)
        self.assertIn("1882", r[170])
    def test_999_ok(self):
        r = parse_bibliography(L(["998. First.", "999. Second."]))
        self.assertIn(998, r); self.assertIn(999, r)
    def test_1000_rejected(self):
        self.assertNotIn(1000, parse_bibliography(L(["1000. Not a source."])))


# ═══════════════════════════════════════════════════════════════════════════
#  citations.py — _expand_bracket
# ═══════════════════════════════════════════════════════════════════════════

class TestExpandBracket(unittest.TestCase):
    def eq(self, content, expected):
        self.assertEqual(_expand_bracket(content), expected)

    # Basic
    def test_single(self):          self.eq("1", {1})
    def test_semicolons(self):      self.eq("1; 3; 7", {1, 3, 7})
    def test_range_hyphen(self):    self.eq("15-18", {15, 16, 17, 18})
    def test_range_endash(self):    self.eq("15–18", {15, 16, 17, 18})
    def test_range_u2212(self):     self.eq("15−18", {15, 16, 17, 18})

    # Semicolon mode: comma = page separator
    def test_semi_with_page(self):     self.eq("1, 15; 3, 20; 7", {1, 3, 7})
    def test_semi_complex(self):
        self.eq("1, 15; 3; 5, 20-25; 40, 20-25, 100; 60, 145, 150", {1, 3, 5, 40, 60})

    # Comma mode: comma = source separator
    def test_comma_mode_two(self):     self.eq("69,85", {69, 85})
    def test_comma_mode_three(self):   self.eq("3, 7, 11", {3, 7, 11})
    def test_comma_mode_range(self):   self.eq("1, 5, 16-17", {1, 5, 16, 17})

    # с. page marker (Bug 5 new dissertation)
    def test_cyrillic_page_semi(self):   self.eq("89, с. 11; 98", {89, 98})
    def test_cyrillic_page_comma(self):  self.eq("250, с. 11-19", {250})
    def test_cyrillic_multi(self):
        self.eq("31, с. 70; 37; 55; 59, с. 26; 85, с. 3", {31, 37, 55, 59, 85})


# ═══════════════════════════════════════════════════════════════════════════
#  citations.py — find_citations
# ═══════════════════════════════════════════════════════════════════════════

class TestFindCitations(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(fc(L(["[1; 3; 7]"])), {1, 3, 7})
    def test_no_citations(self):
        self.assertEqual(fc(L(["Текст."])), set())
    def test_multiline_two(self):
        body = L(["[1]."]) + L(["[2; 3]."])
        self.assertEqual(fc(body), {1, 2, 3})

    # Bug 1 — multiline brackets (any depth)
    def test_multiline_bracket_2lines(self):
        body = [{"line": "[124; 149;", "page": 1}, {"line": "179].", "page": 1}]
        self.assertLessEqual({124, 149, 179}, fc(body))
    def test_multiline_bracket_3lines(self):
        body = [{"line": "[ 8; 16; 19; 57; 68; 84; 89, с. 11; 98;", "page": 1},
                {"line": "100; 164; 189].", "page": 1}]
        self.assertLessEqual({8, 16, 19, 57, 68, 84, 89, 98, 100, 164, 189},
                              fc(body))

    # Bug 2 — U+2212 range
    def test_u2212(self):
        self.assertEqual(fc(L(["[209−210]"])), {209, 210})

    # Bug 3 — Wingdings PUA brackets
    def test_wingdings(self):
        result = fc(L(["94; 108"]))
        self.assertIn(94, result); self.assertIn(108, result)
    def test_wingdings_range(self):
        self.assertEqual(fc(L(["107−108"])), {107, 108})
    def test_wingdings_multiline(self):
        body = [{"line": "94; 97;", "page": 46},
                {"line": "107", "page": 46}]
        result = fc(body)
        for n in [94, 97, 107]: self.assertIn(n, result)

    # Bug 4 — comma-only source lists
    def test_comma_sources(self):
        self.assertEqual(fc(L(["[3, 7, 11]"])), {3, 7, 11})
    def test_comma_four(self):
        self.assertEqual(fc(L(["[1, 5, 10, 12]"])), {1, 5, 10, 12})
    def test_semicolon_mode_unchanged(self):
        self.assertEqual(fc(L(["[1, 15; 3; 5, 20-25]"])), {1, 3, 5})

    # Bug 5 — с. page marker
    def test_cyrillic_page_marker(self):
        body = L(["[95; 113; 170; 178; 235, с. 17]"])
        result = fc(body)
        self.assertLessEqual({95, 113, 170, 178, 235}, result)
    def test_latin_c_page_marker(self):
        # Latin c. (not Cyrillic с.) — used in some dissertations
        for bracket, expected in [
            ("[123, c. 35]",   {123}),
            ("[15, c. 489]",   {15}),
            ("[16, c. 50-53]", {16}),
            ("[26, c. 158]",   {26}),
        ]:
            with self.subTest(bracket=bracket):
                self.assertEqual(
                    set(find_citations(L([bracket])).keys()), expected
                )

    def test_large_with_cyrillic(self):
        body = L(["[3; 22; 23; 24; 31, с. 70; 37; 55; 59, с. 26; 75; 79; 85, с. 3; 93; 95; 114 ]"])
        result = fc(body)
        self.assertLessEqual({3, 22, 23, 24, 31, 37, 55, 59, 75, 79, 85, 93, 95, 114}, result)
    def test_250_comma_no_semi(self):
        result = fc(L(["[250, с. 11-19]"]))
        self.assertIn(250, result)


# ═══════════════════════════════════════════════════════════════════════════
#  compare()
# ═══════════════════════════════════════════════════════════════════════════

class TestCompare(unittest.TestCase):
    def test_all_used(self):
        r = compare({1:"A",2:"B"}, {1,2})
        self.assertEqual(r["orphans"], set())
    def test_orphans(self):
        self.assertEqual(compare({1:"A",2:"B",3:"C"}, {1,3})["orphans"], {2})
    def test_phantom(self):
        self.assertEqual(compare({1:"A"}, {1,99})["phantom"], {99})
    def test_all_orphans(self):
        r = compare({1:"A",2:"B"}, set())
        self.assertEqual(r["orphans"], {1,2}); self.assertEqual(r["used"], set())



class TestBug6EntryValidation(unittest.TestCase):
    def _L(self, t): return [{"line": x, "page": 200} for x in t]

    def test_date_09_rejected(self):
        lines = self._L(["6. URL: example.com/(дата", "09.2019).", "7. Next."])
        r = parse_bibliography(lines)
        self.assertIn(6, r); self.assertIn(7, r)
        self.assertNotIn(9, r)
        self.assertIn("09.2019", r[6])

    def test_spec_code_rejected(self):
        lines = self._L(["18. Thesis. nauk:", "12.00.07. University.", "19. Next."])
        r = parse_bibliography(lines)
        self.assertIn(18, r); self.assertIn(19, r); self.assertNotIn(12, r)
        self.assertIn("12.00.07", r[18])

    def test_date_01_rejected(self):
        lines = self._L(["5. URL: zakon.gov.ua. (data", "01.09.2019).", "6. Next."])
        r = parse_bibliography(lines)
        self.assertIn(5, r); self.assertIn(6, r); self.assertNotIn(1, r)

    def test_no_space_entry_accepted(self):
        lines = self._L(["10.Адміністративна юстиція.", "11.Адміністративне право."])
        r = parse_bibliography(lines)
        self.assertIn(10, r); self.assertIn(11, r)
        self.assertIn("Адміністративна", r[10])



class TestFindCitationsReturnsBracket(unittest.TestCase):
    """find_citations() must return dict[int, str] with the original bracket."""

    def test_returns_dict(self):
        result = find_citations(L(["[1; 3; 7]"]))
        self.assertIsInstance(result, dict)

    def test_bracket_string_preserved(self):
        result = find_citations(L(["text [49; 51; 55; 60; 63-65] end."]))
        self.assertIn(55, result)
        self.assertIn("49", result[55])   # bracket contains the original content

    def test_first_occurrence_kept(self):
        # Source 3 appears in two brackets — must keep first
        result = find_citations(L(["[1; 3] then [3; 5]."]))
        self.assertIn(3, result)
        self.assertIn("1", result[3])     # first bracket contains 1

    def test_cyrillic_page_bracket_preserved(self):
        result = find_citations(L(["[89, с. 11; 98]"]))
        self.assertIn(89, result)
        self.assertIn("89", result[89])



class TestMissingCommaBeforePage(unittest.TestCase):
    """Author forgot comma before page marker: [30 с. 334-344] instead of [30, с. 334-344]."""

    def test_expand_no_comma_cyrillic(self):
        # "30 с. 334-344" — no comma, Cyrillic с
        self.assertEqual(_expand_bracket("30 с. 334-344"), {30})

    def test_expand_no_comma_latin(self):
        # "30 c. 334-344" — no comma, Latin c
        self.assertEqual(_expand_bracket("30 c. 334-344"), {30})

    def test_expand_range_no_comma(self):
        # "25-27 с. 41-55" — range source, no comma before page
        self.assertEqual(_expand_bracket("25-27 с. 41-55"), {25, 26, 27})

    def test_mixed_with_and_without_comma(self):
        # [21, с. 31-33; 30 с. 334-344; 12, с. 31-55]
        self.assertEqual(
            _expand_bracket("21, с. 31-33; 30 с. 334-344; 12, с. 31-55"),
            {21, 30, 12}
        )

    def test_mixed_range_no_comma(self):
        # [201 с. 38–39; 25-27 с. 41-55]
        self.assertEqual(
            _expand_bracket("201 с. 38 – 39; 25-27 с. 41-55"),
            {201, 25, 26, 27}
        )

    def test_comma_present_still_works(self):
        # Existing behaviour unchanged when comma is present
        self.assertEqual(_expand_bracket("89, с. 11; 98"), {89, 98})
        self.assertEqual(_expand_bracket("250, с. 11-19"), {250})

    def test_find_citations_no_comma(self):
        body = L(["[21, с. 31-33; 30 с. 334-344; 12, с. 31-55]."])
        result = fc(body)
        self.assertEqual(result, {21, 30, 12})

    def test_find_citations_range_no_comma(self):
        body = L(["[201 с. 38 – 39; 25-27 с. 41-55 ]."])
        result = fc(body)
        self.assertEqual(result, {201, 25, 26, 27})


if __name__ == "__main__":
    unittest.main(verbosity=2)
