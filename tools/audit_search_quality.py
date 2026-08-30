#!/usr/bin/env python3
"""
Детермінована read-only вибірка ручної якості пошуку.

Інструмент реалізує PLAN_SEARCH.md, §20.3 (крок 16): будує спільний
виробничий pipeline для дев'яти PDF, відбирає 100 tier-1 збігів і по десять
pre-selection кандидатів A/N/B/T/L. JSON навмисно не містить повного тексту праць;
`--verbose` друкує короткий контекст лише для ручного перегляду й не створює
файлів. Ручну fixture цей модуль не читає і не перезаписує.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz

# Прямий запуск `python tools/audit_search_quality.py` додає `tools/`, а не
# корінь репозиторію. Це лише bootstrap імпортів, не продуктова логіка.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.searchdoc import PARSER_VERSION, parse_search_document
from search import ALGO_VERSION
from search.calques import DICT_VERSION, collapse_components, find_calques
from search.markers import find_channel_n_signals
from search.query_builder import (
    _word_frequencies,
    build_search_result_with_candidates,
    build_source_channel_query,
    validate_query_parts,
)
from search.types import (
    CONTENT_SECTION_KINDS,
    Channel,
    SearchDocument,
    SearchQuery,
    SearchResult,
    TextZone,
)


TIER1_SAMPLE_SIZE = 100
QUERY_SAMPLE_PER_CHANNEL = 10
MAX_TIER1_PER_FILE = 25
QUALITY_CHANNELS = (Channel.A, Channel.N, Channel.B, Channel.T, Channel.L)


class QualitySampleError(RuntimeError):
    """Корпус не може виконати числовий контракт §20.3."""


@dataclass(frozen=True)
class CorpusItem:
    path: Path
    document: SearchDocument
    result: SearchResult
    candidates: tuple[SearchQuery, ...]


@dataclass(frozen=True)
class Tier1Candidate:
    sample_id: str
    file: str
    document_sha256: str
    physical_page: int
    rule_id: str
    fragment_sha256: str
    context: str


@dataclass(frozen=True)
class QueryCandidate:
    sample_id: str
    file: str
    document_sha256: str
    query_id: str
    channel: Channel
    primary_channel: Channel
    selection_stage: int
    selected_final: bool
    physical_page: int
    query_sha256: str
    anchor_sha256: str
    provenance_valid: bool
    anchor_found: bool
    query_text: str
    donor_text: str
    pdf_anchor: str


def _digest(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _rank(*parts: object) -> str:
    return hashlib.sha256(
        "|".join((DICT_VERSION, *(str(part) for part in parts))).encode("utf-8")
    ).hexdigest()


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parser_page_text(page) -> str:
    """Той самий sort/spans-шар PyMuPDF, з якого parser створює донори."""
    text_dict = page.get_text("dict", sort=True)
    lines: list[str] = []
    for raw_block in text_dict.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        for raw_line in raw_block.get("lines", []):
            text = "".join(
                span.get("text", "")
                for span in raw_line.get("spans", [])
                if span.get("text", "").strip()
            ).strip()
            if text:
                lines.append(text)
    return _compact(" ".join(lines))


def collect_corpus(
    paths: tuple[Path, ...],
    progress: Callable[[str], None] | None = None,
) -> tuple[CorpusItem, ...]:
    """Один виробничий parse/query прогін на файл у стабільному порядку."""
    items: list[CorpusItem] = []
    ordered = sorted((item.resolve() for item in paths), key=lambda item: item.name.casefold())
    for index, path in enumerate(ordered, start=1):
        if progress is not None:
            progress(f"[{index}/{len(ordered)}] {path.name}: parser")
        data = path.read_bytes()
        document = parse_search_document(data)
        if progress is not None:
            progress(f"[{index}/{len(ordered)}] {path.name}: queries")
        result, candidates = build_search_result_with_candidates(document)
        items.append(CorpusItem(path, document, result, candidates))
        if progress is not None:
            progress(
                f"[{index}/{len(ordered)}] {path.name}: done, "
                f"{len(document.sentences)} sentences, {len(result.queries)} queries"
            )
    return tuple(items)


def _tier1_candidates(corpus: tuple[CorpusItem, ...]) -> tuple[Tier1Candidate, ...]:
    candidates: list[Tier1Candidate] = []
    for item in corpus:
        for block in item.document.blocks:
            hits = tuple(
                hit for hit in collapse_components(find_calques(block))
                if hit.tier == 1 and hit.zone == TextZone.AUTHOR_TEXT
            )
            for hit in hits:
                start = max(0, hit.raw_start - 90)
                end = min(len(block.raw_text), hit.raw_end + 90)
                context = _compact(block.raw_text[start:end])
                identity = (
                    item.path.name,
                    block.physical_page,
                    hit.rule_id,
                    block.block_id,
                    hit.raw_start,
                    hit.raw_end,
                )
                candidates.append(Tier1Candidate(
                    sample_id=_digest("|".join(map(str, identity)), 24),
                    file=item.path.name,
                    document_sha256=item.document.document_sha256,
                    physical_page=block.physical_page,
                    rule_id=hit.rule_id,
                    fragment_sha256=_digest(context),
                    context=context,
                ))
    return tuple(sorted(candidates, key=lambda item: _rank(item.sample_id)))


def select_tier1_sample(
    corpus: tuple[CorpusItem, ...], size: int = TIER1_SAMPLE_SIZE
) -> tuple[Tier1Candidate, ...]:
    """Покрити файли/правила, потім пропорційно добрати з усіх hit-кандидатів."""
    candidates = _tier1_candidates(corpus)
    if len(candidates) < size:
        raise QualitySampleError(f"tier1 candidates: {len(candidates)} < {size}")

    selected: list[Tier1Candidate] = []
    selected_ids: set[str] = set()
    per_file: Counter[str] = Counter()

    def add(candidate: Tier1Candidate) -> bool:
        if candidate.sample_id in selected_ids or per_file[candidate.file] >= MAX_TIER1_PER_FILE:
            return False
        selected.append(candidate)
        selected_ids.add(candidate.sample_id)
        per_file[candidate.file] += 1
        return True

    for file_name in sorted({item.path.name for item in corpus}, key=str.casefold):
        candidate = next((item for item in candidates if item.file == file_name), None)
        if candidate is None or not add(candidate):
            raise QualitySampleError(f"no tier1 candidate for {file_name}")

    for rule_id in sorted({item.rule_id for item in candidates}):
        if any(item.rule_id == rule_id for item in selected):
            continue
        candidate = next(
            (
                item for item in candidates
                if item.rule_id == rule_id and per_file[item.file] < MAX_TIER1_PER_FILE
            ),
            None,
        )
        if candidate is None or not add(candidate):
            raise QualitySampleError(f"cannot cover tier1 rule {rule_id}")

    for candidate in candidates:
        if len(selected) >= size:
            break
        add(candidate)
    if len(selected) != size:
        raise QualitySampleError(f"selected tier1: {len(selected)} != {size}")
    return tuple(sorted(selected, key=lambda item: (item.file.casefold(), item.physical_page, item.sample_id)))


def _query_candidates(corpus: tuple[CorpusItem, ...]) -> tuple[QueryCandidate, ...]:
    candidates: list[QueryCandidate] = []
    for item in corpus:
        relevant = tuple(
            query for query in item.candidates
            if any(channel in QUALITY_CHANNELS for channel in query.attributed_channels)
        )
        final_ids = {query.query_id for query in item.result.queries}
        page_texts: dict[int, str] = {}
        with fitz.open(item.path) as pdf:
            for physical_page in sorted({query.physical_page for query in relevant}):
                if 1 <= physical_page <= len(pdf):
                    page_texts[physical_page] = _parser_page_text(
                        pdf[physical_page - 1]
                    )
        for query in relevant:
            for channel in query.attributed_channels:
                if channel not in QUALITY_CHANNELS:
                    continue
                identity = (item.path.name, query.query_id, channel.value)
                candidates.append(QueryCandidate(
                    sample_id=_digest("|".join(identity), 24),
                    file=item.path.name,
                    document_sha256=item.document.document_sha256,
                    query_id=query.query_id,
                    channel=channel,
                    primary_channel=query.primary_channel,
                    selection_stage=query.selection_stage,
                    selected_final=query.query_id in final_ids,
                    physical_page=query.physical_page,
                    query_sha256=_digest(query.query_text),
                    anchor_sha256=_digest(query.pdf_anchor),
                    provenance_valid=validate_query_parts(query.parts, query.query_text),
                    anchor_found=(
                        _compact(query.pdf_anchor) in page_texts.get(query.physical_page, "")
                    ),
                    query_text=query.query_text,
                    donor_text=query.donor_text,
                    pdf_anchor=query.pdf_anchor,
                ))
    return tuple(sorted(candidates, key=lambda item: _rank(item.channel.value, item.sample_id)))


def select_query_sample(
    corpus: tuple[CorpusItem, ...], per_channel: int = QUERY_SAMPLE_PER_CHANNEL
) -> tuple[QueryCandidate, ...]:
    """Для кожного каналу спершу різні PDF, потім стабільний добір до десяти."""
    candidates = _query_candidates(corpus)
    selected_by_channel: dict[Channel, list[QueryCandidate]] = {}
    used_queries: set[tuple[str, str]] = set()
    pools = {
        channel: [item for item in candidates if item.channel == channel]
        for channel in QUALITY_CHANNELS
    }
    for channel in sorted(QUALITY_CHANNELS, key=lambda item: (len(pools[item]), item.value)):
        pool = [
            item for item in pools[channel]
            if (item.file, item.query_id) not in used_queries
        ]
        if len(pool) < per_channel:
            raise QualitySampleError(
                f"query candidates {channel.value}: {len(pool)} < {per_channel}"
            )
        channel_selected: list[QueryCandidate] = []
        seen_files: set[str] = set()
        for candidate in pool:
            if candidate.file in seen_files:
                continue
            channel_selected.append(candidate)
            seen_files.add(candidate.file)
            if len(channel_selected) >= per_channel:
                break
        for candidate in pool:
            if len(channel_selected) >= per_channel:
                break
            if candidate not in channel_selected:
                channel_selected.append(candidate)
        selected_by_channel[channel] = channel_selected
        used_queries.update((item.file, item.query_id) for item in channel_selected)
    return tuple(
        item
        for channel in QUALITY_CHANNELS
        for item in selected_by_channel[channel]
    )


def render_payload(
    corpus: tuple[CorpusItem, ...],
    tier1: tuple[Tier1Candidate, ...],
    queries: tuple[QueryCandidate, ...],
) -> dict:
    """Редагований вручну manifest без повного тексту дисертацій."""
    return {
        "schema_version": 1,
        "parser_version": PARSER_VERSION,
        "algo_version": ALGO_VERSION,
        "dictionary_version": DICT_VERSION,
        "documents": [
            {"file": item.path.name, "sha256": item.document.document_sha256}
            for item in corpus
        ],
        "tier1_sample": [
            {
                "sample_id": item.sample_id,
                "file": item.file,
                "document_sha256": item.document_sha256,
                "page": item.physical_page,
                "rule_id": item.rule_id,
                "fragment_sha256": item.fragment_sha256,
            }
            for item in tier1
        ],
        "query_sample": [
            {
                "sample_id": item.sample_id,
                "file": item.file,
                "document_sha256": item.document_sha256,
                "query_id": item.query_id,
                "channel": item.channel.value,
                "primary_channel": item.primary_channel.value,
                "selection_stage": item.selection_stage,
                "selected_final": item.selected_final,
                "page": item.physical_page,
                "query_sha256": item.query_sha256,
                "anchor_sha256": item.anchor_sha256,
                "provenance_valid": item.provenance_valid,
                "anchor_found": item.anchor_found,
            }
            for item in queries
        ],
    }


def _summary(corpus, tier1, queries) -> str:
    tier_files = Counter(item.file for item in tier1)
    tier_rules = Counter(item.rule_id for item in tier1)
    query_channels = Counter(item.channel.value for item in queries)
    query_files = Counter(item.file for item in queries)
    lines = [
        f"PARSER_VERSION={PARSER_VERSION}",
        f"DICT_VERSION={DICT_VERSION}",
        f"ALGO_VERSION={ALGO_VERSION}",
        f"documents={len(corpus)} tier1={len(tier1)} queries={len(queries)}",
        "tier1/files: " + ", ".join(f"{name}={count}" for name, count in sorted(tier_files.items())),
        "tier1/rules: " + ", ".join(f"{name}={count}" for name, count in sorted(tier_rules.items())),
        "queries/channels: " + ", ".join(f"{name}={count}" for name, count in sorted(query_channels.items())),
        "queries/files: " + ", ".join(f"{name}={count}" for name, count in sorted(query_files.items())),
        f"provenance={sum(item.provenance_valid for item in queries)}/{len(queries)}",
        f"anchors={sum(item.anchor_found for item in queries)}/{len(queries)}",
    ]
    return "\n".join(lines)


def _verbose(tier1, queries) -> str:
    lines = ["\nTIER1 REVIEW"]
    lines.extend(
        f"{item.sample_id} | {item.file} p.{item.physical_page} | {item.rule_id} | {item.context}"
        for item in tier1
    )
    lines.append("\nQUERY REVIEW")
    lines.extend(
        f"{item.sample_id} | {item.file} p.{item.physical_page} | "
        f"review={item.channel.value} primary={item.primary_channel.value} "
        f"stage={item.selection_stage} final={item.selected_final} "
        f"provenance={item.provenance_valid} anchor={item.anchor_found} | "
        f"Q={item.query_text} | DONOR={item.donor_text} | ANCHOR={item.pdf_anchor}"
        for item in queries
    )
    return "\n".join(lines)


def _diagnose_n(paths: tuple[Path, ...]) -> str:
    lines: list[str] = []
    for path in sorted((item.resolve() for item in paths), key=lambda item: item.name.casefold()):
        document = parse_search_document(path.read_bytes())
        blocks = {block.block_id: block for block in document.blocks}
        sections = {section.section_id: section for section in document.sections}
        frequencies = _word_frequencies(document)
        counts: Counter[str] = Counter()
        details: list[str] = []
        novelty_blocks = [
            block for block in document.blocks
            if "наукова новизна" in block.normalized.text.casefold()
        ]
        for block in novelty_blocks:
            compact_block = _compact(block.raw_text)
            novelty_start = compact_block.casefold().find("наукова новизна")
            lines.append(
                f"  NOVELTY-BLOCK p.{block.physical_page} "
                f"path={' > '.join(block.heading_path) or '-'} | "
                f"{compact_block[max(0, novelty_start - 100):novelty_start + 500]}"
            )
        for donor in document.sentences:
            block = blocks[donor.block_id]
            section = sections.get(donor.section_id)
            headings = block.heading_path + ((section.heading,) if section is not None else ())
            signals = find_channel_n_signals(donor.raw_text, headings)
            if not signals:
                continue
            status = (
                "eligible" if section is not None and section.kind in CONTENT_SECTION_KINDS
                else f"excluded:{section.kind.value if section is not None else 'unresolved'}"
            )
            counts[status] += 1
            query = None
            if status == "eligible":
                query = build_source_channel_query(
                    donor=donor,
                    block=block,
                    channel=Channel.N,
                    signals=signals,
                    score=4.0,
                    freq=frequencies,
                )
                counts["buildable"] += isinstance(query, SearchQuery)
            details.append(
                f"  p.{block.physical_page} {status} "
                f"heading={section.heading if section is not None else '-'} | "
                f"Q={query.query_text if isinstance(query, SearchQuery) else query} | "
                f"{_compact(donor.raw_text)}"
            )
        lines.append(
            f"{path.name}: "
            + ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        )
        lines.extend(details)
    return "\n".join(lines)


def _interactive_review(corpus, tier1, queries) -> int:
    payload = render_payload(corpus, tier1, queries)
    print(_summary(corpus, tier1, queries), flush=True)
    print(
        "REVIEW READY: docs | failures | tier-json START END | query-json START END | "
        "tier-context START END | query-context START END | quit",
        flush=True,
    )
    for raw_command in sys.stdin:
        parts = raw_command.strip().split()
        if not parts:
            print("REVIEW READY", flush=True)
            continue
        command = parts[0].casefold()
        if command == "quit":
            return 0
        if command == "docs":
            print(json.dumps(payload["documents"], ensure_ascii=False, indent=2), flush=True)
        elif command == "failures":
            failed = [
                item for item in queries
                if not item.provenance_valid or not item.anchor_found
            ]
            print(_verbose((), failed), flush=True)
        elif command in {
            "tier-json", "query-json", "tier-context", "query-context"
        } and len(parts) == 3:
            try:
                start, end = int(parts[1]), int(parts[2])
            except ValueError:
                print("REVIEW ERROR: START/END must be integers", flush=True)
                print("REVIEW READY", flush=True)
                continue
            if command == "tier-json":
                print(json.dumps(payload["tier1_sample"][start:end], ensure_ascii=False, indent=2), flush=True)
            elif command == "query-json":
                print(json.dumps(payload["query_sample"][start:end], ensure_ascii=False, indent=2), flush=True)
            elif command == "tier-context":
                print(_verbose(tier1[start:end], ()), flush=True)
            else:
                print(_verbose((), queries[start:end]), flush=True)
        else:
            print("REVIEW ERROR: unknown command", flush=True)
        print("REVIEW READY", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--diagnose-n", action="store_true")
    parser.add_argument("--interactive-review", action="store_true")
    args = parser.parse_args(argv)
    if args.diagnose_n:
        try:
            print(_diagnose_n(tuple(args.files)))
        except (OSError, ValueError) as exc:
            print(f"Помилка: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        progress = (lambda message: print(message, file=sys.stderr, flush=True)) if args.progress else None
        corpus = collect_corpus(tuple(args.files), progress=progress)
        tier1 = select_tier1_sample(corpus)
        queries = select_query_sample(corpus)
    except (OSError, ValueError, QualitySampleError) as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 1
    if args.interactive_review:
        return _interactive_review(corpus, tier1, queries)
    if args.as_json:
        print(json.dumps(render_payload(corpus, tier1, queries), ensure_ascii=False, indent=2))
        if args.verbose:
            print(_verbose(tier1, queries))
    else:
        print(_summary(corpus, tier1, queries))
        if args.verbose:
            print(_verbose(tier1, queries))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
