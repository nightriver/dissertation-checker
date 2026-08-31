"""Acceptance gate for PLAN_SEARCH.md §20.3 and implementation step 16."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pytest

from parser.searchdoc import PARSER_VERSION
from parser.searchdoc import _Line, _merge_baseline_pieces
from search import ALGO_VERSION
from search.calques import DICT_VERSION
from search.engines import ENGINES
from tools.audit_search_quality import (
    MAX_TIER1_PER_FILE,
    QUALITY_CHANNELS,
    QUERY_SAMPLE_PER_CHANNEL,
    TIER1_SAMPLE_SIZE,
    _tier1_candidates,
    render_payload,
    select_query_sample,
    select_tier1_sample,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "search_quality_review.json"
EXAMPLES = ROOT / "examples"
CORPUS_FILES = (
    "Работа май-docx-2.pdf",
    "Гончарова-Парфьонова_дисертація.pdf",
    "DISSERTAZIYA.doc.pdf",
    "diss-doc.pdf",
    "diskor-корецька.pdf",
    "diser.pdf",
    "dis2005_bayar_kandidat.PDF",
    "dis.doc-КОЦЮБА.pdf",
    "Dis-doc-марченко.pdf",
)


@pytest.fixture(scope="module")
def review() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reproduced(canonical_corpus) -> dict:
    corpus = canonical_corpus
    tier1 = select_tier1_sample(corpus)
    queries = select_query_sample(corpus)
    return {
        "corpus": corpus,
        "tier1": tier1,
        "queries": queries,
        "payload": render_payload(corpus, tier1, queries),
    }


def _without_manual_fields(records: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in record.items() if key not in {"label", "reason"}}
        for record in records
    ]


def test_gate_review_has_versions_documents_and_exact_sample_sizes(review: dict) -> None:
    assert review["schema_version"] == 1
    assert review["reviewer"].strip()
    assert review["reviewed_on"] == "2026-08-30"
    assert review["parser_version"] == PARSER_VERSION
    assert review["algo_version"] == ALGO_VERSION
    assert review["dictionary_version"] == DICT_VERSION
    assert len(review["documents"]) == len(CORPUS_FILES) == 9
    assert len(review["tier1_sample"]) == TIER1_SAMPLE_SIZE == 100
    assert len(review["query_sample"]) == len(QUALITY_CHANNELS) * QUERY_SAMPLE_PER_CHANNEL == 50


@pytest.mark.corpus
def test_gate_document_hashes_and_samples_reproduce_byte_for_byte(
    review: dict, reproduced: dict
) -> None:
    payload = reproduced["payload"]
    assert review["documents"] == payload["documents"]
    assert _without_manual_fields(review["tier1_sample"]) == payload["tier1_sample"]
    assert _without_manual_fields(review["query_sample"]) == payload["query_sample"]


@pytest.mark.corpus
def test_gate_tier1_covers_corpus_files_rules_and_respects_cap(
    review: dict, reproduced: dict
) -> None:
    records = review["tier1_sample"]
    assert {record["file"] for record in records} == set(CORPUS_FILES)
    counts = Counter(record["file"] for record in records)
    assert max(counts.values()) <= MAX_TIER1_PER_FILE
    met_rules = {candidate.rule_id for candidate in _tier1_candidates(reproduced["corpus"])}
    assert {record["rule_id"] for record in records} == met_rules
    assert len({record["sample_id"] for record in records}) == len(records)


def test_gate_tier1_manual_precision_passes_overall_and_per_rule(review: dict) -> None:
    records = review["tier1_sample"]
    assert {record["label"] for record in records} <= {"true", "false", "uncertain"}
    assert all(record["reason"].strip() for record in records)
    assert sum(record["label"] == "true" for record in records) / len(records) >= 0.90

    by_rule: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_rule[record["rule_id"]].append(record)
    for rule_id, examples in by_rule.items():
        if len(examples) >= 5:
            precision = sum(item["label"] == "true" for item in examples) / len(examples)
            assert precision >= 0.80, rule_id


def test_gate_query_manual_quality_passes_every_channel_threshold(review: dict) -> None:
    records = review["query_sample"]
    assert {record["label"] for record in records} <= {"useful", "false", "uncertain"}
    assert all(record["reason"].strip() for record in records)
    assert len({record["sample_id"] for record in records}) == len(records)
    assert len({record["query_id"] for record in records}) == len(records)
    assert len({record["file"] for record in records}) >= 5

    thresholds = {"A": 0.90, "N": 0.90, "B": 0.90, "T": 0.80, "L": 0.80}
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_channel[record["channel"]].append(record)
    assert set(by_channel) == set(thresholds)
    for channel, threshold in thresholds.items():
        examples = by_channel[channel]
        assert len(examples) == QUERY_SAMPLE_PER_CHANNEL
        useful = sum(item["label"] == "useful" for item in examples) / len(examples)
        assert useful >= threshold, channel


def test_gate_queries_have_provenance_anchors_hashes_and_no_inventions(review: dict) -> None:
    records = review["query_sample"]
    assert all(record["provenance_valid"] is True for record in records)
    assert all(record["anchor_found"] is True for record in records)
    assert all(len(record["query_sha256"]) == 16 for record in records)
    assert all(len(record["anchor_sha256"]) == 16 for record in records)
    assert review["invented_content_words"] == 0


def test_gate_fixture_contains_no_full_dissertation_text(review: dict) -> None:
    forbidden = {"context", "query_text", "donor_text", "pdf_anchor", "raw_text", "full_text"}
    for group in (review["tier1_sample"], review["query_sample"]):
        for record in group:
            assert forbidden.isdisjoint(record)
    assert FIXTURE_PATH.stat().st_size < 100_000


def test_gate_every_selected_physical_page_was_rendered_and_reviewed(review: dict) -> None:
    pages = {
        (record["file"], record["page"])
        for key in ("tier1_sample", "query_sample")
        for record in review[key]
    }
    assert review["visual_review"]["unique_pages"] == len(pages) == 139
    assert review["visual_review"]["rendering_defects"] == 0
    assert "Poppler" in review["visual_review"]["method"]


def test_gate_engine_decisions_match_registry_and_only_google_prefills(review: dict) -> None:
    decisions = review["engine_review"]
    assert len(decisions) == len(ENGINES) == 7
    assert [item["code"] for item in decisions] == [engine.code for engine in ENGINES]
    assert [item["home_url"] for item in decisions] == [engine.home_url for engine in ENGINES]
    assert all(item["verified_on"] == review["reviewed_on"] for item in decisions)
    assert all(item["reason"].strip() for item in decisions)

    prefills = [item for item in decisions if item["active_prefill"]]
    assert [item["code"] for item in prefills] == ["google"]
    assert prefills[0]["decision"] == "prefill"
    assert all(
        item["decision"] == "home_only"
        for item in decisions
        if item["code"] != "google"
    )
    assert ENGINES[0].verified_on == date.fromisoformat(review["reviewed_on"])


def test_gate_audit_tool_cannot_read_or_overwrite_manual_fixture() -> None:
    source = (ROOT / "tools" / "audit_search_quality.py").read_text(encoding="utf-8")
    assert "search_quality_review" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source


def test_gate_same_baseline_fragments_follow_horizontal_geometry() -> None:
    body = _Line(
        text="вперше запропоновано модель",
        x0=155.85,
        y0=118.57,
        x1=487.58,
        y1=136.78,
        size=12.0,
        bold=False,
        physical_page=30,
    )
    marker = _Line(
        text="‒",
        x0=127.60,
        y0=121.42,
        x1=134.60,
        y1=135.73,
        size=12.0,
        bold=False,
        physical_page=30,
    )

    merged = _merge_baseline_pieces([body, marker])

    assert len(merged) == 1
    assert merged[0].text == "‒ вперше запропоновано модель"
    assert merged[0].x0 == marker.x0
