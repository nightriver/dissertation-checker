"""
types.py
Спільні типи даних для всіх модулів парсера.
"""

from __future__ import annotations
from typing import TypedDict


class LineItem(TypedDict):
    """Стандартна структура рядка документа, що використовується у всіх модулях."""
    line: str
    page: int | None
