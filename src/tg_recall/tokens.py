"""Token estimate for output budgets, without a tokenizer dependency."""

from __future__ import annotations

import math

# Per-character weights calibrated against BPE counts on mixed Russian/English chat
# text: prose lands within about +/-15%, while URLs and emoji are over-estimated
# (the safe direction for a budget).
_WEIGHTS = {"letter": 0.25, "digit": 0.5, "space": 0.15, "punct": 0.6, "cyrillic": 0.45, "bmp": 1.0, "astral": 2.0}
_MARGIN = 1.1


def estimate_text_tokens(text: str) -> int:
    """Estimate model tokens for text (Cyrillic-aware)."""

    total = 0.0
    weights = _WEIGHTS
    for char in text:
        if char.isascii():
            if char.isalpha():
                total += weights["letter"]
            elif char.isdigit():
                total += weights["digit"]
            elif char.isspace():
                total += weights["space"]
            else:
                total += weights["punct"]
        elif "Ѐ" <= char <= "ӿ":
            total += weights["cyrillic"]
        elif ord(char) > 0xFFFF:
            total += weights["astral"]
        else:
            total += weights["bmp"]
    return math.ceil(total * _MARGIN)
