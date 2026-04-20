import re
from typing import Optional


_YEAR_PATTERNS = [
    r',\s*(1[89]\d{2}|20[0-2]\d)\s*[.\–\-]',
    r'\(\s*(1[89]\d{2}|20[0-2]\d)\s*\)',
    r',\s*(1[89]\d{2}|20[0-2]\d)\s*$',
    r'(?<!\d\.)\b(1[89]\d{2}|20[0-2]\d)\b(?!\.\d)',
]


def extract_year(entry: str) -> Optional[int]:
    """Повертає рік видання або None якщо не знайдено."""
    for pattern in _YEAR_PATTERNS:
        match = re.search(pattern, entry)
        if match:
            return int(match.group(1))
    return None


def extract_years(bibliography: dict[int, str]) -> dict[int, Optional[int]]:
    """Повертає dict[номер_джерела -> рік або None]."""
    return {num: extract_year(entry) for num, entry in bibliography.items()}
