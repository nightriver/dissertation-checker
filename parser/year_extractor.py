import re
from typing import Optional

# Перші три патерни прив'язані до контексту — це справді поле року за ДСТУ
# («, 2019.» · «(2019)» · «, 2019» у кінці запису), тому дають 'strong'.
# Четвертий ловить будь-яке число, схоже на рік, будь-де в записі: він може
# взяти рік із назви («Закон України 2020 року про…»), тож дає лише 'weak'.
_YEAR_PATTERNS = [
    r',\s*(1[89]\d{2}|20[0-2]\d)\s*[.\–\-]',
    r'\(\s*(1[89]\d{2}|20[0-2]\d)\s*\)',
    r',\s*(1[89]\d{2}|20[0-2]\d)\s*$',
    r'(?<!\d\.)\b(1[89]\d{2}|20[0-2]\d)\b(?!\.\d)',
]

# Індекс, з якого починаються патерни без контекстної прив'язки.
_WEAK_FROM = 3


def extract_year_with_confidence(entry: str) -> tuple[Optional[int], str]:
    """
    Повертає (рік, достовірність), де достовірність — одне з:

      'strong' — рік узято з контекстного поля ДСТУ;
      'weak'   — рік узято загальним патерном, це може бути число з назви;
      'none'   — року не знайдено (рік = None).
    """
    for index, pattern in enumerate(_YEAR_PATTERNS):
        match = re.search(pattern, entry)
        if match:
            return int(match.group(1)), "weak" if index >= _WEAK_FROM else "strong"
    return None, "none"


def extract_year(entry: str) -> Optional[int]:
    """Повертає рік видання або None якщо не знайдено."""
    return extract_year_with_confidence(entry)[0]


def extract_years(bibliography: dict[int, str]) -> dict[int, Optional[int]]:
    """Повертає dict[номер_джерела -> рік або None]."""
    return {num: extract_year(entry) for num, entry in bibliography.items()}


def extract_years_with_confidence(
    bibliography: dict[int, str],
) -> tuple[dict[int, Optional[int]], dict[int, str]]:
    """Повертає пару словників: роки та достовірність, обидва за номером джерела."""
    years: dict[int, Optional[int]] = {}
    confidence: dict[int, str] = {}
    for num, entry in bibliography.items():
        years[num], confidence[num] = extract_year_with_confidence(entry)
    return years, confidence
