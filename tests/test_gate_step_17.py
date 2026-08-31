"""Незалежний корпусний шлюз кроку 17 і схеми golden."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

import fitz
import pytest

from parser.searchdoc import PARSER_VERSION, parse_search_document
from search import ALGO_VERSION
from search.calques import DICT_VERSION
from search.engines import ENGINES
from search.query_builder import build_search_result, validate_query_parts
from search.types import Channel, QueryPartOrigin
from tools.audit_search_golden import (
    CORPUS_FILES,
    GOLDEN_SCHEMA_VERSION,
    build_golden_payload,
    collect_golden,
    render_json,
)
from tools.measure_calques import measure_document, measure_pdf_bytes


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "search_corpus_golden.json"
QUALITY_PATH = ROOT / "tests" / "fixtures" / "search_quality_review.json"
REVIEW_TOP_KEYS = {
    "schema_version",
    "source",
    "reviewer",
    "reviewed_on",
    "source_commit",
    "parser_version",
    "algo_version",
    "dictionary_version",
    "float_abs_tolerance",
    "documents",
}
MACHINE_TOP_KEYS = {
    "schema_version",
    "float_abs_tolerance",
    "parser_version",
    "algo_version",
    "dictionary_version",
    "documents",
}
DOCUMENT_KEYS = {
    "file",
    "sha256",
    "n_pages",
    "expected_body_pages",
    "extractable_body_pages",
    "coverage_ratio",
    "body_biblio_confidence",
    "author_words",
    "tier1_hits",
    "tier2_hits",
    "tier3_hits",
    "tier1_density",
    "excluded_zone_hits",
    "bibliography_total",
    "bibliography_expected",
    "bibliography_coverage",
    "bibliography_boundary_confidence",
    "language_ru",
    "language_uk",
    "language_mixed",
    "language_unknown",
    "ru_ratio",
    "show_ru_percentage",
    "language_reasons",
    "content_sections",
    "document_query_count",
    "query_ids",
    "generated_by_channel",
    "retained_primary_by_channel",
    "attributed_by_channel",
    "rejected_by_reason",
    "dedup_metrics",
    "signal_hits_by_channel",
    "signal_hits_by_rule",
    "warnings",
    "review_note",
}
MACHINE_DOCUMENT_KEYS = DOCUMENT_KEYS - {"review_note"}
SECTION_KEYS = {
    "section_id",
    "kind",
    "ordinal",
    "heading",
    "physical_pages",
    "author_words",
    "expected_body_pages",
    "extractable_body_pages",
    "coverage_ratio",
    "query_count",
    "query_ids",
    "shortfall",
}
SHORTFALL_KEYS = {
    "target",
    "actual",
    "author_words",
    "raw_sentence_count",
    "eligible_donor_count",
    "generated_window_count",
    "eligible_pre_dedup_count",
    "post_dedup_count",
    "coverage_ratio",
    "normative_sentence_ratio",
    "primary_reason",
    "contributing_reasons",
    "rejected_by_reason",
}
CHANNEL_NAMES = ["A", "B", "K", "L", "N", "T"]
FORBIDDEN_KEYS = {
    "raw_text",
    "full_text",
    "query_text",
    "donor_text",
    "pdf_anchor",
    "context",
    "parts",
}


def _paths() -> tuple[Path, ...]:
    return tuple(EXAMPLES / name for name in CORPUS_FILES)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _without_review_fields(golden: dict) -> dict:
    top = {
        key: value
        for key, value in golden.items()
        if key not in {"source", "reviewer", "reviewed_on", "source_commit"}
    }
    top["documents"] = [
        {key: value for key, value in item.items() if key != "review_note"}
        for item in golden["documents"]
    ]
    return top


def _assert_equal(left, right, path: str = "", tolerance: float = 1e-6) -> None:
    if isinstance(left, bool) or isinstance(right, bool):
        assert left == right, path
        return
    if isinstance(left, float) or isinstance(right, float):
        assert isinstance(left, (int, float)) and isinstance(right, (int, float)), path
        assert math.isfinite(float(left)) and math.isfinite(float(right)), path
        assert abs(float(left) - float(right)) <= tolerance, path
        return
    if isinstance(left, dict) or isinstance(right, dict):
        assert isinstance(left, dict) and isinstance(right, dict), path
        assert set(left) == set(right), path
        for key in left:
            _assert_equal(left[key], right[key], f"{path}.{key}", tolerance)
        return
    if isinstance(left, list) or isinstance(right, list):
        assert isinstance(left, list) and isinstance(right, list), path
        assert len(left) == len(right), path
        for index, (item_left, item_right) in enumerate(zip(left, right)):
            _assert_equal(item_left, item_right, f"{path}[{index}]", tolerance)
        return
    assert left == right, path


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


@pytest.fixture(scope="module")
def golden() -> dict:
    return _load_json(GOLDEN_PATH)


@pytest.fixture(scope="module")
def quality_review() -> dict:
    return _load_json(QUALITY_PATH)


@pytest.fixture(scope="module")
def corpus():
    return collect_golden(_paths())


def test_gate_golden_schema_and_manual_review(golden: dict) -> None:
    assert set(golden) == REVIEW_TOP_KEYS
    assert golden["schema_version"] == GOLDEN_SCHEMA_VERSION == 1
    assert golden["source"].strip()
    assert golden["reviewer"].strip()
    assert date.fromisoformat(golden["reviewed_on"])
    assert golden["source_commit"] == "510be88"
    assert golden["float_abs_tolerance"] == 0.000001
    assert golden["parser_version"] == PARSER_VERSION
    assert golden["algo_version"] == ALGO_VERSION
    assert golden["dictionary_version"] == DICT_VERSION
    assert [item["file"] for item in golden["documents"]] == list(CORPUS_FILES)
    assert len(golden["documents"]) == 9
    assert len({item["review_note"] for item in golden["documents"]}) == 9
    for item in golden["documents"]:
        assert set(item) == DOCUMENT_KEYS
        assert item["review_note"].strip()
        for section in item["content_sections"]:
            assert set(section) == SECTION_KEYS
            if section["shortfall"] is not None:
                assert set(section["shortfall"]) == SHORTFALL_KEYS


def test_gate_versions_and_shas_match_step_16(quality_review: dict, golden: dict) -> None:
    for key in ("parser_version", "algo_version", "dictionary_version"):
        assert golden[key] == quality_review[key]
    expected = {item["file"]: item["sha256"] for item in quality_review["documents"]}
    assert set(expected) == set(CORPUS_FILES)
    for item in golden["documents"]:
        path = EXAMPLES / item["file"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert item["sha256"] == expected[item["file"]] == actual


def test_gate_candidate_reproduces_golden(corpus, golden: dict) -> None:
    candidate = build_golden_payload(corpus)
    _assert_equal(
        _without_review_fields(golden),
        candidate,
        tolerance=golden["float_abs_tolerance"],
    )
    assert set(candidate) == MACHINE_TOP_KEYS
    for item in candidate["documents"]:
        assert set(item) == MACHINE_DOCUMENT_KEYS


def test_gate_render_is_deterministic_and_finite(corpus) -> None:
    payload = build_golden_payload(corpus)
    assert render_json(payload) == render_json(payload)
    assert json.loads(render_json(payload)) == payload
    with pytest.raises(ValueError):
        render_json({"not_finite": float("nan")})


def test_gate_tool_is_read_only_and_has_explicit_corpus() -> None:
    source = (ROOT / "tools" / "audit_search_golden.py").read_text(encoding="utf-8")
    assert "search_corpus_golden" not in source
    assert not re.search(r"\b(?:write_text|write_bytes)\s*\(", source)
    assert not re.search(r"\bopen\s*\([^\n]*[\"']w", source)
    assert "--output" not in source
    assert "CORPUS_FILES" in source
    assert len(CORPUS_FILES) == 9
    assert all(name.casefold().endswith(".pdf") for name in CORPUS_FILES)


def test_gate_fixture_is_small_and_contains_no_full_text(golden: dict) -> None:
    assert GOLDEN_PATH.stat().st_size < 250_000
    assert not (FORBIDDEN_KEYS & set(_all_keys(golden)))


def test_gate_corpus_has_text_queries_and_section_quotas(corpus, golden: dict) -> None:
    high_medium = 0
    for item, golden_item in zip(corpus, golden["documents"]):
        document = item.document
        result = item.result
        assert item.measurement.author_words >= 1
        assert len(result.queries) >= 1
        assert any(section["query_count"] == 10 for section in golden_item["content_sections"])
        high_medium += golden_item["body_biblio_confidence"] in {"high", "medium"}
        for section in golden_item["content_sections"]:
            query_count = section["query_count"]
            shortfall = section["shortfall"]
            assert query_count <= 12
            if query_count >= 10:
                assert shortfall is None
            else:
                assert query_count < 10
                assert shortfall is not None
                assert shortfall["actual"] == query_count
                assert shortfall["target"] == 10
    assert high_medium >= 8


def test_gate_runtime_queries_have_provenance_and_stable_order(corpus, golden: dict) -> None:
    for item, golden_item in zip(corpus, golden["documents"]):
        result = item.result
        query_ids = [query.query_id for query in result.queries]
        assert query_ids == golden_item["query_ids"]
        assert len(query_ids) == len(set(query_ids))
        signal_ids = {
            hit.evidence_id for hit in result.signal_hits if hit.channel == Channel.K
        }
        for query in result.queries:
            assert query.primary_channel != Channel.D
            assert all(channel != Channel.D for channel in query.attributed_channels)
            assert query.query_text.strip()
            assert query.parts
            assert query.pdf_anchor.strip()
            assert validate_query_parts(query.parts, query.query_text)
            if query.primary_channel == Channel.K and query.subtype in {"K2", "K3"}:
                assert query.evidence_ids
                assert set(query.evidence_ids) <= signal_ids
                if query.subtype == "K2":
                    assert any(part.origin == QueryPartOrigin.CALQUE_RULE for part in query.parts)
                else:
                    assert any(part.origin == QueryPartOrigin.SURNAME_TRANSLITERATION for part in query.parts)
                    assert any(
                        part.origin == QueryPartOrigin.SYSTEM_LITERAL
                        and part.origin_id == "definition_literal"
                        for part in query.parts
                    )
        assert build_search_result(item.document) == result


def test_gate_shortfalls_and_runtime_section_counts_match(corpus, golden: dict) -> None:
    for item, golden_item in zip(corpus, golden["documents"]):
        runtime_shortfalls = {shortfall.section_id: shortfall for shortfall in item.result.shortfalls}
        runtime_counts = Counter(query.section_id for query in item.result.queries)
        for section in golden_item["content_sections"]:
            section_id = section["section_id"]
            assert section["query_count"] == runtime_counts[section_id]
            assert section["query_ids"] == [
                query.query_id for query in item.result.queries if query.section_id == section_id
            ]
            shortfall = section["shortfall"]
            if shortfall is None:
                assert section["query_count"] >= 10
                assert section_id not in runtime_shortfalls
            else:
                assert section_id in runtime_shortfalls
                runtime = runtime_shortfalls[section_id]
                assert shortfall["actual"] == runtime.actual == section["query_count"]
                assert shortfall["primary_reason"] == runtime.primary_reason.value
                assert shortfall["contributing_reasons"] == [
                    reason.value for reason in runtime.contributing_reasons
                ]
                assert shortfall["rejected_by_reason"] == [
                    [name, count] for name, count in runtime.rejected_by_reason
                ]


def test_gate_measurement_parity_and_calque_fields(corpus, golden: dict) -> None:
    for item, golden_item in zip(corpus, golden["documents"]):
        measurement = item.measurement
        metrics = item.result.calque_metrics
        assert metrics.author_words == measurement.author_words == golden_item["author_words"]
        assert metrics.tier1_hits == measurement.tier1_hits == golden_item["tier1_hits"]
        assert metrics.tier2_hits == measurement.tier2_hits == golden_item["tier2_hits"]
        assert metrics.tier3_hits == measurement.tier3_hits == golden_item["tier3_hits"]
        assert round(metrics.tier1_density, 6) == golden_item["tier1_density"]
        assert golden_item["n_pages"] == measurement.n_pages
        assert golden_item["expected_body_pages"] == item.document.expected_body_pages
        assert golden_item["extractable_body_pages"] == item.document.extractable_body_pages
        assert golden_item["coverage_ratio"] == round(item.document.coverage_ratio, 6)
        assert golden_item["excluded_zone_hits"] == sorted(
            [[zone.value, count] for zone, count in metrics.excluded_zone_hits]
        )
        assert golden_item["sha256"] == measurement.document_sha256
        assert golden_item["body_biblio_confidence"] == measurement.bibliography_boundary_confidence
        assert golden_item["bibliography_total"] == measurement.bibliography_total
        assert golden_item["bibliography_expected"] == measurement.bibliography_expected
        assert golden_item["bibliography_coverage"] == (
            round(measurement.bibliography_coverage, 6)
            if measurement.bibliography_coverage is not None
            else None
        )
        for key in ("language_ru", "language_uk", "language_mixed", "language_unknown", "show_ru_percentage"):
            assert golden_item[key] == getattr(measurement, key)
        assert golden_item["ru_ratio"] == (
            round(measurement.ru_ratio, 6) if measurement.ru_ratio is not None else None
        )
        assert golden_item["language_reasons"] == list(measurement.language_reasons)


def test_gate_measure_document_matches_bytes_adapter() -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "ВСТУП\nАвтор аналізує наукове питання.")
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "СПИСОК ЛІТЕРАТУРИ\n1. Автор А. Теорія права. Київ, 2020.")
    data = pdf.tobytes()
    pdf.close()
    document = parse_search_document(data)
    assert measure_pdf_bytes(data, name="synthetic.pdf") == measure_document(
        document, name="synthetic.pdf"
    )


def test_gate_all_counters_keep_channels_reasons_and_dedup(corpus, golden: dict) -> None:
    for item, golden_item in zip(corpus, golden["documents"]):
        result = item.result
        for key in (
            "generated_by_channel",
            "retained_primary_by_channel",
            "attributed_by_channel",
            "signal_hits_by_channel",
        ):
            assert [name for name, _ in golden_item[key]] == CHANNEL_NAMES
        assert sum(count for _, count in golden_item["signal_hits_by_channel"]) == len(result.signal_hits)
        assert sum(count for _, count in golden_item["signal_hits_by_rule"]) == len(result.signal_hits)
        assert all(channel != "D" for channel, _ in golden_item["generated_by_channel"])
        assert set(golden_item["dedup_metrics"]) == {
            "input_count",
            "component_count",
            "removed_count",
            "merged_channel_attributions",
        }
        assert golden_item["rejected_by_reason"] == sorted(golden_item["rejected_by_reason"])
        assert golden_item["warnings"] == list(result.warnings)


def test_gate_engine_review_keeps_single_verified_prefill(quality_review: dict) -> None:
    decisions = quality_review["engine_review"]
    assert len(decisions) == len(ENGINES) == 7
    prefills = [item for item in decisions if item["active_prefill"]]
    assert [item["code"] for item in prefills] == ["google"]
    assert all(
        item["decision"] == "home_only"
        for item in decisions
        if item["code"] != "google"
    )
