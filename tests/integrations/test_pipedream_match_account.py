"""Pin the slug-matching contract of PipedreamProvider.match_account_for_app.

The bug we're guarding against (observed 2026-06-03, Antiff workspace):
user asks "@misterr conectame salesforce", connect flow resolves
"salesforce" -> "salesforce_rest_api" upstream, OAuth completes,
account exists at Pipedream as `salesforce_rest_api`. The poll
fallback then asked `match_account_for_app(accounts, "salesforce")`
and got None because the match was an exact string compare. Result:
poll timed out, agent marked the connect as failed, customer saw
":x: No pude conectar a salesforce" despite the OAuth being green.

The fix: three-pass matching (exact, `<app>_*` prefix, `<app>` as a
slug token). Tests below pin all three passes + the negative case."""

from __future__ import annotations

from app.integrations.pipedream_provider import PipedreamProvider


def _acc(name_slug: str) -> dict:
    return {"id": f"acct_{name_slug}", "app": {"name_slug": name_slug}}


class TestExactMatch:
    def test_returns_account_when_slug_matches(self):
        p = PipedreamProvider()
        accounts = [_acc("github"), _acc("notion")]
        assert p.match_account_for_app(accounts, "notion") is accounts[1]


class TestPrefixMatch:
    def test_salesforce_to_salesforce_rest_api(self):
        # The real-world case that motivated this fix.
        p = PipedreamProvider()
        accounts = [_acc("salesforce_rest_api")]
        match = p.match_account_for_app(accounts, "salesforce")
        assert match is not None
        assert match["app"]["name_slug"] == "salesforce_rest_api"

    def test_google_to_google_sheets(self):
        p = PipedreamProvider()
        accounts = [_acc("google_sheets")]
        match = p.match_account_for_app(accounts, "google")
        assert match is not None

    def test_prefix_does_not_match_partial_word(self):
        # `goog` should NOT prefix-match `google_sheets` because the
        # next char isn't an underscore. Otherwise we'd false-positive
        # on any leading-substring search.
        p = PipedreamProvider()
        accounts = [_acc("google_sheets")]
        assert p.match_account_for_app(accounts, "goog") is None


class TestTokenMatch:
    def test_app_anywhere_in_tokens(self):
        # If the user's slug appears as a `_`-separated token anywhere
        # in the canonical slug, that's a match. Covers edge cases like
        # `microsoft_outlook` resolving from the user saying "outlook".
        p = PipedreamProvider()
        accounts = [_acc("microsoft_outlook")]
        match = p.match_account_for_app(accounts, "outlook")
        assert match is not None


class TestNoMatch:
    def test_returns_none_for_unrelated_app(self):
        p = PipedreamProvider()
        accounts = [_acc("notion"), _acc("github")]
        assert p.match_account_for_app(accounts, "salesforce") is None

    def test_empty_app_string(self):
        p = PipedreamProvider()
        accounts = [_acc("notion")]
        assert p.match_account_for_app(accounts, "") is None


class TestMatchPriority:
    def test_exact_wins_over_prefix(self):
        # If both `salesforce` and `salesforce_rest_api` are connected,
        # asking for "salesforce" must return the exact match, not the
        # compound one. (Hypothetical -- Pipedream rarely emits the
        # short slug -- but the contract should be deterministic.)
        p = PipedreamProvider()
        exact = _acc("salesforce")
        compound = _acc("salesforce_rest_api")
        accounts = [compound, exact]  # compound listed first
        match = p.match_account_for_app(accounts, "salesforce")
        assert match is exact
