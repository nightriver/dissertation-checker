"""
types.py
Спільні типи даних для всіх модулів пакету parser.
"""

from typing import TypedDict


class LineItem(TypedDict):
    """Один рядок документа з номером сторінки."""
    line: str
    page: int | None
