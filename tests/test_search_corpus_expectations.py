"""
Validates tests/fixtures/search_corpus_expectations.json — the manually
observed facts about the nine PLAN_SEARCH.md corpus PDFs (§20.2, step 2 of
§22). No search/* heuristic exists yet; these tests only check:

  1. the fixture's own shape is well-formed and internally consistent;
  2. the "observed" facts still match the actual files in examples/ — sha256
     and page count are cheap to re-derive and catch a stale fixture or a
     silently swapped example file.

They intentionally do NOT re-implement or approve any recognition heuristic:
that is search/*'s job in later steps. `author_text_min_words` and
`bibliography_entry_count_observed` were derived with a generous safety
margin from the existing (pre-PLAN_SEARCH) parser.extractor/parser.bibliography
tooling — see tools used to build the fixture in the commit that added it —
and are meant purely as a floor, not a golden value.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import fitz

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_corpus_expectations.json"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

EXPECTED_FILES = {
    "Работа май-docx-2.pdf",
    "Гончарова-Парфьонова_дисертація.pdf",
    "DISSERTAZIYA.doc.pdf",
    "diss-doc.pdf",
    "diskor-корецька.pdf",
    "diser.pdf",
    "dis2005_bayar_kandidat.PDF",
    "dis.doc-КОЦЮБА.pdf",
    "Dis-doc-марченко.pdf",
}


def _load():
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


class TestFixtureShape(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(FIXTURE_PATH.is_file())

    def test_schema_version_is_one(self):
        self.assertEqual(_load()["schema_version"], 1)

    def test_covers_exactly_the_nine_plan_pdfs(self):
        payload = _load()
        names = {doc["file"] for doc in payload["documents"]}
        self.assertEqual(names, EXPECTED_FILES)
        self.assertEqual(len(payload["documents"]), 9)

    def test_no_duplicate_files(self):
        payload = _load()
        names = [doc["file"] for doc in payload["documents"]]
        self.assertEqual(len(names), len(set(names)))


class TestDocumentRecordShape(unittest.TestCase):
    """Every record has the fields §20.2 asks for, with sane types."""

    def setUp(self):
        self.payload = _load()

    def test_every_record_has_required_top_level_keys(self):
        required = {
            "file", "sha256", "expected_pages", "headings",
            "author_text_min_words", "bibliography_entry_count_observed",
            "zone_examples", "citation_example",
        }
        for doc in self.payload["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertTrue(required.issubset(doc.keys()))

    def test_sha256_is_64_hex_chars(self):
        for doc in self.payload["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertRegex(doc["sha256"], r"^[0-9a-f]{64}$")

    def test_headings_are_ordered_intro_before_chapter_before_conclusions_before_biblio(self):
        for doc in self.payload["documents"]:
            h = doc["headings"]
            with self.subTest(doc=doc["file"]):
                self.assertLess(h["intro_page"], h["first_chapter_page"])
                self.assertLess(h["first_chapter_page"], h["conclusions_page"])
                self.assertLess(h["conclusions_page"], h["bibliography_page"])
                self.assertLessEqual(h["bibliography_page"], doc["expected_pages"])
                self.assertGreaterEqual(h["intro_page"], 1)

    def test_author_text_min_words_is_a_positive_lower_bound(self):
        for doc in self.payload["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertGreater(doc["author_text_min_words"], 0)

    def test_bibliography_entry_count_observed_is_plausible(self):
        # A real dissertation has tens to a few hundred sources; this is a
        # sanity band, not a precision requirement (§20.2 disclaimer above).
        for doc in self.payload["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertGreater(doc["bibliography_entry_count_observed"], 20)
                self.assertLess(doc["bibliography_entry_count_observed"], 1000)

    def test_zone_examples_have_both_keys(self):
        for doc in self.payload["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertIn("quoted_text", doc["zone_examples"])
                self.assertIn("author_text", doc["zone_examples"])

    def test_present_fragment_examples_store_a_hash_not_raw_text(self):
        # PLAN_SEARCH.md §20.2: "Фикстура хранит только короткие хешированные
        # фрагменты" — never the dissertation's own sentences verbatim.
        for doc in self.payload["documents"]:
            for key, example in doc["zone_examples"].items():
                if example is None:
                    continue
                with self.subTest(doc=doc["file"], zone=key):
                    self.assertRegex(example["fragment_sha256"], r"^[0-9a-f]{16}$")
                    self.assertGreater(example["page"], 0)

    def test_citation_example_when_present_links_a_real_bibliography_ordinal(self):
        for doc in self.payload["documents"]:
            example = doc["citation_example"]
            if example is None:
                continue
            with self.subTest(doc=doc["file"]):
                self.assertGreater(example["source_ordinal"], 0)
                self.assertRegex(example["bracket_text_sha256"], r"^[0-9a-f]{16}$")
                self.assertRegex(example["bibliography_entry_sha256"], r"^[0-9a-f]{16}$")


class TestFixtureMatchesActualFiles(unittest.TestCase):
    """
    Re-derives the two cheapest facts (hash, page count) directly from
    examples/ so a swapped or edited corpus file is caught immediately,
    without waiting for parser.searchdoc to exist.
    """

    def setUp(self):
        self.payload = _load()

    def test_sha256_matches_file_on_disk(self):
        for doc in self.payload["documents"]:
            path = EXAMPLES_DIR / doc["file"]
            with self.subTest(doc=doc["file"]):
                self.assertTrue(path.is_file(), f"missing corpus file: {path}")
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, doc["sha256"])

    def test_page_count_matches_file_on_disk(self):
        for doc in self.payload["documents"]:
            path = EXAMPLES_DIR / doc["file"]
            with self.subTest(doc=doc["file"]):
                pdf = fitz.open(str(path))
                try:
                    self.assertEqual(pdf.page_count, doc["expected_pages"])
                finally:
                    pdf.close()


if __name__ == "__main__":
    unittest.main()
