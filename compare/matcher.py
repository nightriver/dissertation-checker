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
    # Довгу область більше не ріжемо тут: різати треба до SequenceMatcher,
    # але зшивати назад — до групування, тому і те, і те живе всередині
    # align_candidate_segments. Сюди повертаються цілі області.
    candidates = [
        candidate
        for candidate in raw_candidates
        if candidate.length >= params.MIN_CANDIDATE_TOKENS
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


def _group_opcodes_by_gap(opcodes: Sequence[tuple]) -> list[list[tuple]]:
    """
    Ріже опкоди кандидата на групи по довгому розриву між збігами.

    Група завжди починається і закінчується блоком ``equal``, а розрив
    усередині не перевищує ``MAX_MATCH_GAP`` з жодного боку. Випадковий
    контекст на краях кандидата відпадає сам: усе до першого і після
    останнього ``equal`` не потрапляє в жодну групу.
    """
    equal_indexes = [
        index for index, opcode in enumerate(opcodes)
        if opcode[0] == "equal" and opcode[2] > opcode[1]
    ]
    if not equal_indexes:
        return []
    # Межі груп у координатах списку опкодів: (перший equal, останній equal).
    bounds: list[list[int]] = [[equal_indexes[0], equal_indexes[0]]]
    for index in equal_indexes[1:]:
        previous = opcodes[bounds[-1][1]]
        gap_a = opcodes[index][1] - previous[2]
        gap_b = opcodes[index][3] - previous[4]
        if max(gap_a, gap_b) <= params.MAX_MATCH_GAP:
            bounds[-1][1] = index
        else:
            bounds.append([index, index])
    return [list(opcodes[first:last + 1]) for first, last in bounds]


def _stitched_opcodes(
    candidate: Candidate,
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
) -> list[tuple]:
    """
    Опкоди всієї області в абсолютних координатах токенів.

    Довга область ріжеться на куски по ``MAX_CANDIDATE_TOKENS`` — це
    жорсткий предел перед ``SequenceMatcher``, без нього довелося б
    вирівнювати весь документ проти всього документа. Але потоки опкодів
    сусідніх кусків зшиваються назад в один (розділ 6.3, п. 7): інакше
    суцільний збіг показувався б окремим рядком на кожні 3000 слів, а
    ``CANDIDATE_OVERLAP`` слів на кожному шві — двічі.

    Шов проходить по блоку ``equal``, який перетинає межу кусків: у нього
    обрізається початок, і обрізається симетрично з обох боків, тому
    відповідність сторін усередині блока не втрачається.
    """
    stitched: list[tuple] = []
    emitted_a = emitted_b = 0
    for index, chunk in enumerate(split_candidate(candidate)):
        words_a = [token.normalized for token in tokens_a[chunk.a_start:chunk.a_end]]
        words_b = [token.normalized for token in tokens_b[chunk.b_start:chunk.b_end]]
        opcodes = [
            (tag, chunk.a_start + i1, chunk.a_start + i2, chunk.b_start + j1, chunk.b_start + j2)
            for tag, i1, i2, j1, j2 in
            SequenceMatcher(None, words_a, words_b, autojunk=False).get_opcodes()
        ]
        if index == 0:
            stitched.extend(opcodes)
        else:
            # Хвіст попереднього куска після останнього equal — це край
            # вікна, де вирівнювання найгірше. Віддаємо цю ділянку
            # наступному куску, у якого вона всередині, а не з краю.
            while stitched and stitched[-1][0] != "equal":
                stitched.pop()
            if stitched:
                emitted_a, emitted_b = stitched[-1][2], stitched[-1][4]
            stitched.extend(_resume_after_seam(opcodes, emitted_a, emitted_b))
        if stitched:
            emitted_a, emitted_b = max(emitted_a, stitched[-1][2]), max(emitted_b, stitched[-1][4])
    return stitched


def _resume_after_seam(opcodes: Sequence[tuple], emitted_a: int, emitted_b: int) -> list[tuple]:
    """
    Лишає з куска тільки те, що ще не випущено попереднім куском.

    Відлік починається з першого блока ``equal``, який дотягується за межу
    вже випущеного. Усе до нього лежить у перекритті й уже описане сусідом.
    Якщо жоден ``equal`` шов не перетинає — це справжній розрив, і групування
    нижче чесно розділить його на два рядки.
    """
    for position, (tag, a1, a2, b1, b2) in enumerate(opcodes):
        if tag != "equal" or a2 <= emitted_a or b2 <= emitted_b:
            continue
        shift = max(emitted_a - a1, emitted_b - b1, 0)
        if a1 + shift >= a2 or b1 + shift >= b2:
            continue
        head = (tag, a1 + shift, a2, b1 + shift, b2)
        return [head, *opcodes[position + 1:]]
    return []


def align_candidate_segments(
    candidate: Candidate,
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
) -> list[TextSegment]:
    """
    Вирівнює одну область і повертає ВСІ її сегменти.

    Область може містити кілька окремих збігів, розділених чужим текстом.
    Раніше вони склеювались в один сегмент, і в правій комірці таблиці
    опинявся текст, якого немає в лівій. Тепер кожен збіг — свій сегмент.
    """
    opcodes = _stitched_opcodes(candidate, tokens_a, tokens_b)
    return [
        segment
        for group in _group_opcodes_by_gap(opcodes)
        if (segment := _align_group(group, tokens_a, tokens_b)) is not None
    ]


def align_candidate(
    candidate: Candidate,
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
) -> TextSegment | None:
    """Найповніший сегмент області; повний список дає align_candidate_segments."""
    segments = align_candidate_segments(candidate, tokens_a, tokens_b)
    return max(segments, key=lambda item: item.matched) if segments else None


def _align_group(
    opcodes: Sequence[tuple],
    tokens_a: Sequence[CompareToken],
    tokens_b: Sequence[CompareToken],
) -> TextSegment | None:
    """
    Збирає сегмент з готової групи опкодів; fuzzy не впливає на прийняття.

    Координати опкодів абсолютні. Опкоди навмисно не перераховуються заново
    для вікна групи: інша межа вікна дала б інший контекст, і всередині
    знову міг би зʼявитися розрив довший за ``MAX_MATCH_GAP``.
    """
    a_start, b_start = opcodes[0][1], opcodes[0][3]
    a_end, b_end = opcodes[-1][2], opcodes[-1][4]
    len_a, len_b = a_end - a_start, b_end - b_start

    a_operations = ["replace"] * len_a
    b_operations = ["replace"] * len_b
    matched = fuzzy_matched = longest = 0
    changed = False
    for tag, i1, i2, j1, j2 in opcodes:
        ai1, ai2 = i1 - a_start, i2 - a_start
        bj1, bj2 = j1 - b_start, j2 - b_start
        if tag == "equal":
            size = i2 - i1
            matched += size
            longest = max(longest, size)
            a_operations[ai1:ai2] = ["equal"] * size
            b_operations[bj1:bj2] = ["equal"] * size
        elif tag == "replace":
            changed = True
            pairs = _fuzzy_pairs(
                [token.normalized for token in tokens_a[i1:i2]],
                [token.normalized for token in tokens_b[j1:j2]],
            )
            for a_index, b_index in pairs:
                a_operations[ai1 + a_index] = "fuzzy"
                b_operations[bj1 + b_index] = "fuzzy"
            fuzzy_matched += len(pairs)
        elif tag == "delete":
            changed = True
            a_operations[ai1:ai2] = ["delete"] * (i2 - i1)
        elif tag == "insert":
            changed = True
            b_operations[bj1:bj2] = ["insert"] * (j2 - j1)

    similarity = 2 * matched / (len_a + len_b) if len_a + len_b else 0.0
    coverage_a = matched / len_a if len_a else 0.0
    coverage_b = matched / len_b if len_b else 0.0
    # Поріг схожості спільний для обох гілок: п'ятнадцять дослівних слів
    # приймають сегмент лише тоді, коли сегмент навколо них не розтягнутий.
    dense_enough = (
        similarity >= params.MIN_SIMILARITY
        and min(coverage_a, coverage_b) >= params.MIN_SEGMENT_COVERAGE
    )
    passes_low = dense_enough and (
        matched >= params.MIN_MATCHED_LOW or longest >= params.MIN_VERBATIM_LOW
    )
    if not passes_low:
        return None
    window_a = [token.normalized for token in tokens_a[a_start:a_end]]
    window_b = [token.normalized for token in tokens_b[b_start:b_end]]
    raw_a = " ".join(token.raw for token in tokens_a[a_start:a_end])
    raw_b = " ".join(token.raw for token in tokens_b[b_start:b_end])
    boilerplate_phrases = (
        "дисертація містить результати власних досліджень",
        "на здобуття наукового ступеня",
        "міністерство освіти і науки україни",
    )
    normalized_raw = " ".join(window_a + window_b)
    boilerplate = any(phrase in normalized_raw for phrase in boilerplate_phrases)
    normative = (
        is_possibly_normative(window_a, raw_a)
        or is_possibly_normative(window_b, raw_b)
    )
    passes_high = dense_enough and (
        matched >= params.MIN_MATCHED_HIGH or longest >= params.MIN_VERBATIM_HIGH
    )
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
        coverage_a=coverage_a,
        coverage_b=coverage_b,
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
    """
    Лишає найповнішу з вкладених, однакових і повторюваних знахідок.

    Перекриття перевіряється по кожному боку ОКРЕМО: один абзац ліворуч і
    п'ять його майже однакових копій праворуч давали п'ять рядків з тією
    самою правою коміркою. Тепер це один рядок, а число прибраних повторів
    лишається на ньому в ``suppressed_repeats`` — нічого не глушиться мовчки.
    """
    kept: list[TextSegment] = []
    ordered = sorted(segments, key=lambda item: (item.matched, item.len_a + item.len_b), reverse=True)
    for segment in ordered:
        duplicate: TextSegment | None = None
        for other in kept:
            overlap_a = _overlap(segment.a_start, segment.a_end, other.a_start, other.a_end)
            overlap_b = _overlap(segment.b_start, segment.b_end, other.b_start, other.b_end)
            if (
                overlap_a >= params.DUPLICATE_OVERLAP * segment.len_a
                or overlap_b >= params.DUPLICATE_OVERLAP * segment.len_b
            ):
                duplicate = other
                break
        if duplicate is None:
            kept.append(segment)
        else:
            duplicate.suppressed_repeats += 1
    return sorted(kept, key=lambda item: (item.a_start, item.b_start))


def count_off_alignment(segments: Sequence[TextSegment]) -> int:
    """
    Скільки знахідок стоїть осторонь основного відповідання документів.

    Діагностика, а не фільтр: перестановка фрагментів при запозиченні —
    законна, глушити її не можна. Але коли таких рядків більшість, як було
    на парі однакових файлів, це видно згори, а не після окремого розбору.
    """
    if not segments:
        return 0
    drifts = sorted(segment.b_start - segment.a_start for segment in segments)
    median = drifts[len(drifts) // 2]
    return sum(abs(drift - median) > params.OFF_ALIGNMENT_DRIFT for drift in drifts)


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
        for segment in align_candidate_segments(candidate, tokens_a, tokens_b)
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
