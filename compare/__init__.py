"""Порівняння двох завантажених документів."""

from compare.normalize import normalize_search_token, tokenize_lines
from compare.types import CompareToken, TokenPart

__all__ = ["CompareToken", "TokenPart", "normalize_search_token", "tokenize_lines"]
