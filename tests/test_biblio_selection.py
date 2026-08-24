"""
Regressions for choosing WHICH heading starts the real bibliography,
and for dropping stray entry numbers.

Run: pytest tests/test_biblio_selection.py
"""
import unittest

from parser.bibliography import (
    MIN_BIBLIO_ENTRIES,
    BibliographyNotFoundError,
    parse_bibliography,
    split_zones,
)


def L(texts, page=1):
    return [{"line": t, "page": page} for t in texts]


def entries(n_from, n_to, prefix="Джерело"):
    return [f"{i}. {prefix} номер {i}." for i in range(n_from, n_to + 1)]


class TestHeaderSelection(unittest.TestCase):
    def test_english_references_in_abstract_does_not_win(self):
        """
        The failure seen on a real dissertation: a translated REFERENCES list
        after the Ukrainian one used to win purely by being last, leaving the
        real bibliography classified as body text.
        """
        doc = (L(["РОЗДІЛ 1", "Текст [1] і [2]."])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(1, 10))
               + L(["АНОТАЦІЯ", "Текст анотації"])
               + L(["REFERENCES"] + entries(1, 4, prefix="Dzherelo")))

        r = split_zones(doc)
        self.assertEqual(r.biblio_header_line, "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ")
        self.assertEqual(len(parse_bibliography(r.bibliography)), 10)

    def test_per_chapter_list_loses_to_the_main_one(self):
        doc = (L(["РОЗДІЛ 1", "Текст."])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ ДО РОЗДІЛУ 1"] + entries(1, 4))
               + L(["РОЗДІЛ 2", "Текст."])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(1, 30)))

        r = split_zones(doc)
        self.assertEqual(r.biblio_header_line, "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ")
        self.assertEqual(len(parse_bibliography(r.bibliography)), 30)

    def test_toc_entry_is_never_a_candidate(self):
        doc = (L(["ЗМІСТ", "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ ......... 185"])
               + L(["РОЗДІЛ 1", "Текст."])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(1, 8)))

        r = split_zones(doc)
        self.assertEqual(r.biblio_header_line, "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ")
        self.assertEqual(len(parse_bibliography(r.bibliography)), 8)
        # The TOC line stays in the body zone
        self.assertIn("......", " ".join(i["line"] for i in r.body))

    def test_repeated_running_header_picks_the_first(self):
        """
        In PDFs the list heading is often repeated as a running header on every
        page of the bibliography. The earliest occurrence must win, so that no
        entries are lost off the front of the list.
        """
        doc = (L(["РОЗДІЛ 1", "Текст."])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(1, 10))
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(11, 20))
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(21, 30)))

        r = split_zones(doc)
        parsed = parse_bibliography(r.bibliography)
        self.assertEqual(len(parsed), 30)
        self.assertIn(1, parsed)
        self.assertIn(30, parsed)

    def test_falls_back_to_last_candidate_when_nothing_qualifies(self):
        # Below MIN_BIBLIO_ENTRIES everywhere: keep the old behaviour so the UI
        # reports "no numbered sources found" rather than "list not found".
        doc = L(["Текст."]) + L(["СПИСОК ЛІТЕРАТУРИ", "1. Єдине джерело."])
        r = split_zones(doc)
        self.assertEqual(r.biblio_header_line, "СПИСОК ЛІТЕРАТУРИ")

    def test_no_header_at_all_raises(self):
        with self.assertRaises(BibliographyNotFoundError):
            split_zones(L(["Текст без бібліографії."]))

    def test_min_entries_threshold_is_respected(self):
        # A heading with exactly MIN_BIBLIO_ENTRIES qualifies
        doc = (L(["Текст."])
               + L(["СПИСОК ЛІТЕРАТУРИ"] + entries(1, MIN_BIBLIO_ENTRIES)))
        r = split_zones(doc)
        self.assertEqual(len(parse_bibliography(r.bibliography)), MIN_BIBLIO_ENTRIES)

    def test_page_of_chosen_header_is_reported(self):
        doc = (L(["Текст."], page=1)
               + L(["СПИСОК ЛІТЕРАТУРИ"] + entries(1, 5), page=42))
        self.assertEqual(split_zones(doc).biblio_start_page, 42)


class TestIsolatedOutliers(unittest.TestCase):
    """A stray '457.' from a wrapped URL used to become source #457."""

    def test_far_isolated_number_dropped(self):
        lines = L(entries(1, 30) + ["457. URL: https://example.com/vb457609"])
        parsed = parse_bibliography(lines)
        self.assertNotIn(457, parsed)
        self.assertEqual(len(parsed), 30)

    def test_small_gap_is_kept(self):
        # entry 15 lost by the PDF extractor — 16..30 must survive
        lines = L(entries(1, 14) + entries(16, 30))
        parsed = parse_bibliography(lines)
        self.assertIn(16, parsed)
        self.assertIn(30, parsed)

    def test_distant_block_with_neighbours_is_kept(self):
        lines = L(entries(1, 20) + entries(80, 95))
        parsed = parse_bibliography(lines)
        self.assertIn(80, parsed)
        self.assertIn(95, parsed)

    def test_short_lists_untouched(self):
        lines = L(["1. Перше.", "500. Друге."])
        parsed = parse_bibliography(lines)
        self.assertIn(500, parsed)

    def test_contiguous_list_untouched(self):
        lines = L(entries(1, 50))
        self.assertEqual(len(parse_bibliography(lines)), 50)


class TestContainmentRule(unittest.TestCase):
    """
    A TOC entry with neither dot leaders nor a trailing page number is not
    filtered out as a TOC line, so its zone swallows the body and the real
    list — and can score at least as high as the real heading. The
    containment rule resolves it: the narrower zone wins when it loses little.
    """

    def test_bare_toc_entry_loses_to_the_real_heading(self):
        doc = (L(["ЗМІСТ", "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"])
               + L(["РОЗДІЛ 1"]
                   + [f"{i}. Пункт переліку в тексті." for i in range(1, 9)])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(1, 8)))

        r = split_zones(doc)
        # The real heading is the last line before the entries, so the body
        # zone must contain the TOC line and the whole chapter.
        self.assertGreater(len(r.body), 5)
        self.assertEqual(len(parse_bibliography(r.bibliography)), 8)

    def test_wider_zone_wins_when_it_holds_clearly_more(self):
        # Containment must not fire when the inner candidate really is smaller:
        # a heading followed by 40 entries beats a later one with only 4.
        doc = (L(["Текст."])
               + L(["СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ"] + entries(1, 40))
               + L(["БІБЛІОГРАФІЯ"] + entries(1, 4)))

        r = split_zones(doc)
        self.assertEqual(r.biblio_header_line, "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ")
