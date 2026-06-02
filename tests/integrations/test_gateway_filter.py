"""Unit tests for the server-side `filter_substring` deep-search helper.

The deep matcher is pure: no DB, no provider. The gateway's `run_action`
integration is exercised live via `scripts/test_run_action_filter.py`.
"""

from __future__ import annotations

import pytest

from app.integrations import gateway as gw


def test_deep_match_finds_substring_in_top_level_string():
    assert gw._deep_string_match({"title": "MercadoLibre call"}, "mercadolibre") is True


def test_deep_match_is_case_insensitive():
    assert gw._deep_string_match({"title": "MERCADOLIBRE"}, "mercadolibre") is True
    assert gw._deep_string_match({"title": "mercadolibre"}, "MercadoLibre") is False  # needle should be pre-lowercased


def test_deep_match_walks_nested_lists():
    item = {
        "parties": [
            {"emailAddress": "alice@simetrik.com"},
            {"emailAddress": "bob@mercadolibre.com"},
        ]
    }
    assert gw._deep_string_match(item, "mercadolibre.com") is True


def test_deep_match_walks_nested_dicts():
    item = {
        "context": [
            {
                "system": "Salesforce",
                "objects": [
                    {"fields": [{"name": "Name", "value": "Mercado Libre"}]},
                ],
            }
        ]
    }
    assert gw._deep_string_match(item, "mercado libre") is True


def test_deep_match_finds_in_dict_keys():
    item = {"mercadolibre_id": "abc-123"}
    assert gw._deep_string_match(item, "mercadolibre") is True


def test_deep_match_coerces_scalars():
    assert gw._deep_string_match({"amount": 12345}, "234") is True
    assert gw._deep_string_match({"active": True}, "true") is True


def test_deep_match_returns_false_when_absent():
    item = {"parties": [{"emailAddress": "alice@simetrik.com"}], "id": 1}
    assert gw._deep_string_match(item, "mercadolibre") is False


def test_deep_match_handles_none():
    assert gw._deep_string_match(None, "x") is False


def test_deep_match_handles_empty_collections():
    assert gw._deep_string_match([], "x") is False
    assert gw._deep_string_match({}, "x") is False


def test_deep_match_realistic_gong_row_shape():
    # Stripped-down version of an actual Gong extensive-data row.
    row = {
        "metaData": {"id": "123", "title": "Weekly Sync", "started": "2026-05-26"},
        "parties": [
            {"name": "Alice", "emailAddress": "alice@simetrik.com", "affiliation": "Internal"},
            {"name": "Pedro", "emailAddress": "pedro@mercadolibre.com", "affiliation": "External"},
        ],
        "context": [
            {
                "system": "Salesforce",
                "objects": [
                    {
                        "objectType": "Account",
                        "fields": [{"name": "Name", "value": "MercadoLibre"}],
                    }
                ],
            }
        ],
    }
    # Should match via parties OR via context.
    assert gw._deep_string_match(row, "mercadolibre") is True
    assert gw._deep_string_match(row, "mercadolibre.com") is True
    # Negative control.
    assert gw._deep_string_match(row, "rappi") is False
