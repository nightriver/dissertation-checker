"""Відбитки, кандидати, точне вирівнювання та метрики збігів."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Sequence

from rapidfuzz.distance import Levenshtein

from compare import params
from compare.normative import is_possibly_normative
from compare.prepare import prepare_document_for_comparison
from compare.biblio_match import compare_bibliographies
from compare.types import ComparisonResult, CompareToken, DiffSpan, TextSegment


@dataclass(frozen=True)
class Fingerprint:
    digest: bytes
    position: int


@dataclass(frozen=True)
class Seed:
    a_pos: int
    b_pos: int


@dataclass(frozen=True)
class Candidate:
    a_start: int
    a_end: int
    b_start: int
    b_end: int
    seed_count: int = 0

    @property
    def length(self) -> int:
        return max(self.a_end - self.a_start, self.b_end - self.b_start)


def candidate_word(word: str, truncate_stems: bool = params.USE_TRUNCATED_STEMS) -> str:
    """Форма лише для генерації кандидата; повні слова лишаються для метрик."""
    return word[:params.TRUNCATED_STEM_LENGTH] if truncate_stems else word


def _stable_hash(words: Sequence[str]) -> bytes:
    return hashlib.sha256("\x1f".join(words).encode("utf-8")).digest()


def build_fingerprints(
    tokens: Sequence[CompareToken],
    truncate_stems: bool = params.USE_TRUNCATED_STEMS,
) -> list[Fingerprint]:
    """П'ятислівні SHA-256 відбитки з правостороннім winnowing."""
    words = [candidate_word(token.normalized, truncate_stems) for token in tokens]
    if len(words) < params.FINGERPRINT_K:
        return []
    hashes = [
        _stable_hash(words[index:index + params.FINGERPRINT_K])
        for index in range(len(words) - params.FINGERPRINT_K + 1)
    ]
    window = min(params.WINNOW_WINDOW, len(hashes))
    selected: list[Fingerprint] = []
    last_position = -1
    for start in range(len(hashes) - window + 1):
        values = hashes[start:start + window]
        minimum = min(values)
        relative = max(index for index, value in enumerate(values) if value == minimum)
        position = start + relative
        if position != last_position:
            selected.append(Fingerprint(minimum, position))
            last_position = position
    return selected


def chain_seeds(seeds: Iterable[Seed]) -> list[list[Seed]]:
    """Групує семена у монотонні ланцюжки з обмеженням розриву і дрейфу."""
    chains: list[list[Seed]] = []
    for seed in sorted(set(seeds), key=lambda item: (item.a_pos, item.b_pos)):
        best_index: int | None = None
        best_gap: int | None = None
        for index, chain in enumerate(chains):
            last = chain[-1]
            gap_a = seed.a_pos - last.a_pos
            gap_b = seed.b_pos - last.b_pos
            initial_drift = chain[0].b_pos - chain[0].a_pos
            drift = seed.b_pos - seed.a_pos
            if not (0 < gap_a <= params.MAX_SEED_GAP and 0 < gap_b <= params.MAX_SEED_GAP):
                continue
            if abs(drift - initial_drift) > params.MAX_CHAIN_DRIFT:
                continue
            gap = gap_a + gap_b
            if best_gap is None or gap < best_gap:
                best_index, best_gap = index, gap
        if best_index is None:
            chains.append([seed])
        else:
            chains[best_index].append(seed)
    return [chain for chain in chains if len(chain) >= params.MIN_SEEDS_PER_CHAIN]


def _add_frequent_seeds(
    chains: list[list[Seed]],
    fingerprints_a: Sequence[Fingerprint],
    postings: dict[bytes, list[int]],
) -> list[list[Seed]]:
    """Частий відбиток не створює ланцюг, але уточнює вже знайдений."""
    frequent_a = [
        fingerprint for fingerprint in fingerprints_a
        if len(postings.get(fingerprint.digest, ())) >= params.MAX_FINGERPRINT_POSTINGS
    ]
    augmented: list[list[Seed]] = []
    for chain in chains:
        first, last = chain[0], chain[-1]
        base_drift = sum(seed.b_pos - seed.a_pos for seed in chain) / len(chain)
        additions: list[Seed] = []
        for fingerprint in frequent_a:
            if not (first.a_pos <= fingerprint.position <= last.a_pos):
                continue
            possible = [
                b_pos for b_pos in postings[fingerprint.digest]
                if first.b_pos <= b_pos <= last.b_pos
                and abs((b_pos - fingerprint.position) - base_drift) <= params.MAX_CHAIN_DRIFT
            ]
            if possible:
                best = min(possible, key=lambda b_pos: abs((b_pos - fingerprint.position) - base_drift))
                additions.append(Seed(fingerprint.position, best))
        augmented.append(sorted(set(chain + additions), key=lambda seed: (seed.a_pos, seed.b_pos)))
    return augmented


def _candidate_from_chain(chain: Sequence[Seed], len_a: int, len_b: int) -> Candidate:
    context = params.FINGERPRINT_K + params.WINNOW_WINDOW
    return Candidate(
        max(0, chain[0].a_pos - context),
        min(len_a, chain[-1].a_pos + params.FINGERPRINT_K + context),
        max(0, chain[0].b_pos - context),
        min(len_b, chain[-1].b_pos + params.FINGERPRINT_K + context),
        len(chain),
    )


def split_candidate(candidate: Candidate) -> list[Candidate]:
    """Ріже довгі області по 3000 токенів з перекриттям 200."""
    if candidate.length <= params.MAX_CANDIDATE_TOKENS:
        return [candidate]
    chunks: list[Candidate] = []
    offset = 0
    step = params.MAX_CANDIDATE_TOKENS - params.CANDIDATE_OVERLAP
    while offset < candidate.length:
        a_start = min(candidate.a_end, candidate.a_start + offset)
        b_start = min(candidate.b_end, candidate.b_start + offset)
        chunks.append(Candidate(
            a_start,
            min(candidate.a_end, a_start + params.MAX_CANDIDATE_TOKENS),
            b_start,
            min(candidate.b_end, b_start + params.MAX_CANDIDATE_TOKENS),
            candidate.seed_count,
        ))
        if offset + params.MAX_CANDIDATE_TOKENS >= candidate.length:
            break
        offset += step
    return chunks


def merge_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Об'єднує вкладені, перекривні та зовсім сусідні області до вирівнювання."""
    ordered = sorted(candidates, key=lambda item: (item.a_start, item.b_start))
    if not ordered:
        return []
    merged = [ordered[0]]
    for candidate in ordered[1:]:
        current = merged[-1]
        close_a = candidate.a_start <= current.a_end + params.CANDIDATE_MERGE_GAP
        close_b = candidate.b_start <= current.b_end + params.CANDIDATE_MERGE_GAP
        drift_current = current.b_start - current.a_start
        drift_candidate = candidate.b_start - candidate.a_start
        if close_a and close_b and abs(drift_candidate - drift_current) <= params.MAX_CHAIN_DRIFT:
            merged[-1] = Candidate(
                min(current.a_start, candidate.a_start),
                max(current.a_end, candidate.a_end),
                min(current.b_start, candidate.b_start),
                max(current.b_end, candidate.b_end),
                current.seed_count + candidate.seed_count,
            )
        else:
            merged.append(candidate)
    return merged


def find_candidates(
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
    truncate_stems: bool = params.USE_TRUNCATED_STEMS,
) -> tuple[list[Candidate], int]:
    """Повертає ранжовані області та їх повну кількість до ліміту."""
    fingerprints_a = build_fingerprints(tokens_a, truncate_stems)
    fingerprints_b = build_fingerprints(tokens_b, truncate_stems)
    postings: dict[bytes, list[int]] = defaultdict(list)
    for fingerprint in fingerprints_b:
        postings[fingerprint.digest].append(fingerprint.position)
    seeds = [
        Seed(fingerprint.position, b_pos)
        for fingerprint in fingerprints_a
        for b_pos in postings.get(fingerprint.digest, ())
        if len(postings[fingerprint.digest]) < params.MAX_FINGERPRINT_POSTINGS
    ]
    chains = _add_frequent_seeds(chain_seeds(seeds), fingerprints_a, postings)
    raw_candidates = merge_candidates(
        _candidate_from_chain(chain, len(tokens_a), len(tokens_b)) for chain in chains
    )
    candidates = [
        chunk
        for candidate in raw_candidates
        for chunk in split_candidate(candidate)
        if chunk.length >= params.MIN_CANDIDATE_TOKENS
    ]
    candidates.sort(
        key=lambda item: (item.seed_count / max(item.length, 1), item.length),
        reverse=True,
    )
    total = len(candidates)
    return candidates[:params.MAX_CHAINS], total


def _fuzzy_pairs(a_words: Sequence[str], b_words: Sequence[str]) -> list[tuple[int, int]]:
    """Максимально вагоме одно-к-одному зіставлення зі збереженням порядку."""
    rows, cols = len(a_words), len(b_words)
    scores = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    take = [[False] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            best = max(scores[i - 1][j], scores[i][j - 1])
            score = Levenshtein.normalized_similarity(a_words[i - 1], b_words[j - 1])
            threshold = (
                params.FUZZY_SHORT_THRESHOLD
                if min(len(a_words[i - 1]), len(b_words[j - 1])) <= 4
                else params.FUZZY_THRESHOLD
            )
            paired = scores[i - 1][j - 1] + score if score >= threshold else -1.0
            if paired > best:
                scores[i][j] = paired
                take[i][j] = True
            else:
                scores[i][j] = best
    pairs: list[tuple[int, int]] = []
    i, j = rows, cols
    while i and j:
        if take[i][j]:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif scores[i - 1][j] >= scores[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs))


def _merge_operations(operations: Sequence[str], offset: int) -> tuple[DiffSpan, ...]:
    if not operations:
        return ()
    spans: list[DiffSpan] = []
    start = 0
    current = operations[0]
    for index, operation in enumerate(operations[1:], 1):
        if operation != current:
            spans.append(DiffSpan(offset + start, offset + index, current))
            start, current = index, operation
    spans.append(DiffSpan(offset + start, offset + len(operations), current))
    return tuple(spans)


def align_candidate(
    candidate: Candidate,
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
) -> TextSegment | None:
    """Вирівнює одну коротку область; fuzzy не впливає на прийняття."""
    words_a = [token.normalized for token in tokens_a[candidate.a_start:candidate.a_end]]
    words_b = [token.normalized for token in tokens_b[candidate.b_start:candidate.b_end]]
    matcher = SequenceMatcher(None, words_a, words_b, autojunk=False)
    opcodes = matcher.get_opcodes()
    equal = [opcode for opcode in opcodes if opcode[0] == "equal" and opcode[2] > opcode[1]]
    if not equal:
        return None

    # Знімаємо випадковий контекст з країв, залишаючи зміни всередині збігу.
    a_left, b_left = equal[0][1], equal[0][3]
    a_right, b_right = equal[-1][2], equal[-1][4]
    words_a = words_a[a_left:a_right]
    words_b = words_b[b_left:b_right]
    a_start = candidate.a_start + a_left
    b_start = candidate.b_start + b_left
    matcher = SequenceMatcher(None, words_a, words_b, autojunk=False)

    a_operations = ["replace"] * len(words_a)
    b_operations = ["replace"] * len(words_b)
    matched = fuzzy_matched = longest = 0
    changed = False
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            size = i2 - i1
            matched += size
            longest = max(longest, size)
            a_operations[i1:i2] = ["equal"] * size
            b_operations[j1:j2] = ["equal"] * size
        elif tag == "replace":
            changed = True
            pairs = _fuzzy_pairs(words_a[i1:i2], words_b[j1:j2])
            for a_index, b_index in pairs:
                a_operations[i1 + a_index] = "fuzzy"
                b_operations[j1 + b_index] = "fuzzy"
            fuzzy_matched += len(pairs)
        elif tag == "delete":
            changed = True
            a_operations[i1:i2] = ["delete"] * (i2 - i1)
        elif tag == "insert":
            changed = True
            b_operations[j1:j2] = ["insert"] * (j2 - j1)

    len_a, len_b = len(words_a), len(words_b)
    similarity = 2 * matched / (len_a + len_b) if len_a + len_b else 0.0
    passes_low = (
        matched >= params.MIN_MATCHED_LOW and similarity >= params.MIN_SIMILARITY
    ) or longest >= params.MIN_VERBATIM_LOW
    if not passes_low:
        return None
    raw_a = " ".join(token.raw for token in tokens_a[a_start:a_start + len_a])
    raw_b = " ".join(token.raw for token in tokens_b[b_start:b_start + len_b])
    boilerplate_phrases = (
        "дисертація містить результати власних досліджень",
        "на здобуття наукового ступеня",
        "міністерство освіти і науки україни",
    )
    normalized_raw = " ".join(words_a + words_b)
    boilerplate = any(phrase in normalized_raw for phrase in boilerplate_phrases)
    normative = (
        is_possibly_normative(words_a, raw_a)
        or is_possibly_normative(words_b, raw_b)
    )
    passes_high = (
        matched >= params.MIN_MATCHED_HIGH and similarity >= params.MIN_SIMILARITY
    ) or longest >= params.MIN_VERBATIM_HIGH
    status = "accepted_normative" if normative and passes_high else (
        "normative_only" if normative else "accepted"
    )
    return TextSegment(
        a_start=a_start,
        a_end=a_start + len_a,
        b_start=b_start,
        b_end=b_start + len_b,
        matched=matched,
        fuzzy_matched=fuzzy_matched,
        len_a=len_a,
        len_b=len_b,
        coverage_a=matched / len_a if len_a else 0.0,
        coverage_b=matched / len_b if len_b else 0.0,
        similarity=similarity,
        longest_verbatim=longest,
        kind="modified" if changed else "verbatim",
        possibly_normative=normative,
        possibly_boilerplate=boilerplate,
        status=status,
        a_spans=_merge_operations(a_operations, a_start),
        b_spans=_merge_operations(b_operations, b_start),
    )


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def deduplicate_segments(segments: Iterable[TextSegment]) -> list[TextSegment]:
    """Лишає найповнішу з вкладених/майже однакових знахідок."""
    kept: list[TextSegment] = []
    ordered = sorted(segments, key=lambda item: (item.matched, item.len_a + item.len_b), reverse=True)
    for segment in ordered:
        duplicate = False
        for other in kept:
            overlap_a = _overlap(segment.a_start, segment.a_end, other.a_start, other.a_end)
            overlap_b = _overlap(segment.b_start, segment.b_end, other.b_start, other.b_end)
            if (
                overlap_a >= 0.8 * min(segment.len_a, other.len_a)
                and overlap_b >= 0.8 * min(segment.len_b, other.len_b)
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(segment)
    return sorted(kept, key=lambda item: (item.a_start, item.b_start))


def _union_length(intervals: Iterable[tuple[int, int]]) -> int:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def coverage_from_segments(
    segments: Iterable[TextSegment], side: str = "a", strict: bool = False
) -> int:
    """Точне покриття як об'єднання equal-інтервалів, без fuzzy-пар."""
    spans = (
        segment.a_spans if side == "a" else segment.b_spans
        for segment in segments
        if segment.status == "accepted" or (not strict and segment.status == "accepted_normative")
    )
    return _union_length(
        (span.start_token, span.end_token)
        for group in spans
        for span in group
        if span.operation == "equal"
    )


def compare_tokens(
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
) -> ComparisonResult:
    candidates, candidates_total = find_candidates(tokens_a, tokens_b)
    segments = deduplicate_segments(
        segment
        for candidate in candidates
        if (segment := align_candidate(candidate, tokens_a, tokens_b)) is not None
    )
    covered_a = coverage_from_segments(segments, "a")
    covered_b = coverage_from_segments(segments, "b")
    strict_a = coverage_from_segments(segments, "a", strict=True)
    strict_b = coverage_from_segments(segments, "b", strict=True)
    return ComparisonResult(
        segments=segments,
        analyzed_tokens_a=len(tokens_a),
        analyzed_tokens_b=len(tokens_b),
        covered_tokens_a=covered_a,
        covered_tokens_b=covered_b,
        covered_tokens_a_strict=strict_a,
        covered_tokens_b_strict=strict_b,
        excluded_a=(),
        excluded_b=(),
        biblio=None,
        analysis_complete=candidates_total <= params.MAX_CHAINS,
        candidates_total=candidates_total,
        candidates_processed=len(candidates),
    )


def compare_documents(lines_a, lines_b) -> ComparisonResult:
    """Порівнює рядки з консервативним структурним обрізанням кожного боку."""
    prepared_a, entries_a = prepare_document_for_comparison(lines_a)
    prepared_b, entries_b = prepare_document_for_comparison(lines_b)
    result = compare_tokens(prepared_a.tokens, prepared_b.tokens)
    result.excluded_a = prepared_a.excluded
    result.excluded_b = prepared_b.excluded
    result.biblio = compare_bibliographies(entries_a, entries_b)
    return result
