"""Unit tests for `gateway._render_truncated` / `_extract_cursor_hint`.

The byte cap, item cap, and cursor hint logic is pure — no DB, no
provider, no LiteLLM. The tests pin the contract:

  - list responses keep WHOLE items, never half-items
  - byte cap stops the loop before item cap fires (and vice versa)
  - at least one item survives even if it exceeds the byte cap (so
    the agent still sees the row shape)
  - footer fires only when items are dropped, with count + cursor
    when available
  - non-list responses fall back to byte truncation against the
    `ret`-unwrapped `out`, NOT the raw `result` (preserves the
    pre-existing rendering of small write-action responses)
"""

from __future__ import annotations

import json

import pytest

from app.integrations import gateway as gw


# --------------------------------------------------------------------------- #
# _extract_cursor_hint
# --------------------------------------------------------------------------- #


def test_cursor_hint_picks_up_records_cursor():
    h = gw._extract_cursor_hint({"records": {"cursor": "abc"}, "calls": []})
    assert "cursor='abc'" in h


def test_cursor_hint_unwraps_pipedream_ret_records():
    # Gong list-calls shape after Pipedream wrap.
    h = gw._extract_cursor_hint({"ret": {"records": {"cursor": "xyz"}, "calls": []}})
    assert "cursor='xyz'" in h


def test_cursor_hint_truncates_long_cursors():
    long = "a" * 200
    h = gw._extract_cursor_hint({"records": {"cursor": long}})
    assert "cursor='" in h
    # 80-char cap + '...' suffix.
    assert h.count("a") <= 81


def test_cursor_hint_returns_empty_string_when_none():
    assert gw._extract_cursor_hint({"records": {"calls": []}}) == ""
    assert gw._extract_cursor_hint({"ok": True}) == ""
    assert gw._extract_cursor_hint("string") == ""


# --------------------------------------------------------------------------- #
# _render_truncated — non-list path
# --------------------------------------------------------------------------- #


def test_render_truncated_passes_small_dict_through():
    out = {"ok": True, "id": "abc"}
    text = gw._render_truncated(result={"ret": out}, fallback_out=out)
    # No truncation needed.
    assert text == str(out)


def test_render_truncated_byte_caps_huge_dict():
    big = {"k": "x" * 50_000}
    text = gw._render_truncated(result={"ret": big}, fallback_out=big)
    assert "[truncated to" in text
    # Body is capped at the non-list ceiling.
    assert len(text) <= gw._RESULT_NONLIST_MAX_BYTES + 50


# --------------------------------------------------------------------------- #
# _render_truncated — list path
# --------------------------------------------------------------------------- #


def test_render_truncated_keeps_full_items_under_caps():
    items = [{"id": i, "name": f"row-{i}"} for i in range(5)]
    result = {"ret": items}
    text = gw._render_truncated(result=result, fallback_out=items)
    # No footer when nothing was dropped.
    assert "[truncated:" not in text
    # Each item is reachable as a parseable dict-ish substring.
    for i in range(5):
        assert f"'id': {i}" in text


def test_render_truncated_stops_at_item_cap():
    items = [{"id": i} for i in range(100)]
    result = {"ret": items}
    text = gw._render_truncated(result=result, fallback_out=items)
    assert "[truncated:" in text
    assert f"of {len(items)} items" in text
    # Should keep exactly _RESULT_MAX_ITEMS items.
    assert text.startswith("[{")
    assert f"showing {gw._RESULT_MAX_ITEMS} of " in text


def test_render_truncated_stops_at_byte_cap_before_item_cap():
    # Each item is ~1KB, so 30 items already passes 20KB byte cap.
    items = [{"id": i, "blob": "x" * 1024} for i in range(35)]
    result = {"ret": items}
    text = gw._render_truncated(result=result, fallback_out=items)
    assert "[truncated:" in text
    # Body itself is bounded.
    body = text.split("\n\n[truncated:")[0]
    assert len(body) <= gw._RESULT_MAX_BYTES + 200  # +200 for separator slop


def test_render_truncated_keeps_at_least_one_item_even_if_oversized():
    # Single item bigger than the byte cap. Must still appear so the
    # agent sees the row shape.
    big = {"id": 1, "blob": "y" * (gw._RESULT_MAX_BYTES * 2)}
    result = {"ret": [big, {"id": 2}]}
    text = gw._render_truncated(result=result, fallback_out=[big, {"id": 2}])
    assert "'id': 1" in text
    assert "[truncated:" in text
    assert "showing 1 of 2 items" in text


def test_render_truncated_footer_includes_cursor_when_present():
    items = [{"id": i} for i in range(50)]
    result = {"ret": {"records": {"cursor": "PAGE_TOKEN"}, "calls": items}}
    text = gw._render_truncated(result=result, fallback_out=result["ret"])
    assert "[truncated:" in text
    assert "cursor='PAGE_TOKEN'" in text


def test_render_truncated_empty_list_returns_fallback():
    result = {"ret": []}
    text = gw._render_truncated(result=result, fallback_out=[])
    assert text == "[]"
    assert "[truncated:" not in text
