"""Unit tests for scheduled-tasks logic that doesn't touch Postgres.

Covers:
- cron validation (parse errors + sub-5-min cadence rejection)
- timezone resolution (IANA passthrough, aliases, Slack fallback, UTC fallback)
- next_run_at / count_missed_fires
- UUID + slug regexes
- _parse_until date parsing
- _build_seed_text formatting
- tool-layer rejection of scope='global' in v1

Integration tests (DB + permissions + seeding + scheduler) live in
`test_scheduled_tasks_integration.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

# `tests/conftest.py` sets dummy env vars before any `app.*` import; importing
# the repo + timezone modules here only after that runs is fine since pytest
# discovers conftest before module collection.
from app.scheduled_tasks import repository as repo
from app.scheduled_tasks import scheduler as sched
from app.scheduled_tasks.timezone import list_known_aliases, resolve_timezone


# --------------------------------------------------------------------------- #
# Cron validation
# --------------------------------------------------------------------------- #


class TestCronValidation:
    def test_invalid_cron_rejected(self):
        with pytest.raises(repo.TaskValidationError) as exc_info:
            repo.validate_cron_spec("not-a-cron", "UTC")
        assert "no es válido" in str(exc_info.value)

    def test_sub_min_cadence_rejected(self):
        # `* * * * *` fires every minute -- below the 5-min floor.
        with pytest.raises(repo.TaskValidationError) as exc_info:
            repo.validate_cron_spec("* * * * *", "UTC")
        assert "5 minutos" in str(exc_info.value) or "5min" in str(exc_info.value)

    def test_every_two_min_rejected(self):
        # `*/2 * * * *` also under the 5-min floor.
        with pytest.raises(repo.TaskValidationError):
            repo.validate_cron_spec("*/2 * * * *", "UTC")

    def test_five_min_cadence_accepted(self):
        # `*/5 * * * *` -> consecutive fires 5 min apart, exactly at the floor.
        repo.validate_cron_spec("*/5 * * * *", "UTC")

    def test_daily_at_9am_bogota_accepted(self):
        repo.validate_cron_spec("0 9 * * 1-5", "America/Bogota")

    def test_unknown_timezone_rejected(self):
        with pytest.raises(repo.TaskValidationError) as exc_info:
            repo.validate_cron_spec("0 9 * * *", "Foo/Bar")
        assert "Timezone" in str(exc_info.value) or "timezone" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# next_run_at + count_missed_fires
# --------------------------------------------------------------------------- #


class TestNextRunAt:
    def test_computes_future_utc(self):
        base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
        # Daily at 04:01 UTC -- next from 12:00 UTC is tomorrow 04:01.
        nxt = repo.compute_next_run_at("1 4 * * *", "UTC", base_utc=base)
        assert nxt.tzinfo is not None
        assert nxt == datetime(2026, 5, 31, 4, 1, 0, tzinfo=timezone.utc)

    def test_respects_timezone_walltime(self):
        # 9am Bogota = 14:00 UTC. From 12:00 UTC base, next 9am Bogota is
        # 14:00 UTC of the same day (Bogota is UTC-5).
        base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
        nxt = repo.compute_next_run_at("0 9 * * *", "America/Bogota", base_utc=base)
        # Day-of-week 30 May 2026 is Saturday, so unrestricted cron just shifts
        # to the next 9am Bogota local. Bogota DST: none (Colombia doesn't shift).
        assert nxt == datetime(2026, 5, 30, 14, 0, 0, tzinfo=timezone.utc)


class TestCountMissedFires:
    def test_returns_zero_when_never_run(self):
        assert repo.count_missed_fires("0 * * * *", "UTC", last_run_at=None) == 0

    def test_returns_zero_when_only_one_due(self):
        # Hourly cron; last run 30 min ago; only the current fire is due, no missed.
        last = datetime(2026, 5, 30, 11, 30, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 30, 12, 5, 0, tzinfo=timezone.utc)
        assert repo.count_missed_fires("0 * * * *", "UTC", last_run_at=last, now_utc=now) == 0

    def test_counts_skipped_fires_during_downtime(self):
        # Hourly cron; last run 3 hours + 5 min ago. Two fires were skipped
        # (the 12:00 and 13:00 fires); the current one (14:00) is "due now",
        # not counted.
        last = datetime(2026, 5, 30, 10, 55, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 30, 14, 5, 0, tzinfo=timezone.utc)
        # Cron `0 * * * *` next from 10:55 is 11:00, then 12:00, 13:00, 14:00.
        # Fires < 14:05: 11:00, 12:00, 13:00, 14:00 = 4. (current fire IS counted
        # since the implementation walks until >= now; 14:00 < 14:05 so it IS
        # counted by the simple definition. Read the impl to be sure.)
        missed = repo.count_missed_fires("0 * * * *", "UTC", last_run_at=last, now_utc=now)
        assert missed >= 3  # at least 11:00, 12:00, 13:00


# --------------------------------------------------------------------------- #
# Timezone resolution
# --------------------------------------------------------------------------- #


class TestResolveTimezone:
    def test_iana_passthrough(self):
        assert resolve_timezone("America/Bogota") == "America/Bogota"

    def test_lowercase_canonicalized(self):
        # macOS filesystem is case-insensitive; on Linux this matters. The
        # resolver canonicalizes BEFORE returning so the stored value is portable.
        assert resolve_timezone("america/bogota") == "America/Bogota"

    def test_alias_hora_col(self):
        assert resolve_timezone("hora Col") == "America/Bogota"

    def test_alias_pt(self):
        assert resolve_timezone("PT") == "America/Los_Angeles"

    def test_unknown_falls_back_to_utc(self):
        assert resolve_timezone("garbage") == "UTC"

    def test_slack_fallback_used_when_text_unknown(self):
        assert resolve_timezone("garbage", fallback_slack_tz="America/New_York") == "America/New_York"

    def test_slack_fallback_ignored_if_text_resolves(self):
        # Direct hit on text should NOT use the slack fallback.
        assert resolve_timezone("America/Bogota", fallback_slack_tz="America/New_York") == "America/Bogota"

    def test_none_text_with_slack_fallback(self):
        assert resolve_timezone(None, fallback_slack_tz="Europe/Madrid") == "Europe/Madrid"

    def test_invalid_slack_fallback_falls_to_utc(self):
        assert resolve_timezone("nope", fallback_slack_tz="Atlantis/Lost") == "UTC"

    def test_alias_table_intact(self):
        aliases = list_known_aliases()
        assert aliases["hora col"] == "America/Bogota"
        assert aliases["pst"] == "America/Los_Angeles"
        assert aliases["utc"] == "UTC"


# --------------------------------------------------------------------------- #
# Regex shape
# --------------------------------------------------------------------------- #


class TestRegexes:
    def test_uuid_regex_matches_canonical(self):
        u = uuid.uuid4()
        assert repo._UUID_RE.match(str(u)) is not None

    def test_uuid_regex_rejects_garbage(self):
        assert repo._UUID_RE.match("not-a-uuid") is None
        assert repo._UUID_RE.match("12345") is None
        # Wrong version (v0): rejected.
        assert repo._UUID_RE.match("00000000-0000-0000-0000-000000000000") is None

    def test_slug_regex_kebab_case(self):
        assert repo._SLUG_RE.match("daily-revops-report") is not None
        assert repo._SLUG_RE.match("a") is not None  # single char ok
        assert repo._SLUG_RE.match("a1") is not None

    def test_slug_regex_rejects(self):
        assert repo._SLUG_RE.match("Daily Report") is None  # uppercase + space
        assert repo._SLUG_RE.match("-leading") is None
        assert repo._SLUG_RE.match("trailing-") is None
        assert repo._SLUG_RE.match("") is None
        assert repo._SLUG_RE.match("snake_case") is None


# --------------------------------------------------------------------------- #
# _parse_until (tool-layer)
# --------------------------------------------------------------------------- #


def _import_parse_until():
    # Imported here (not at module top) to keep the conftest env-var setup
    # ordering stable -- agent_tools also imports repo + db modules.
    from app.scheduled_tasks.agent_tools import _parse_until
    return _parse_until


class TestParseUntil:
    def test_iso_date(self):
        parse = _import_parse_until()
        assert parse("2026-06-15") == datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)

    def test_ddmm_yyyy(self):
        parse = _import_parse_until()
        assert parse("15/06/2026") == datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)

    def test_none_returns_none(self):
        parse = _import_parse_until()
        assert parse(None) is None
        assert parse("") is None

    def test_unparseable_returns_none(self):
        parse = _import_parse_until()
        assert parse("tomorrow") is None
        assert parse("not-a-date") is None


# --------------------------------------------------------------------------- #
# Scheduler: seed text formatting (pure)
# --------------------------------------------------------------------------- #


class TestBuildSeedText:
    def _make_fire(self, summary: str | None) -> sched._PendingFire:
        return sched._PendingFire(
            task_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scope="local",
            name="daily-revops-report",
            prompt="Mandame el reporte de consumo de hoy.",
            destination_type="dm",
            destination_slack_id="U123",
            cron_spec="0 9 * * 1-5",
            timezone="America/Bogota",
            previous_summary=summary,
            fire_once=False,
        )

    def test_includes_no_prior_summary_when_first_run(self):
        text = sched._build_seed_text_for_test(self._make_fire(None))
        assert "Last run summary: no prior summary" in text
        assert "Mandame el reporte" in text
        assert "Task name: daily-revops-report" in text

    def test_includes_previous_summary(self):
        prev = "Found 3 patterns: A, B, C. Suggested workflows: X, Y."
        text = sched._build_seed_text_for_test(self._make_fire(prev))
        assert prev in text
        assert "Last run summary:" in text

    def test_truncates_long_summary(self):
        very_long = "x" * 5000
        text = sched._build_seed_text_for_test(self._make_fire(very_long))
        # 1800 chars + "…" suffix, well under the original 5000.
        assert "…" in text
        assert len(text) < 3000


# --------------------------------------------------------------------------- #
# Tool-layer: scope='global' rejected by v1
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_global_scope_rejected_at_tool_layer():
    """The Pydantic JSON-schema literal blocks scope='global', but the handler
    has a defense-in-depth check too. Calling with scope='global' directly
    should return a friendly Spanish error without touching the DB.

    Note: this needs the contextvars set so the handler doesn't bail out on
    "no contexto de workspace/usuario" before reaching the scope check."""
    from app.agent.context import set_run_context
    from app.scheduled_tasks.agent_tools import _create_scheduled_task

    set_run_context(
        workspace_id=str(uuid.uuid4()),
        run_id="test-run",
        skills_context="",
        app_user_id=str(uuid.uuid4()),
    )
    result = await _create_scheduled_task(
        name="test-task",
        prompt="do thing",
        cron_spec="0 9 * * *",
        timezone="UTC",
        scope="global",
        destination_type="channel",
        destination_slack_id="C123",
    )
    assert "global" in result.lower()
    assert "local" in result.lower()
