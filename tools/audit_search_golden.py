#!/usr/bin/env python3
"""
Read-only candidate для golden-снимка якості пошуку.

Інструмент використовує один спільний parser/query-прохід на PDF, додає
метрики вже побудованого `SearchDocument` і друкує або машинний payload, або
людинозчитувану зведену картину для ручного перегляду. Файлів не створює.
Специфікація — пакет кроку 17 та PLAN_SEARCH.md, §20.2.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
else:
    ROOT = Path(__file__).resolve().parents[1]

with redirect_stdout(sys.stderr):
    from parser.searchdoc import PARSER_VERSION
    from search import ALGO_VERSION
    from search.calques import DICT_VERSION, density_band
    from search.query_builder import build_search_result
    from search.types import (
        CONTENT_SECTION_KINDS,
        Channel,
        SearchDocument,
        SearchResult,
        SectionShortfall,
    )
    from tools.audit_search_quality import CorpusItem, collect_corpus
    from tools.measure_calques import FileMeasurement, measure_document


GOLDEN_SCHEMA_VERSION = 1
FLOAT_ROUND_DIGITS = 6
CORPUS_FILES: tuple[str, ...] = (
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

_CHANNELS = tuple(channel for channel in Channel if channel != Channel.D)


@dataclass(frozen=True)
class GoldenCorpusItem:
    """Усі результати одного спільного проходу для одного PDF."""

    path: Path
    document: SearchDocument
    result: SearchResult
    measurement: FileMeasurement


def _round_float(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("float metric is not finite")
    return round(value, FLOAT_ROUND_DIGITS)


def _pairs(values) -> list[list[object]]:
    """Серіалізує іменований лічильник стабільним алфавітним порядком."""

    normalized = [
        (getattr(key, "value", str(key)), int(value))
        for key, value in values
    ]
    return [[key, value] for key, value in sorted(normalized)]


def _all_channel_pairs(values) -> list[list[object]]:
    counts = {
        key.value if isinstance(key, Channel) else str(key): int(value)
        for key, value in values
    }
    return [[channel.value, counts.get(channel.value, 0)] for channel in sorted(_CHANNELS, key=lambda item: item.value)]


def _signal_rule_key(rule_id: str) -> str:
    """Згортає лише динамічні форми, не втрачаючи їхні загальні лічильники."""

    if rule_id.startswith("A.stem."):
        return "A.stem"
    if rule_id.startswith("A.phrase."):
        return "A.phrase"
    if rule_id.startswith("T."):
        return "T.rare_form"
    return rule_id


def _shortfall_payload(shortfall: SectionShortfall) -> dict:
    return {
        "target": shortfall.target,
        "actual": shortfall.actual,
        "author_words": shortfall.author_words,
        "raw_sentence_count": shortfall.raw_sentence_count,
        "eligible_donor_count": shortfall.eligible_donor_count,
        "generated_window_count": shortfall.generated_window_count,
        "eligible_pre_dedup_count": shortfall.eligible_pre_dedup_count,
        "post_dedup_count": shortfall.post_dedup_count,
        "coverage_ratio": _round_float(shortfall.coverage_ratio),
        "normative_sentence_ratio": _round_float(shortfall.normative_sentence_ratio),
        "primary_reason": shortfall.primary_reason.value,
        "contributing_reasons": [reason.value for reason in shortfall.contributing_reasons],
        "rejected_by_reason": _pairs(shortfall.rejected_by_reason),
    }


def _section_payload(item: GoldenCorpusItem, section) -> dict:
    queries = tuple(query for query in item.result.queries if query.section_id == section.section_id)
    shortfall = next(
        (candidate for candidate in item.result.shortfalls if candidate.section_id == section.section_id),
        None,
    )
    return {
        "section_id": section.section_id,
        "kind": section.kind.value,
        "ordinal": section.ordinal,
        "heading": section.heading,
        "physical_pages": list(section.physical_pages),
        "author_words": section.author_words,
        "expected_body_pages": section.expected_body_pages,
        "extractable_body_pages": section.extractable_body_pages,
        "coverage_ratio": _round_float(section.coverage_ratio),
        "query_count": len(queries),
        "query_ids": [query.query_id for query in queries],
        "shortfall": _shortfall_payload(shortfall) if shortfall is not None else None,
    }


def _document_payload(item: GoldenCorpusItem) -> dict:
    document = item.document
    result = item.result
    measurement = item.measurement
    metrics = result.calque_metrics
    signal_by_channel = Counter(hit.channel.value for hit in result.signal_hits)
    signal_by_rule = Counter(_signal_rule_key(hit.rule_id) for hit in result.signal_hits)
    return {
        "file": item.path.name,
        "sha256": document.document_sha256,
        "n_pages": document.n_pages,
        "expected_body_pages": document.expected_body_pages,
        "extractable_body_pages": document.extractable_body_pages,
        "coverage_ratio": _round_float(document.coverage_ratio),
        "body_biblio_confidence": document.body_biblio_confidence.value,
        "author_words": metrics.author_words,
        "tier1_hits": metrics.tier1_hits,
        "tier2_hits": metrics.tier2_hits,
        "tier3_hits": metrics.tier3_hits,
        "tier1_density": _round_float(metrics.tier1_density),
        "excluded_zone_hits": _pairs(metrics.excluded_zone_hits),
        "bibliography_total": measurement.bibliography_total,
        "bibliography_expected": measurement.bibliography_expected,
        "bibliography_coverage": (
            _round_float(measurement.bibliography_coverage)
            if measurement.bibliography_coverage is not None
            else None
        ),
        "bibliography_boundary_confidence": measurement.bibliography_boundary_confidence,
        "language_ru": measurement.language_ru,
        "language_uk": measurement.language_uk,
        "language_mixed": measurement.language_mixed,
        "language_unknown": measurement.language_unknown,
        "ru_ratio": _round_float(measurement.ru_ratio) if measurement.ru_ratio is not None else None,
        "show_ru_percentage": measurement.show_ru_percentage,
        "language_reasons": list(measurement.language_reasons),
        "content_sections": [
            _section_payload(item, section)
            for section in document.sections
            if section.kind in CONTENT_SECTION_KINDS
        ],
        "document_query_count": len(result.queries),
        "query_ids": [query.query_id for query in result.queries],
        "generated_by_channel": _all_channel_pairs(result.candidate_metrics.generated_by_channel),
        "retained_primary_by_channel": _all_channel_pairs(result.candidate_metrics.retained_primary_by_channel),
        "attributed_by_channel": _all_channel_pairs(result.candidate_metrics.attributed_by_channel),
        "rejected_by_reason": _pairs(result.candidate_metrics.rejected_by_reason),
        "dedup_metrics": {
            "input_count": result.dedup_metrics.input_count,
            "component_count": result.dedup_metrics.component_count,
            "removed_count": result.dedup_metrics.removed_count,
            "merged_channel_attributions": result.dedup_metrics.merged_channel_attributions,
        },
        "signal_hits_by_channel": [
            [channel.value, signal_by_channel.get(channel.value, 0)]
            for channel in sorted(_CHANNELS, key=lambda item: item.value)
        ],
        "signal_hits_by_rule": [[name, count] for name, count in sorted(signal_by_rule.items())],
        "warnings": list(result.warnings),
    }


def collect_golden(
    paths: tuple[Path, ...],
    progress: Callable[[str], None] | None = None,
) -> tuple[GoldenCorpusItem, ...]:
    """Будує document/result/measurement рівно одним parser/query-проходом."""

    collected = collect_corpus(paths, progress=progress)
    return collect_golden_from_corpus(paths, collected, progress=progress)


def collect_golden_from_corpus(
    paths: tuple[Path, ...],
    collected: tuple[CorpusItem, ...],
    progress: Callable[[str], None] | None = None,
) -> tuple[GoldenCorpusItem, ...]:
    """Додає вимірювання до готового corpus без повторного query-проходу."""

    by_path = {str(item.path.resolve()).casefold(): item for item in collected}
    ordered: list[GoldenCorpusItem] = []
    for path in paths:
        corpus_item: CorpusItem | None = by_path.get(str(path.resolve()).casefold())
        if corpus_item is None:
            raise ValueError(f"Не знайдено результат для {path}")
        if progress is not None:
            progress(f"{path.name}: metrics")
        measurement = measure_document(corpus_item.document, name=path.name)
        ordered.append(GoldenCorpusItem(path, corpus_item.document, corpus_item.result, measurement))
    return tuple(ordered)


def build_golden_payload(corpus: tuple[GoldenCorpusItem, ...]) -> dict:
    """Повертає лише відтворювані поля без ручних review-полів."""

    return {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "float_abs_tolerance": 0.000001,
        "parser_version": PARSER_VERSION,
        "algo_version": ALGO_VERSION,
        "dictionary_version": DICT_VERSION,
        "documents": [_document_payload(item) for item in corpus],
    }


def render_json(payload: dict) -> str:
    """Рендерить payload детерміновано та забороняє NaN/Infinity."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )


def _format_pairs(values: list[list[object]]) -> str:
    return ", ".join(f"{name}={value}" for name, value in values) or "—"


def render_summary(corpus: tuple[GoldenCorpusItem, ...]) -> str:
    """Виводить повну коротку картину для ручного golden-review."""

    lines = [
        f"PARSER_VERSION={PARSER_VERSION}",
        f"ALGO_VERSION={ALGO_VERSION}",
        f"DICT_VERSION={DICT_VERSION}",
        f"documents={len(corpus)}",
    ]
    for item in corpus:
        document = item.document
        result = item.result
        measurement = item.measurement
        sections = [
            f"{section.kind.value}:{section.heading!r}="
            f"{sum(query.section_id == section.section_id for query in result.queries)}"
            for section in document.sections
            if section.kind in CONTENT_SECTION_KINDS
        ]
        shortfalls = [
            (
                f"{shortfall.section_id}: actual={shortfall.actual}/{shortfall.target}, "
                f"primary={shortfall.primary_reason.value}, "
                f"contributing={[reason.value for reason in shortfall.contributing_reasons]}, "
                f"rejected={_format_pairs(_pairs(shortfall.rejected_by_reason))}"
            )
            for shortfall in result.shortfalls
        ]
        primary = Counter(query.primary_channel.value for query in result.queries)
        attributed = Counter(
            channel.value
            for query in result.queries
            for channel in query.attributed_channels
            if channel != Channel.D
        )
        lines.extend(
            [
                "",
                f"FILE {item.path.name}",
                f"  sha256={document.document_sha256}",
                f"  pages={document.n_pages} body_pages={document.expected_body_pages}/{document.extractable_body_pages} "
                f"coverage={document.coverage_ratio:.6f} confidence={document.body_biblio_confidence.value}",
                f"  author_words={measurement.author_words} tier1/2/3={measurement.tier1_hits}/{measurement.tier2_hits}/{measurement.tier3_hits} "
                f"density={measurement.tier1_density:.6f} band={measurement.density_band} "
                f"excluded={_format_pairs(_pairs(measurement.excluded_zone_hits))}",
                f"  sections={'; '.join(sections) or '—'}",
                f"  queries total={len(result.queries)} primary={_format_pairs(_pairs(primary.items()))} "
                f"attributed={_format_pairs(_pairs(attributed.items()))}",
                f"  shortfalls={len(shortfalls)}" + (" | " + " | ".join(shortfalls) if shortfalls else ""),
                f"  bibliography={measurement.bibliography_total}/{measurement.bibliography_expected} "
                f"coverage={measurement.bibliography_coverage} boundary={measurement.bibliography_boundary_confidence}",
                f"  language=RU:{measurement.language_ru} UK:{measurement.language_uk} "
                f"MIXED:{measurement.language_mixed} UNKNOWN:{measurement.language_unknown} "
                f"ru_ratio={measurement.ru_ratio} show_ru={measurement.show_ru_percentage} "
                f"reasons={list(measurement.language_reasons) or '—'}",
                f"  dedup={result.dedup_metrics} rejected={_format_pairs(_pairs(result.candidate_metrics.rejected_by_reason))}",
                f"  signal_channels={_format_pairs(_all_channel_pairs(result.candidate_metrics.generated_by_channel))} "
                f"warnings={list(result.warnings) or '—'} D_queries={primary.get('D', 0)}",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs=9, type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    progress = (
        (lambda message: print(message, file=sys.stderr, flush=True))
        if args.progress
        else None
    )
    try:
        with redirect_stdout(sys.stderr):
            corpus = collect_golden(tuple(args.files), progress=progress)
    except (OSError, ValueError) as exc:
        print(f"Помилка golden-аудиту: {exc}", file=sys.stderr)
        return 1
    payload = build_golden_payload(corpus)
    if args.as_json:
        print(render_json(payload))
    else:
        print(render_summary(corpus))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
