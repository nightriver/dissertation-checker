"""Ручний benchmark реальної пари; не входить до pytest."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.matcher import compare_documents, count_off_alignment
from parser.extractor import extract_lines


def _longest_unmatched_run(spans) -> int:
    """Найдовша смуга поспіль без збігу з одного боку сегмента."""
    longest = current = 0
    for span in sorted(spans, key=lambda item: item.start_token):
        if span.operation == "equal":
            current = 0
        else:
            current += span.end_token - span.start_token
            longest = max(longest, current)
    return longest


DEFAULT_LEFT = ROOT / "examples" / "diskor-корецька.pdf"
DEFAULT_RIGHT = ROOT / "examples" / "Гончарова-Парфьонова_дисертація.pdf"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run(left: Path, right: Path) -> int:
    started = time.perf_counter()
    lines_a = extract_lines(left.read_bytes(), left.name)
    lines_b = extract_lines(right.read_bytes(), right.name)
    extracted = time.perf_counter()
    result = compare_documents(lines_a, lines_b)
    finished = time.perf_counter()
    coverage_a = result.covered_tokens_a / result.analyzed_tokens_a if result.analyzed_tokens_a else 0.0
    coverage_b = result.covered_tokens_b / result.analyzed_tokens_b if result.analyzed_tokens_b else 0.0
    print(f"A: {left.name} — {result.analyzed_tokens_a} токенів")
    print(f"B: {right.name} — {result.analyzed_tokens_b} токенів")
    print(f"Читання: {extracted - started:.2f} с")
    print(f"Порівняння: {finished - extracted:.2f} с")
    print(f"Разом: {finished - started:.2f} с (бюджет 60 с)")
    print(
        f"Кандидати: {result.candidates_processed}/{result.candidates_total}; "
        f"ліміт: {'так' if not result.analysis_complete else 'ні'}"
    )
    print(f"Фрагменти: {len(result.segments)}")
    print(f"Покриття A/B: {coverage_a:.2%} / {coverage_b:.2%}")

    # Якість рядків: регресія тут так само помітна, як і регресія часу.
    gaps = [
        max(_longest_unmatched_run(segment.a_spans), _longest_unmatched_run(segment.b_spans))
        for segment in result.segments
    ]
    drifts = sorted(segment.b_start - segment.a_start for segment in result.segments)
    print(f"Найдовший розрив у рядку: {max(gaps) if gaps else 0} слів")
    print(f"Осторонь основного відповідання: {count_off_alignment(result.segments)}")
    print(f"Дрейф: {drifts[0] if drifts else 0}..{drifts[-1] if drifts else 0}")
    print(f"Прибрані повтори: {sum(segment.suppressed_repeats for segment in result.segments)}")
    return 0 if finished - started <= 60 else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", nargs="?", type=Path, default=DEFAULT_LEFT)
    parser.add_argument("right", nargs="?", type=Path, default=DEFAULT_RIGHT)
    args = parser.parse_args()
    return run(args.left, args.right)


if __name__ == "__main__":
    raise SystemExit(main())
