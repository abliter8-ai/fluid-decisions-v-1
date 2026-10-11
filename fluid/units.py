"""Split a tool result into small units for the two-stage post-result check.

Pure and deterministic. The invariant the locate-then-confirm check relies on is
total coverage: the units, concatenated in order, reproduce the input exactly
(``"".join(split_units(text, policy)) == text``). A located-and-confirmed unit
therefore maps back to a real span of the result, and no text slips the check by
falling between units.

Paragraphs split at blank lines; a fenced code block stays one unit so an
instruction is not quoted out of a code sample; a paragraph longer than
``max_unit_chars`` splits at sentence boundaries; units shorter than
``min_unit_chars`` merge into a neighbour; and the whole is capped at
``max_units`` by evenly grouping adjacent units, so a pathological result cannot
explode the question count. None of these steps ever adds, drops or reorders a
character.
"""
from __future__ import annotations

import re
from typing import Any

_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _config(policy: dict[str, Any]) -> dict[str, Any]:
    units = policy.get("units") if isinstance(policy, dict) else None
    return units if isinstance(units, dict) else {}


def _fence_marker(line: str) -> str | None:
    match = _FENCE.match(line)
    return match.group(1)[0] * 3 if match else None


def _closes(line: str, opener: str) -> bool:
    marker = _FENCE.match(line)
    return marker is not None and marker.group(1)[0] == opener[0]


def _blocks(text: str) -> list[tuple[str, str]]:
    """Segment the text into ``code`` and ``para`` blocks, preserving every byte.

    A paragraph carries its own trailing blank lines so the blank-line separators
    are never lost. A fence with no close runs to the end of the text."""
    lines = text.splitlines(keepends=True)
    blocks: list[tuple[str, str]] = []
    index, total = 0, len(lines)
    while index < total:
        opener = _fence_marker(lines[index])
        if opener is not None:
            buffer = [lines[index]]
            index += 1
            while index < total:
                buffer.append(lines[index])
                closed = _closes(lines[index], opener)
                index += 1
                if closed:
                    break
            blocks.append(("".join(buffer), "code"))
            continue
        buffer = []
        while index < total and lines[index].strip() != "" and _fence_marker(lines[index]) is None:
            buffer.append(lines[index])
            index += 1
        while index < total and lines[index].strip() == "":
            buffer.append(lines[index])
            index += 1
        blocks.append(("".join(buffer), "para"))
    return blocks


def _sentences(text: str) -> list[str]:
    bounds = sorted({0, len(text), *(m.end() for m in _SENTENCE_END.finditer(text))})
    return [text[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]


def _split_long(text: str, max_chars: int) -> list[str]:
    """Split an over-long paragraph at sentence boundaries, hard-cutting a single
    sentence that still exceeds the limit. Concatenation reproduces the input."""
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    current = ""
    for sentence in _sentences(text):
        while len(sentence) > max_chars:
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + len(sentence) > max_chars:
            out.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        out.append(current)
    return out


def _merge_short(units: list[list[str]], min_chars: int) -> list[list[str]]:
    """Merge a non-code unit shorter than ``min_chars`` into an adjacent non-code
    unit, preferring the previous. A short unit between code blocks is left as is."""
    units = [list(unit) for unit in units]
    changed = True
    while changed and len(units) > 1:
        changed = False
        for index, (text_u, kind) in enumerate(units):
            if kind == "code" or len(text_u) >= min_chars:
                continue
            if index > 0 and units[index - 1][1] != "code":
                units[index - 1][0] += text_u
                del units[index]
                changed = True
                break
            if index + 1 < len(units) and units[index + 1][1] != "code":
                units[index + 1][0] = text_u + units[index + 1][0]
                del units[index]
                changed = True
                break
    return units


def _rechunk_even(units: list[list[str]], cap: int) -> list[list[str]]:
    """Group adjacent units into exactly ``cap`` groups of near-equal count,
    concatenating each group's text. Coverage is preserved."""
    count = len(units)
    base, extra = divmod(count, cap)
    grouped: list[list[str]] = []
    start = 0
    for group in range(cap):
        size = base + (1 if group < extra else 0)
        grouped.append(["".join(text for text, _ in units[start:start + size]), "para"])
        start += size
    return grouped


def redact_units(units: list[str], confirmed: list[int], marker: str) -> str:
    """Replace each confirmed unit with ``marker`` and join (IP-007 section 4).

    Relies on the full-coverage invariant: the surviving units are returned byte
    for byte and only the confirmed spans become the marker, so nothing outside a
    confirmed unit is altered. One marker per confirmed unit."""
    chosen = set(confirmed)
    return "".join(marker if index in chosen else unit for index, unit in enumerate(units))


def split_units(text: str, policy: dict[str, Any]) -> list[str]:
    """Return the result split into units with full byte coverage (see module
    docstring). Empty text yields no units."""
    if not isinstance(text, str) or not text:
        return []
    config = _config(policy)
    max_chars = int(config.get("max_unit_chars", 600))
    min_chars = int(config.get("min_unit_chars", 40))
    cap = max(1, int(config.get("max_units", 64)))
    if max_chars < 1:
        max_chars = 600
    units: list[list[str]] = []
    for block_text, kind in _blocks(text):
        if kind == "code":
            units.append([block_text, "code"])
        else:
            units.extend([piece, "para"] for piece in _split_long(block_text, max_chars))
    units = _merge_short(units, min_chars)
    if len(units) > cap:
        units = _rechunk_even(units, cap)
    return [text for text, _ in units]
