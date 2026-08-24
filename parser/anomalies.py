"""
anomalies.py
Анахронізми у списку літератури: джерело не може бути новішим за роботу,
яка на нього посилається.

Модуль — чисті функції над словниками: ані PDF, ані DOCX для тестів не потрібні.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from parser.types import Severity


@dataclass
class Anachronism:
    """
    Одна знахідка. Поля `num` немає навмисно — воно дублювало б ключ словника,
    який повертає find_anachronisms().
    """
    source_year: int
    delta: int          # source_year - dissertation_year (0 якщо року немає)
    severity: Severity
    reason: str


def find_anachronisms(
    years: dict[int, Optional[int]],
    confidence: dict[int, str],
    dissertation_year: Optional[int],
    current_year: Optional[int] = None,
) -> dict[int, Anachronism]:
    """
    Шукає джерела, датовані пізніше за дисертацію.

    years / confidence — виходи year_extractor, ключ у обох — номер джерела.
    dissertation_year  — рік з титульної сторінки або введений експертом.
    current_year       — параметр, а не datetime.now(): інакше тест довелося б
                         прив'язувати до системного годинника.

    Правила застосовуються згори вниз, перше збіжне виграє:

      рік > current_year              → PROOF   (не залежить від року роботи)
      delta >= +2, патерн 'strong'    → PROOF
      delta == +1, патерн 'strong'    → SUSPECT (рік подання vs рік захисту)
      delta >= +1, патерн 'weak'      → SUSPECT (рік визначено неточно)
    """
    findings: dict[int, Anachronism] = {}

    for num, year in years.items():
        if year is None:
            continue

        conf = confidence.get(num, "none")

        if current_year is not None and year > current_year:
            findings[num] = Anachronism(
                source_year=year,
                delta=(year - dissertation_year) if dissertation_year else 0,
                severity=Severity.PROOF,
                reason="рік видання ще не настав",
            )
            continue

        if dissertation_year is None:
            # Без року дисертації delta порахувати нема від чого —
            # решта правил не виконується.
            continue

        delta = year - dissertation_year

        if delta >= 2 and conf == "strong":
            findings[num] = Anachronism(
                source_year=year,
                delta=delta,
                severity=Severity.PROOF,
                reason="джерело новіше за рік на титульній сторінці",
            )
        elif delta == 1 and conf == "strong":
            findings[num] = Anachronism(
                source_year=year,
                delta=delta,
                severity=Severity.SUSPECT,
                reason="джерело на рік новіше — можливо, рік подання проти року захисту",
            )
        elif delta >= 1 and conf == "weak":
            findings[num] = Anachronism(
                source_year=year,
                delta=delta,
                severity=Severity.SUSPECT,
                reason="рік видання визначено неточно — перевірте запис",
            )

    return findings
