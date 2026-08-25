"""Незалежне зіставлення двох розпізнаних списків літератури."""

from __future__ import annotations

from rapidfuzz.fuzz import ratio

from compare.types import BiblioMatchResult
from parser.duplicates import BibliographicKey, make_bibliographic_key


ALPHABETICAL_THRESHOLD = 0.70
NEAR_TITLE_THRESHOLD = 90.0


def _alphabetical_share(keys: list[BibliographicKey]) -> float:
    if len(keys) < 2:
        return 1.0
    sort_keys = [(key.author or key.title, key.title) for key in keys]
    return sum(left <= right for left, right in zip(sort_keys, sort_keys[1:])) / (len(keys) - 1)


def _order_runs(pairs: list[tuple[int, int]]) -> tuple[int, ...]:
    if not pairs:
        return ()
    runs: list[int] = []
    current = 1
    for previous, item in zip(pairs, pairs[1:]):
        if item[0] == previous[0] + 1 and item[1] == previous[1] + 1:
            current += 1
        else:
            if current >= 3:
                runs.append(current)
            current = 1
    if current >= 3:
        runs.append(current)
    return tuple(runs)


def compare_bibliographies(
    entries_a: dict[int, str], entries_b: dict[int, str]
) -> BiblioMatchResult:
    ordered_a = [make_bibliographic_key(entries_a[number]) for number in sorted(entries_a)]
    ordered_b = [make_bibliographic_key(entries_b[number]) for number in sorted(entries_b)]
    alphabetical_a = _alphabetical_share(ordered_a)
    alphabetical_b = _alphabetical_share(ordered_b)
    applicable = not (
        alphabetical_a >= ALPHABETICAL_THRESHOLD
        and alphabetical_b >= ALPHABETICAL_THRESHOLD
    )

    used_b: set[int] = set()
    pairs: list[tuple[int, int]] = []
    exact = near = 0
    for index_a, key_a in enumerate(ordered_a):
        exact_index = next(
            (index for index, key in enumerate(ordered_b) if index not in used_b and key == key_a),
            None,
        )
        if exact_index is not None:
            used_b.add(exact_index)
            pairs.append((index_a, exact_index))
            exact += 1
            continue
        near_index = next((
            index for index, key in enumerate(ordered_b)
            if index not in used_b
            and key.author == key_a.author
            and key.year == key_a.year
            and bool(key.title and key_a.title)
            and ratio(key.title, key_a.title) >= NEAR_TITLE_THRESHOLD
        ), None)
        if near_index is not None:
            used_b.add(near_index)
            pairs.append((index_a, near_index))
            near += 1

    pairs.sort()
    return BiblioMatchResult(
        parsed_a=bool(entries_a),
        parsed_b=bool(entries_b),
        entries_a=len(entries_a),
        entries_b=len(entries_b),
        common_exact=exact,
        common_near=near,
        alphabetical_a=alphabetical_a,
        alphabetical_b=alphabetical_b,
        order_signal_applicable=applicable,
        order_runs=_order_runs(pairs) if applicable else (),
    )
