"""Text helpers: parsing list-like fields, normalising names, extracting citable snippets."""

from __future__ import annotations

import ast
import json
import math
import re
from typing import Any, Iterable

_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null", "na", "n/a", "[]", "{}"}:
        return True
    return False


def split_camel(s: str) -> str:
    """'cardiacSurgery' -> 'cardiac Surgery' (specialty codes in the dataset are camelCase)."""
    return _CAMEL_RE.sub(" ", s)


def norm(s: Any) -> str:
    """Normalise free text for matching: camelCase split, lowercase, punctuation -> space."""
    if is_missing(s):
        return ""
    s = split_camel(str(s)).lower()
    s = s.replace("&", " and ")
    s = _NON_ALNUM_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _flatten(items: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for it in items:
        if isinstance(it, (list, tuple)) or (hasattr(it, "tolist") and not isinstance(it, str)):
            out.extend(_flatten(it.tolist() if hasattr(it, "tolist") else it))
        elif isinstance(it, dict):
            out.extend(str(v) for v in it.values() if not is_missing(v))
        elif not is_missing(it):
            out.append(str(it).strip())
    return out


def parse_list(value: Any) -> list[str]:
    """Parse a list-like field robustly.

    Handles real arrays, JSON arrays, Python-literal lists, and delimited strings.
    Items are stripped and de-duplicated (order kept).
    """
    if value is None:
        return []
    if not isinstance(value, str):
        if isinstance(value, float) and math.isnan(value):
            return []
        if isinstance(value, (list, tuple, dict)) or hasattr(value, "tolist"):
            items = _flatten(value if not hasattr(value, "tolist") else value.tolist())
            return _dedupe(items)
        value = str(value)
    s = value.strip()
    if is_missing(s):
        return []
    items: list[str] | None = None
    if s.startswith("[") or s.startswith("{"):
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(s)
                items = _flatten(parsed if isinstance(parsed, (list, tuple)) else [parsed])
                break
            except Exception:
                continue
        if items is None:  # malformed bracketed string
            inner = s.strip("[]{}")
            items = [p.strip().strip("'\"") for p in re.split(r"\"\s*,\s*\"|'\s*,\s*'", inner)]
    else:
        if re.search(r"[;|\n]", s):
            items = re.split(r"[;|\n]+", s)
        else:
            parts = [p for p in s.split(",")]
            avg = sum(len(p) for p in parts) / max(len(parts), 1)
            items = parts if len(parts) > 1 and avg < 40 else [s]
    return _dedupe([i.strip().strip("'\"").strip() for i in items])


def _dedupe(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if is_missing(i):
            continue
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out


def to_json_list(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False)


def from_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    if is_missing(value):
        return []
    try:
        v = json.loads(value)
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    except Exception:
        return parse_list(value)


def humanize_code(item: str) -> str:
    """Specialty codes like 'gynecologyAndObstetrics' -> 'Gynecology And Obstetrics'."""
    if " " in item:
        return item
    return split_camel(item).replace("_", " ").strip().capitalize()


def snippet(text: str, start: int, end: int, width: int = 220) -> str:
    """Return the sentence (or window) around a match, for citation."""
    if len(text) <= width:
        return text.strip()
    # sentence boundaries
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    left = 0 if left < 0 else left + 1
    right_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    if right - left > width:
        half = (width - (end - start)) // 2
        left = max(0, start - half)
        right = min(len(text), end + half)
    out = text[left:right].strip()
    if left > 0:
        out = "…" + out
    if right < len(text):
        out = out + "…"
    return out


def clean_pincode(value: Any) -> str | None:
    """Extract a valid Indian 6-digit PIN (first digit 1-9)."""
    if is_missing(value):
        return None
    s = str(value)
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    m = re.search(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)", s)
    return m.group(1) + m.group(2) if m else None


def find_pincodes(text: str) -> list[str]:
    if not text:
        return []
    return sorted({a + b for a, b in re.findall(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)", text)})


def to_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        f = float(str(value).replace(",", "").strip())
        return None if math.isnan(f) else f
    except ValueError:
        m = re.search(r"-?\d+(\.\d+)?", str(value).replace(",", ""))
        return float(m.group(0)) if m else None
