"""
Sanity tests for search/types.py — the data contract PLAN_SEARCH.md §4
requires every later module to share. These do not test any analysis logic
(none exists yet); they lock down the shape and a few cross-checks between
tables that are easy to silently desync (e.g. ZONE_PRIORITY vs TextZone).
"""
import dataclasses
import unittest

from search import ALGO_VERSION
from search.types import (
    CONTENT_SECTION_KINDS,
    ZONE_PRIORITY,
    CalqueMetrics,
    CandidateMetrics,
    CharOrigin,
    Channel,
    Confidence,
    DedupMetrics,
    EngineSpec,
    Language,
    NormalizedText,
    PageInfo,
    PageTextState,
    QueryPart,
    QueryPartOrigin,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchQuery,
    SearchResult,
    SearchToken,
    SectionInfo,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
    SectionShortfall,
    SentenceDonor,
    ShortfallReason,
    SignalHit,
    SourceSpan,
    TextZone,
    ZoneSpan,
)


class TestAlgoVersion(unittest.TestCase):
    def test_is_a_non_empty_string_constant(self):
        self.assertIsInstance(ALGO_VERSION, str)
        self.assertTrue(ALGO_VERSION)


class TestZonePriority(unittest.TestCase):
    def test_lists_every_text_zone_exactly_once(self):
        self.assertEqual(set(ZONE_PRIORITY), set(TextZone))
        self.assertEqual(len(ZONE_PRIORITY), len(set(ZONE_PRIORITY)))

    def test_author_text_is_lowest_priority(self):
        # §4.1: only the intersecting span changes zone; the rest of the
        # paragraph stays AUTHOR_TEXT, so it must lose every tie.
        self.assertEqual(ZONE_PRIORITY[-1], TextZone.AUTHOR_TEXT)

    def test_header_footer_is_highest_priority(self):
        self.assertEqual(ZONE_PRIORITY[0], TextZone.HEADER_FOOTER)


class TestContentSectionKinds(unittest.TestCase):
    def test_only_intro_chapter_conclusions_count_as_content(self):
        self.assertEqual(
            CONTENT_SECTION_KINDS,
            {SectionKind.INTRO, SectionKind.CHAPTER, SectionKind.CONCLUSIONS},
        )

    def test_title_toc_abstract_biblio_appendix_are_excluded(self):
        excluded = {
            SectionKind.TITLE, SectionKind.TOC, SectionKind.ABSTRACT,
            SectionKind.BIBLIO, SectionKind.APPENDIX, SectionKind.UNKNOWN,
        }
        self.assertTrue(excluded.isdisjoint(CONTENT_SECTION_KINDS))


class TestDataclassesAreFrozen(unittest.TestCase):
    """Every public struct must be immutable — nothing here is a plain dict."""

    def test_all_listed_structs_are_frozen_dataclasses(self):
        structs = [
            RawSpan, SourceSpan, CharOrigin, NormalizedText, ZoneSpan, SearchToken,
            SearchBlock, PageInfo, SectionInfo, SectionOverride, SentenceDonor,
            SignalHit, QueryPart, SearchQuery, SectionShortfall, SearchDocument,
            CalqueMetrics, CandidateMetrics, DedupMetrics, SearchResult, EngineSpec,
        ]
        for cls in structs:
            with self.subTest(cls=cls.__name__):
                self.assertTrue(dataclasses.is_dataclass(cls))
                self.assertTrue(cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen")

    def test_frozen_instance_rejects_mutation(self):
        span = RawSpan(block_id="b1", physical_page=1, raw_start=0, raw_end=3)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            span.raw_start = 1  # type: ignore[misc]


class TestNormalizedTextInvariant(unittest.TestCase):
    def test_origins_length_matches_text_length_by_construction(self):
        text = "ab"
        origins = (CharOrigin(0, 1), CharOrigin(1, 2))
        nt = NormalizedText(text=text, origins=origins)
        self.assertEqual(len(nt.text), len(nt.origins))


class TestSourceSpanAssembly(unittest.TestCase):
    def test_parts_survive_construction_in_order(self):
        parts = (
            RawSpan("b1", 1, 0, 5),
            RawSpan("b1", 1, 10, 15),
        )
        span = SourceSpan(parts=parts)
        self.assertEqual(span.parts, parts)


class TestEnumsCoverPlanValues(unittest.TestCase):
    def test_channel_includes_reserved_d(self):
        self.assertIn(Channel.D, Channel)
        self.assertEqual({c.value for c in Channel}, {"A", "N", "B", "K", "T", "L", "D"})

    def test_shortfall_reason_has_all_eight_reasons(self):
        self.assertEqual(len(list(ShortfallReason)), 8)

    def test_confidence_has_three_levels(self):
        self.assertEqual({c.value for c in Confidence}, {"high", "medium", "low"})

    def test_language_has_four_values(self):
        self.assertEqual({v.value for v in Language}, {"ru", "uk", "mixed", "unknown"})

    def test_page_text_state_has_four_values(self):
        self.assertEqual(
            {v.value for v in PageTextState},
            {"text_ok", "low_text", "no_text", "expected_sparse"},
        )

    def test_query_part_origin_has_six_values(self):
        self.assertEqual(len(list(QueryPartOrigin)), 6)

    def test_section_override_action_has_two_values(self):
        self.assertEqual(
            {v.value for v in SectionOverrideAction},
            {"set_kind", "exclude_heading"},
        )


class TestEngineSpecShape(unittest.TestCase):
    def test_can_represent_an_unverified_fallback_only_engine(self):
        spec = EngineSpec(
            code="yandex",
            label="Яндекс",
            channels=frozenset({Channel.K}),
            home_url="https://yandex.ru",
            query_url_template=None,
            max_query_chars=400,
            warning="може бути недоступний із поточної мережі",
            verified_on=None,
            active_prefill=False,
        )
        self.assertFalse(spec.active_prefill)
        self.assertIsNone(spec.query_url_template)
