"""Tests for `app.slack.render.text_to_blocks`.

The renderer is pure: input text → list of Block Kit dicts. No Slack
client, no I/O. We pin the contract:

  - Markdown tables become `rich_text` + `rich_text_preformatted` with
    space-aligned columns. NEVER leave raw pipes in a section block.
  - ATX headings become `header` blocks (max 150 chars).
  - Fenced code blocks become preformatted blocks.
  - Prose becomes `section` blocks with mrkdwn.
  - Empty / whitespace input still produces a renderable block list.
  - Slack hard caps respected (50 blocks max, 150 char header, 3000
    char section text).
"""

from __future__ import annotations

import pytest

from app.slack.render import (
    MAX_BLOCKS,
    MAX_HEADER_TEXT,
    MAX_SECTION_TEXT,
    text_to_blocks,
)


def _kinds(blocks: list[dict]) -> list[str]:
    """Flat list of block types in order. `rich_text` shows up as
    'pre' if the only inner element is `rich_text_preformatted`,
    else 'rich_text'. Lets tests pattern-match on shape quickly."""
    out = []
    for b in blocks:
        t = b["type"]
        if t == "rich_text" and b.get("elements", [{}])[0].get("type") == "rich_text_preformatted":
            out.append("pre")
        else:
            out.append(t)
    return out


def _pre_text(block: dict) -> str:
    """Extract the inner text of a preformatted block."""
    el = block["elements"][0]["elements"][0]
    assert el["type"] == "text"
    return el["text"]


# --------------------------------------------------------------------------- #
# Heading -> header block
# --------------------------------------------------------------------------- #


def test_h2_becomes_header_block():
    blocks = text_to_blocks("## Historial de oportunidades\n\nDetalle abajo.")
    assert _kinds(blocks) == ["header", "section"]
    assert blocks[0]["text"]["text"] == "Historial de oportunidades"
    assert blocks[1]["text"]["text"] == "Detalle abajo."


def test_h1_h2_h3_all_become_header_blocks():
    blocks = text_to_blocks("# Top\n\n## Mid\n\n### Sub")
    assert _kinds(blocks) == ["header", "header", "header"]
    assert [b["text"]["text"] for b in blocks] == ["Top", "Mid", "Sub"]


def test_header_strips_markdown_bold_and_italic():
    blocks = text_to_blocks("## *Resumen* del _trimestre_")
    assert blocks[0]["text"]["text"] == "Resumen del trimestre"


def test_header_text_capped_at_150():
    long = "x" * 300
    blocks = text_to_blocks(f"## {long}")
    assert blocks[0]["type"] == "header"
    assert len(blocks[0]["text"]["text"]) == MAX_HEADER_TEXT


# --------------------------------------------------------------------------- #
# Markdown tables -> rich_text_preformatted
# --------------------------------------------------------------------------- #


def test_basic_table_renders_aligned():
    md = (
        "| Fecha | Cliente | Estado | ARR |\n"
        "|-------|---------|--------|-----|\n"
        "| abr 2024 | iFood Brazil | Lost | $120K |\n"
        "| sep 2024 | iFood | Won | $514K |\n"
    )
    blocks = text_to_blocks(md)
    assert _kinds(blocks) == ["pre"]
    aligned = _pre_text(blocks[0])
    # Pipes must NOT survive into the output.
    assert "|" not in aligned
    # All rows present.
    assert "Fecha" in aligned and "Cliente" in aligned
    assert "abr 2024" in aligned and "iFood Brazil" in aligned
    assert "sep 2024" in aligned and "iFood" in aligned
    # Columns aligned: every line should have the data at the same
    # column offset for "Cliente".
    offsets = [ln.find("iFood") for ln in aligned.splitlines() if "iFood" in ln]
    assert len(offsets) == 2
    assert offsets[0] == offsets[1]


def test_table_right_aligns_numeric_columns():
    md = (
        "| Mes | Ventas |\n"
        "|-----|--------|\n"
        "| Q1  | 5      |\n"
        "| Q2  | 1000   |\n"
    )
    blocks = text_to_blocks(md)
    aligned = _pre_text(blocks[0])
    # The two value rows should have "5" right-aligned: it must sit
    # at the SAME ending column as "1000".
    rows = [ln for ln in aligned.splitlines() if ln and ln[0].isalnum()]
    assert len(rows) >= 3
    # End column of "5" matches end column of "1000".
    row_q1 = next(ln for ln in rows if "Q1" in ln)
    row_q2 = next(ln for ln in rows if "Q2" in ln)
    assert row_q1.rstrip().endswith("5")
    assert row_q2.rstrip().endswith("1000")
    # And the "5" appears AT the same end-offset as "1000".
    assert len(row_q1.rstrip()) == len(row_q2.rstrip())


def test_table_with_mixed_prose():
    md = (
        "Aquí va el resumen:\n"
        "\n"
        "| col1 | col2 |\n"
        "|------|------|\n"
        "| a    | b    |\n"
        "\n"
        "Eso es todo."
    )
    blocks = text_to_blocks(md)
    assert _kinds(blocks) == ["section", "pre", "section"]
    assert blocks[0]["text"]["text"] == "Aquí va el resumen:"
    assert blocks[2]["text"]["text"] == "Eso es todo."


def test_lone_pipe_in_prose_is_not_a_table():
    """A pipe inside prose without a separator row stays prose."""
    blocks = text_to_blocks("El owner es Alice | el AE es Bob.")
    assert _kinds(blocks) == ["section"]
    assert "|" in blocks[0]["text"]["text"]


# --------------------------------------------------------------------------- #
# Code blocks
# --------------------------------------------------------------------------- #


def test_fenced_code_block_becomes_preformatted():
    text = "Mira:\n\n```\nselect * from foo;\nselect 1;\n```\n\nFin."
    blocks = text_to_blocks(text)
    assert _kinds(blocks) == ["section", "pre", "section"]
    assert "select * from foo;" in _pre_text(blocks[1])
    assert "select 1;" in _pre_text(blocks[1])


# --------------------------------------------------------------------------- #
# Prose
# --------------------------------------------------------------------------- #


def test_pure_prose_is_one_section():
    blocks = text_to_blocks("Hola Sam.\nTodo bien por aquí.")
    assert _kinds(blocks) == ["section"]
    assert "Hola Sam." in blocks[0]["text"]["text"]


def test_long_prose_chunked_into_sections():
    long = "linea de texto. " * 300  # ~5K chars
    blocks = text_to_blocks(long)
    assert all(b["type"] == "section" for b in blocks)
    assert len(blocks) >= 2
    for b in blocks:
        assert len(b["text"]["text"]) <= MAX_SECTION_TEXT


def test_empty_input_returns_placeholder():
    blocks = text_to_blocks("")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "section"
    assert "sin respuesta" in blocks[0]["text"]["text"]


def test_whitespace_only_input_returns_placeholder():
    blocks = text_to_blocks("   \n\n  \n")
    assert len(blocks) == 1
    assert "sin respuesta" in blocks[0]["text"]["text"]


# --------------------------------------------------------------------------- #
# Slack limits
# --------------------------------------------------------------------------- #


def test_more_than_50_blocks_truncated():
    paragraphs = "\n\n".join(f"paragraph {i}." for i in range(80))
    blocks = text_to_blocks(paragraphs)
    assert len(blocks) <= MAX_BLOCKS
    # The last block flags truncation.
    last = blocks[-1]
    assert last["type"] == "section"
    assert "truncada" in last["text"]["text"].lower()


# --------------------------------------------------------------------------- #
# The exact bug Sam reported
# --------------------------------------------------------------------------- #


def test_crm_history_example_from_report():
    """Pin: the screenshot from 2026-06-03 stops leaking raw pipes
    after this lands."""
    md = (
        "## Historial de oportunidades (Salesforce)\n"
        "\n"
        "| Cierre | Opp | Tipo | Estado | ACV/ARR |\n"
        "|---|---|---|---|---|\n"
        "| abr 2024 | iFood Brazil - MIN | New | Lost | $120K |\n"
        "| may 2024 | iFood | New | Lost | — |\n"
        "| sep 2024 | iFood - MIN | New | Won | $514.5K |\n"
    )
    blocks = text_to_blocks(md)
    # First block: header. Second: the aligned table.
    assert blocks[0]["type"] == "header"
    assert blocks[0]["text"]["text"] == "Historial de oportunidades (Salesforce)"
    assert blocks[1]["type"] == "rich_text"
    aligned = _pre_text(blocks[1])
    # The smoking gun: zero pipe characters left.
    assert "|" not in aligned
    # Every row's data made it through.
    for needle in [
        "Cierre", "Opp", "Tipo", "Estado", "ACV/ARR",
        "abr 2024", "iFood Brazil - MIN", "Lost", "$120K",
        "sep 2024", "$514.5K",
    ]:
        assert needle in aligned
