import re
from enum import Enum


class DstuStatus(Enum):
    DSTU    = "dstu"
    PARTIAL = "partial"
    OTHER   = "other"


# ---------------------------------------------------------------------------
# Сильні сигнали ДСТУ 8302:2015
# ---------------------------------------------------------------------------
# " : "    — розділювач міста та видавництва:  Київ : Либідь
# ". – "   — розділювач полів запису:           Київ, 2020. – 240 с.
# " // "   — стаття у журналі/збірнику:        Назва // Журнал. – 2014. – №3
# " / "    — автор після назви:                Назва / І. Іванов
_DSTU_STRONG = [
    re.compile(r'\s:\s'),           # " : "
    re.compile(r'\.\s[–—]\s'),      # ". – " або ". — "
    re.compile(r'\s//\s'),          # " // "
    re.compile(r'\s/\s'),           # " / "
]

# ---------------------------------------------------------------------------
# Слабкі сигнали ДСТУ
# ---------------------------------------------------------------------------
# число + "с." — обсяг видання: "240 с."
# "URL :"      — електронний ресурс
# "дата звернення" — електронний ресурс
# "№"          — номер журналу/випуску (характерно для укр. стандарту)
_DSTU_WEAK = [
    re.compile(r'\d+\s*с\.'),
    re.compile(r'URL\s*:', re.IGNORECASE),
    re.compile(r'дата звернення', re.IGNORECASE),
    re.compile(r'№\s*\d+'),
]

# ---------------------------------------------------------------------------
# Чіткі ознаки НЕ-ДСТУ (APA, Vancouver, Chicago тощо)
# ---------------------------------------------------------------------------
_NON_DSTU = [
    re.compile(r'\(\d{4}\)\.'),             # (2021).  → APA
    re.compile(r'Vol\.\s*\d+', re.IGNORECASE),   # Vol. 10  → англомовний
    re.compile(r'\bNo\.\s*\d+', re.IGNORECASE),  # No. 3    → англомовний
    re.compile(r'\bpp?\.\s*\d+', re.IGNORECASE), # pp. 12   → англомовний
]


def check_dstu(entry: str) -> DstuStatus:
    """
    Перевіряє один запис на відповідність ДСТУ 8302:2015.

    Логіка:
      • Будь-який NON_DSTU маркер → OTHER
      • strong_hits >= 2           → DSTU
      • strong_hits >= 1
          AND weak_hits >= 1       → DSTU
      • strong_hits == 1
          OR  weak_hits >= 1       → PARTIAL
      • Інакше                     → OTHER
    """
    for pattern in _NON_DSTU:
        if pattern.search(entry):
            return DstuStatus.OTHER

    strong_hits = sum(1 for p in _DSTU_STRONG if p.search(entry))
    weak_hits   = sum(1 for p in _DSTU_WEAK   if p.search(entry))

    if strong_hits >= 2:
        return DstuStatus.DSTU
    if strong_hits >= 1 and weak_hits >= 1:
        return DstuStatus.DSTU
    if strong_hits == 1 or weak_hits >= 1:
        return DstuStatus.PARTIAL
    return DstuStatus.OTHER


def validate_bibliography(bibliography: dict[int, str]) -> dict[int, DstuStatus]:
    """Перевіряє всі записи. Повертає dict[номер -> DstuStatus]."""
    return {num: check_dstu(entry) for num, entry in bibliography.items()}
