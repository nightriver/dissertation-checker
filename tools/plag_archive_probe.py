#!/usr/bin/env python3
"""
Замір: чи є в архіві знімки там, де запит архівної копії дав `http_404`.

Тільки вимірювання, продуктовий код не змінюється. Специфікація —
PLAN_PLAG_FILTER_V3.md, §7 етап 4: чи означає `http_404` на запит
`wayback_copy_url` (снімок на 1 січня наступного за дисертацією року)
«знімків немає взагалі», чи «немає знімка саме навколо цієї дати». Від
відповіді залежить, чи виконувати етап 5.

З трас `events.jsonl` (записаних `examples/codex/measure.py`, самі траси —
дані тільки для читання) беруться записи `fetch_end`, `stage=="archive_copy"`,
`error=="http_404"`; з адреси архівного запиту відновлюється вихідна адреса
джерела. Для кожної такої адреси — один послідовний запит CDX API з
`filter=statuscode:200`, пауза 1 с між запитами. Реальні адреси джерел
пишуться лише в `--out` — файл поза репозиторієм.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from plag_filter.fetch import WAYBACK_BASE, fetch_document, parse_cdx_first_capture

_ARCHIVE_COPY_RE = re.compile(r"^https://web\.archive\.org/web/\d{14}id_/(.+)$")

_PAUSE_SECONDS = 1.0


def _original_url(archive_copy_url: str) -> str | None:
    """Вихідна адреса джерела з адреси запиту архівної копії — `wayback_copy_url`."""
    match = _ARCHIVE_COPY_RE.match(archive_copy_url)
    return match.group(1) if match else None


def _addresses_with_missed_copy(events_paths: list[Path]) -> list[str]:
    """Адреси, для яких запит архівної копії дав `http_404`, у порядку появи."""
    seen: dict[str, None] = {}
    for path in events_paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("kind") != "fetch_end":
                    continue
                if record.get("stage") != "archive_copy":
                    continue
                if record.get("error") != "http_404":
                    continue
                original = _original_url(record.get("url", ""))
                if original is not None:
                    seen.setdefault(original, None)
    return list(seen)


def _success_cdx_url(url: str) -> str:
    from urllib.parse import quote

    return f"{WAYBACK_BASE}/cdx/search/cdx?url={quote(url, safe='')}&filter=statuscode:200&limit=1&fl=timestamp"


def probe(addresses: list[str], out_path: Path) -> None:
    """Один послідовний запит CDX на адресу, з паузою — §7 етап 4."""
    with tempfile.TemporaryDirectory(prefix="plag_archive_probe_") as tmp, out_path.open(
        "w", encoding="utf-8"
    ) as out:
        tmp_dir = Path(tmp)
        for index, address in enumerate(addresses):
            if index > 0:
                time.sleep(_PAUSE_SECONDS)
            start = time.perf_counter()
            result = fetch_document(_success_cdx_url(address), tmp_dir=tmp_dir)
            duration = time.perf_counter() - start

            if not result.ok and result.error == "rate_limited":
                print(
                    f"Архів відмовив за частотою (rate_limited) на адресі {index + 1} з "
                    f"{len(addresses)} — зупинка.",
                    file=sys.stderr,
                )
                break

            timestamp = None
            if result.ok and result.pages:
                timestamp = parse_cdx_first_capture(result.pages[0])

            row = {
                "url": address,
                "timestamp": timestamp,
                "error": result.error,
                "duration": round(duration, 3),
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--events",
        type=Path,
        action="append",
        required=True,
        help="шлях до events.jsonl (можна кілька разів)",
    )
    parser.add_argument("--out", type=Path, required=True, help="файл .jsonl поза репозиторієм")
    args = parser.parse_args(argv)

    addresses = _addresses_with_missed_copy(args.events)
    print(f"Адрес із http_404 на архівну копію: {len(addresses)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    probe(addresses, args.out)

    found = 0
    with args.out.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    found = sum(1 for row in rows if row["timestamp"] is not None)
    print(f"Перевірено: {len(rows)} з {len(addresses)}; знайдено знімок: {found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
