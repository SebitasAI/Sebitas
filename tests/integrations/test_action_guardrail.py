"""Unit tests for the platform-level run_action guardrail.

These are pure unit tests: no DB, no Pipedream, no LiteLLM. They cover:

  - validate_params: missing required, unknown keys, type mismatches,
    no-op on healthy params
  - _response_seems_sparse: empty list, totalRecords=0, count=0, healthy
    responses, non-dict inputs
  - _identify_off_rich_flags: includeX=false, context=Basic, omitted
    rich flag, non-rich props ignored
  - annotate_sparse_result: returns None on healthy result, None when no
    rich flags are off, returns annotation when both conditions hold

The Pipedream component fetch is NOT exercised here -- that's covered by
the gateway integration test where `get_action_props` falls back to None
on import failure and the gateway short-circuits gracefully.
"""

from __future__ import annotations

import pytest

from app.integrations import action_guardrail as ag


# Shared fixture: a representative Gong action spec. Mirrors the shape
# `pipedream.get_component()` returns -- the test does not call the real
# function, it passes this list directly to the validator.
GONG_GET_EXTENSIVE_PROPS = [
    {"name": "callIds", "type": "string[]", "optional": False,
     "description": "List of call ids."},
    {"name": "includeParties", "type": "boolean", "optional": True,
     "description": "Include party info."},
    {"name": "includeContent", "type": "boolean", "optional": True,
     "description": "Include transcript content."},
    {"name": "context", "type": "string", "optional": True,
     "description": "Basic or Extended."},
    {"name": "limit", "type": "integer", "optional": True},
]


# --------------------------------------------------------------------------- #
# validate_params
# --------------------------------------------------------------------------- #


def test_validate_returns_none_on_healthy_params():
    out = ag.validate_params(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["abc"], "includeParties": True, "context": "Extended"},
    )
    assert out is None


def test_validate_returns_none_when_no_spec():
    assert ag.validate_params([], {"anything": 1}) is None


def test_validate_flags_missing_required():
    out = ag.validate_params(
        GONG_GET_EXTENSIVE_PROPS,
        {"includeParties": True},  # callIds missing
    )
    assert out is not None
    assert "callIds" in out["missing_required"]
    assert out["unknown_fields"] == []


def test_validate_flags_unknown_fields():
    out = ag.validate_params(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["abc"], "includeParites": True},  # typo'd flag
    )
    assert out is not None
    assert "includeParites" in out["unknown_fields"]


def test_validate_flags_boolean_type_mismatch():
    out = ag.validate_params(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["abc"], "includeParties": "yes"},  # string, not bool
    )
    assert out is not None
    assert any("includeParties" in e for e in out["type_errors"])


def test_validate_flags_integer_type_mismatch():
    out = ag.validate_params(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["abc"], "limit": "100"},  # string, not int
    )
    assert out is not None
    assert any("limit" in e for e in out["type_errors"])


# --------------------------------------------------------------------------- #
# _extract_items
# --------------------------------------------------------------------------- #


def test_extract_items_finds_top_level_calls():
    assert ag._extract_items({"calls": [{"id": 1}]}) == [{"id": 1}]


def test_extract_items_unwraps_ret_list():
    # The Pipedream Code-step wrapper: actual data lives under `ret`.
    assert ag._extract_items({"ret": [{"a": 1}], "os": "...", "t": 0}) == [{"a": 1}]


def test_extract_items_unwraps_records_items():
    out = ag._extract_items({"records": {"totalRecords": 1, "items": [{"id": 9}]}})
    assert out == [{"id": 9}]


def test_extract_items_returns_empty_on_zero_total():
    assert ag._extract_items({"records": {"totalRecords": 0}}) == []


def test_extract_items_passes_through_list():
    assert ag._extract_items([{"a": 1}]) == [{"a": 1}]


def test_extract_items_returns_none_on_unknown_shape():
    # An ack-only response (write action), no list anywhere.
    assert ag._extract_items({"ok": True}) is None


def test_extract_items_returns_none_on_non_collection():
    assert ag._extract_items("string") is None
    assert ag._extract_items(None) is None


# --------------------------------------------------------------------------- #
# _enrichment_field_for_flag
# --------------------------------------------------------------------------- #


def test_enrichment_field_for_include_flag():
    assert ag._enrichment_field_for_flag("includeParties") == "parties"
    assert ag._enrichment_field_for_flag("includeMedia") == "media"
    assert ag._enrichment_field_for_flag("includePublicComments") == "publicComments"


def test_enrichment_field_for_context():
    assert ag._enrichment_field_for_flag("context") == "context"


def test_enrichment_field_for_with_and_expand():
    assert ag._enrichment_field_for_flag("withParticipants") == "participants"
    assert ag._enrichment_field_for_flag("expandUser") == "user"


def test_enrichment_field_for_non_rich_flag():
    assert ag._enrichment_field_for_flag("maxResults") is None
    assert ag._enrichment_field_for_flag("fromDateTime") is None
    assert ag._enrichment_field_for_flag("") is None


# --------------------------------------------------------------------------- #
# _identify_off_rich_flags
# --------------------------------------------------------------------------- #


def test_off_flags_detects_include_false():
    off = ag._identify_off_rich_flags(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["x"], "includeParties": False, "includeContent": True},
    )
    assert "includeParties" in off
    assert "includeContent" not in off


def test_off_flags_detects_context_basic():
    off = ag._identify_off_rich_flags(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["x"], "context": "Basic"},
    )
    assert "context" in off


def test_off_flags_detects_context_extended_as_off_false():
    off = ag._identify_off_rich_flags(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["x"], "context": "Extended"},
    )
    assert "context" not in off


def test_off_flags_treats_omitted_rich_flag_as_off():
    off = ag._identify_off_rich_flags(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["x"]},  # no rich flags passed
    )
    assert "includeParties" in off
    assert "includeContent" in off
    assert "context" in off


def test_off_flags_ignores_non_rich_props():
    off = ag._identify_off_rich_flags(
        GONG_GET_EXTENSIVE_PROPS,
        {"callIds": ["x"], "limit": 1},
    )
    assert "callIds" not in off
    assert "limit" not in off


# --------------------------------------------------------------------------- #
# annotate_sparse_result (the orchestrator)
# --------------------------------------------------------------------------- #


def test_annotate_returns_none_when_response_is_not_list_shaped():
    out = ag.annotate_sparse_result(
        result={"ok": True},
        action_id="x", props=GONG_GET_EXTENSIVE_PROPS,
        params={"callIds": ["a"]},
    )
    assert out is None


def test_annotate_returns_none_when_all_rich_flags_on_and_items_enriched():
    items = [{"id": 1, "parties": [{"name": "x"}], "context": [], "content": "text"}]
    out = ag.annotate_sparse_result(
        result={"calls": items},
        action_id="gong-x", props=GONG_GET_EXTENSIVE_PROPS,
        params={
            "callIds": ["x"],
            "includeParties": True, "includeContent": True,
            "context": "Extended",
        },
    )
    assert out is None


def test_annotate_emits_empty_list_hint_when_items_empty_and_rich_off():
    out = ag.annotate_sparse_result(
        result={"calls": []},
        action_id="gong-list-calls", props=GONG_GET_EXTENSIVE_PROPS,
        params={"callIds": ["x"], "includeParties": False, "context": "Basic"},
    )
    assert out is not None
    assert out["platform_hint"] is True
    assert "includeParties" in out["off_flags"]
    assert "Empty list" in out["observation"]


def test_annotate_emits_missing_enrichment_hint_when_items_lack_field():
    # The MercadoLibre scenario reproduced: items returned, but they
    # don't carry the `parties` / `context` fields the user needs to
    # match the call to a company name.
    items_without_parties = [{"id": 1, "metaData": {"title": "x"}}]
    out = ag.annotate_sparse_result(
        result={"ret": items_without_parties},
        action_id="gong-get-extensive-data", props=GONG_GET_EXTENSIVE_PROPS,
        params={"callIds": ["x"]},
    )
    assert out is not None
    assert out["platform_hint"] is True
    assert "parties" in out["missing_fields"]
    assert "context" in out["missing_fields"]
    assert "includeParties" in out["off_flags"]


def test_annotate_returns_none_when_items_already_have_enrichment():
    items = [
        {"id": 1, "metaData": {"title": "MercadoLibre call"},
         "parties": [{"name": "X"}], "context": [{"system": "Salesforce"}],
         "content": "transcript"},
    ]
    out = ag.annotate_sparse_result(
        result={"ret": items},
        action_id="gong-get-extensive-data", props=GONG_GET_EXTENSIVE_PROPS,
        params={
            "callIds": ["x"],
            "includeParties": True, "includeContent": True,
            "context": "Extended",
        },
    )
    assert out is None
