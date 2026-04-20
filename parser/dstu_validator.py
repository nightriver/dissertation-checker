import re
from enum import Enum


class DstuStatus(Enum):
    DSTU    = "dstu"
    PARTIAL = "partial"
    OTHER   = "other"


_DSTU_STRONG = [
    re.compile(r'\s:\s'),
    re.compile(r'\.\s[–—]\s'),
]

_DSTU_WEAK = [
    re.compile(r'\d+\s*с\.'),
    re.compile(r'URL\s*:', re.IGNORECASE),
    re.compile(r'дата звернення', re.IGNORECASE),
]

_NON_DSTU = [
    re.compile(r'\(\d{4}\)\.'),
    re.compile(r'Vol\.\s*\d+', re.IGNORECASE),
    re.compile(r'No\.\s*\d+', re.IGNORECASE),
    re.compile(r'pp?\.\s*\d+', re.IGNORECASE),
]


def check_dstu(entry: str) -> DstuStatus:
    """Перевіряє один запис на відповідність ДСТУ 8302:2015."""
    for pattern in _NON_DSTU:
        if pattern.search(entry):
            return DstuStatus.OTHER

    strong_hits = sum(1 for p in _DSTU_STRONG if p.search(entry))
    weak_hits   = sum(1 for p in _DSTU_WEAK   if p.search(entry))

    if strong_hits >= 2:
        return DstuStatus.DSTU
    if strong_hits >= 1 and weak_hits >= 2:
        return DstuStatus.DSTU
    if strong_hits == 1 or weak_hits >= 1:
        return DstuStatus.PARTIAL
    return DstuStatus.OTHER


def validate_bibliography(bibliography: dict[int, str]) -> dict[int, DstuStatus]:
    """Перевіряє всі записи. Повертає dict[номер -> DstuStatus]."""
    return {num: check_dstu(entry) for num, entry in bibliography.items()}
