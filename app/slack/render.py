"""Convert agent text output into Slack Block Kit blocks.

Slack's mrkdwn does not render Markdown tables, ATX headings, or
fenced code blocks correctly when posted as plain `text=...`. The
agent emits those naturally; the renderer here translates them into
Block Kit equivalents:

  - `## Heading`            -> `header` block
  - `| col | col | ... |`   -> `rich_text` / `rich_text_preformatted`
                                with space-aligned columns
  - ` ``` code ``` `        -> `rich_text` / `rich_text_preformatted`
  - prose paragraphs        -> `section` block with mrkdwn

Returns a list of blocks suitable for `client.chat_postMessage(blocks=...)`.
Callers should also pass `text=` as a notification fallback (Slack
requires it for screen readers and mobile previews).

Tested explicitly against the patterns Misterr's agent produces today:
CRM history tables, multi-section reports, code snippets, mixed prose
and markdown.

Limits enforced:
  - Slack hard cap: 50 blocks per message. If we'd emit more, the
    tail is collapsed into a single "respuesta truncada" notice.
  - `header` text capped at 150 chars (Slack limit).
  - `section.text.text` capped at 3000 chars (Slack limit). Long
    paragraphs are split across multiple sections.
"""

from __future__ import annotations

import re
from typing import Iterator

# Slack hard limits.
MAX_BLOCKS = 50
MAX_HEADER_TEXT = 150
MAX_SECTION_TEXT = 3000


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,3})\s+(.+?)\s*#*\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _is_table_row(line: str) -> bool:
    """A pipe-bordered Markdown table row: starts and/or ends with `|`
    and contains at least one more `|`. We don't require strict
    bordering -- the agent occasionally omits the leading or trailing
    pipe and we still want to render it correctly."""
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.count("|") >= 2 and (
        stripped.startswith("|") or stripped.endswith("|") or " | " in stripped
    )


def _parse_table_rows(lines: list[str]) -> list[list[str]]:
    """Split each pipe-delimited row into trimmed cells, dropping empty
    leading/trailing cells caused by bordering pipes."""
    out: list[list[str]] = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        out.append(cells)
    return out


def _format_table_aligned(rows: list[list[str]]) -> str:
    """Space-aligned, monospace-friendly rendering. Each column is
    padded to the max width of any cell in that column. Numeric-only
    columns are right-aligned; everything else is left-aligned (a
    quick heuristic: if every non-empty cell in a column matches a
    numeric pattern, right-align).
    """
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    # Normalise: pad short rows so zip() doesn't drop cells.
    norm = [r + [""] * (cols - len(r)) for r in rows]
    widths = [max(len(r[c]) for r in norm) for c in range(cols)]
    numeric_pat = re.compile(r"^[\-\$€£¥]?\s*[\d.,]+\s*[KMBkmb%]?\s*$")
    # Right-align decisions look ONLY at the data rows. The first row
    # is almost always a header (`Ventas`, `ARR`, `Total`) which isn't
    # numeric and would otherwise pull the whole column to left-align.
    data_rows = norm[1:] if len(norm) > 1 else norm
    right_align = [
        bool(data_rows)
        and any(r[c] for r in data_rows)  # at least one non-empty cell
        and all(
            numeric_pat.match(r[c]) for r in data_rows if r[c]
        )
        for c in range(cols)
    ]
    lines: list[str] = []
    for r in norm:
        cells = []
        for c, cell in enumerate(r):
            if right_align[c]:
                cells.append(cell.rjust(widths[c]))
            else:
                cells.append(cell.ljust(widths[c]))
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def _chunk(text: str, limit: int) -> list[str]:
    """Split `text` into chunks no longer than `limit`. Tries to break
    on a paragraph boundary, then a line boundary, else hard cuts."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        out.append(remaining)
    return out


def _segments(text: str) -> Iterator[tuple[str, str]]:
    """Yield (kind, body) tuples. Kinds: `heading`, `table`,
    `code_block`, `prose`. Body is the raw segment content."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Fenced code block.
        if line.strip().startswith("```"):
            j = i + 1
            body_lines: list[str] = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                body_lines.append(lines[j])
                j += 1
            yield ("code_block", "\n".join(body_lines))
            i = j + 1 if j < len(lines) else j
            continue

        # ATX heading. Slack's `header` block doesn't support mrkdwn
        # markup; strip * and _ from the captured text.
        m = _HEADING_RE.match(line)
        if m:
            heading = m.group(2)
            heading = re.sub(r"\*([^*]+)\*", r"\1", heading)
            heading = re.sub(r"_([^_]+)_", r"\1", heading)
            yield ("heading", heading.strip())
            i += 1
            continue

        # Markdown table. We require at least two rows AND a separator
        # row of dashes to avoid false positives on prose that happens
        # to contain `|`.
        if _is_table_row(line) and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            block_lines = [line]
            j = i + 1
            # Eat the separator row.
            block_lines.append(lines[j])
            j += 1
            while j < len(lines) and _is_table_row(lines[j]):
                block_lines.append(lines[j])
                j += 1
            rows_with_sep = _parse_table_rows(block_lines)
            # Drop the separator row (it's the second row, all dashes).
            rows = [r for r in rows_with_sep if not all(
                set(c.replace(":", "").strip()) <= {"-"} for c in r if c
            )]
            yield ("table", _format_table_aligned(rows))
            i = j
            continue

        # Prose paragraph: accumulate until blank line / next special.
        para_lines = []
        while i < len(lines):
            curr = lines[i]
            if not curr.strip():
                break
            if curr.strip().startswith("```"):
                break
            if _HEADING_RE.match(curr):
                break
            if (
                _is_table_row(curr)
                and i + 1 < len(lines)
                and _TABLE_SEP_RE.match(lines[i + 1])
            ):
                break
            para_lines.append(curr)
            i += 1
        if para_lines:
            yield ("prose", "\n".join(para_lines))
        # Skip blank lines between segments.
        while i < len(lines) and not lines[i].strip():
            i += 1


def _header_block(text: str) -> dict:
    capped = text[:MAX_HEADER_TEXT]
    return {"type": "header", "text": {"type": "plain_text", "text": capped, "emoji": True}}


def _section_block(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _preformatted_block(text: str) -> dict:
    """rich_text wrapping a rich_text_preformatted element. This renders
    as a monospace, code-styled block in Slack -- the only reliable way
    to show aligned columns or code without raw-pipe leak."""
    return {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_preformatted",
                "elements": [{"type": "text", "text": text}],
            }
        ],
    }


def text_to_blocks(text: str) -> list[dict]:
    """Convert agent output into a Block Kit block list.

    Empty / whitespace-only input returns a single section with the
    placeholder "(sin respuesta)" so the post still succeeds.

    Honors Slack's 50-block cap: surplus blocks are dropped and a
    final section flags the truncation.
    """
    if not text or not text.strip():
        return [_section_block("_(sin respuesta)_")]

    blocks: list[dict] = []
    for kind, body in _segments(text):
        if kind == "heading":
            if not body.strip():
                continue
            blocks.append(_header_block(body))
        elif kind == "table":
            if not body.strip():
                continue
            blocks.append(_preformatted_block(body))
        elif kind == "code_block":
            blocks.append(_preformatted_block(body))
        else:  # prose
            for chunk in _chunk(body, MAX_SECTION_TEXT):
                if chunk.strip():
                    blocks.append(_section_block(chunk))

    if not blocks:
        return [_section_block("_(sin respuesta)_")]

    if len(blocks) > MAX_BLOCKS:
        blocks = blocks[: MAX_BLOCKS - 1] + [
            _section_block("_(respuesta truncada por longitud)_")
        ]
    return blocks


__all__ = ["text_to_blocks"]
