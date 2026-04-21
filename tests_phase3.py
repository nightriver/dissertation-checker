"""
tests_phase3.py  —  Unit tests for paragraph analyzer.
Run: python tests_phase3.py
"""

import unittest
from parser.paragraph_analyzer import (
    _is_section_trigger,
    extract_content_bounds,
    _count_sentences,
    END_SECTION_HEADERS,
    CHAPTER_HEADERS,
    ContentBoundsNotFoundError,
)

def L(texts, page=1):
    return [{"line": t, "page": page} for t in texts]

class TestExtractContentBounds(unittest.TestCase):
    def test_find_first_chapter_after_intro(self):
        doc = L(["ВСТУП", "РОЗДІЛ 1", "Текст", "ВИСНОВКИ"])
        start, end = extract_content_bounds(doc, None)
        self.assertEqual(start, 1)

    def test_stop_at_conclusions(self):
        doc = L(["РОЗДІЛ 1", "Текст", "ВИСНОВКИ", "Текст висновків"])
        start, end = extract_content_bounds(doc, None)
        self.assertEqual(end, 1)

    def test_stop_at_bibliography(self):
        doc = L(["РОЗДІЛ 1", "Текст", "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ", "1. Джерело"])
        start, end = extract_content_bounds(doc, None)
        self.assertEqual(end, 1)

    def test_ignore_not_exact_chapter(self):
        doc = L(["ВСТУП", "Розглянемо розділ 2 детальніше.", "РОЗДІЛ 1", "ВИСНОВКИ"])
        start, end = extract_content_bounds(doc, None)
        self.assertEqual(start, 2)

    def test_no_chapter_raises(self):
        doc = L(["ВСТУП", "ВИСНОВКИ"])
        with self.assertRaises(ContentBoundsNotFoundError):
            extract_content_bounds(doc, None)

    def test_ignore_toc_lines(self):
        doc = L(["ВСТУП", "РОЗДІЛ 1. ОГЛЯД ЛІТЕРАТУРИ ...... 12", "РОЗДІЛ 1", "ВИСНОВКИ"])
        start, end = extract_content_bounds(doc, None)
        self.assertEqual(start, 2)

class TestSectionTrigger(unittest.TestCase):
    def test_exact_conclusions(self):
        self.assertTrue(_is_section_trigger("ВИСНОВКИ", END_SECTION_HEADERS, exact=True))
        self.assertFalse(_is_section_trigger("ВИСНОВКИ ДО РОЗДІЛУ 1", END_SECTION_HEADERS, exact=True))
        self.assertFalse(_is_section_trigger("Висновки до першого розділу", END_SECTION_HEADERS, exact=True))
        self.assertTrue(_is_section_trigger("ВИСНОВКИ.", END_SECTION_HEADERS, exact=True))
        self.assertTrue(_is_section_trigger("ЗАГАЛЬНІ ВИСНОВКИ:", END_SECTION_HEADERS, exact=True))

class TestCountSentences(unittest.TestCase):
    def test_no_double_count_initials(self):
        self.assertEqual(_count_sentences("В. В. розробив метод."), 1)

    def test_two_sentences(self):
        self.assertEqual(_count_sentences("Метод розроблено. Результати наведено."), 2)

    def test_digits_and_quotes(self):
        self.assertEqual(_count_sentences("...дорівнює 42. Наступний крок..."), 2)
        self.assertEqual(_count_sentences('...назвав "успіхом". Після цього...'), 2)

    def test_abbreviations(self):
        self.assertEqual(_count_sentences("табл. 1.1. Аналіз показує..."), 1)
        self.assertEqual(_count_sentences("рис. 2.3. Схема відображає..."), 1)

if __name__ == "__main__":
    unittest.main(verbosity=2)
