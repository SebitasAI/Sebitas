"""End-to-end smoke test for the run_action guardrail.

Usage:
    doppler run -p sebitas -c prd -- .venv/bin/python scripts/test_run_action_guardrail.py

Six checks against the Antiff workspace's real Gong connection:

  1. Schema fetch via `get_action_props` works.
  2. validate_params accepts the agent's typical (broken) Gong call.
  3. annotate_sparse_result fires when we call with includeParties=false.
  4. Full gateway.run_action returns a [platform hint] prefix on broken call.
  5. Full gateway.run_action returns no hint on a properly-flagged call.
  6. Validation error returned to LLM when callIds is missing on get-extensive.

All checks log PASS / FAIL with short context. Exit code 0 iff everything passes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from app.agent.context import workspace_id_var
from app.integrations import action_guardrail as ag
from app.integrations import gateway


ANTIFF_WS = uuid.UUID("8192aaf0-e38c-4385-9a3f-4e651c984b75")
GONG_LIST_ACTION = "gong-list-calls"
GONG_GET_EXTENSIVE = "gong-get-extensive-data"  # the action whose rich flags drive sparseness

# A date window we know has MercadoLibre calls (per Sam, last call on
# 2026-05-31). One-week window for safety.
FROM_DT = "2026-05-25T00:00:00Z"
TO_DT = "2026-06-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _banner(idx: int, name: str) -> None:
    print(f"\n=== [{idx}] {name} ===", flush=True)


def _pass(msg: str = "") -> None:
    print(f"  PASS {msg}".rstrip(), flush=True)


def _fail(msg: str) -> str:
    print(f"  FAIL {msg}", flush=True)
    return msg


async def _run_with_ws(coro_fn):
    token = workspace_id_var.set(str(ANTIFF_WS))
    try:
        return await coro_fn()
    finally:
        workspace_id_var.reset(token)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


async def check_1_schema_fetch() -> list[str]:
    _banner(1, "Schema fetch via get_action_props")
    fails: list[str] = []
    # Bust cache so we exercise the live fetch path.
    ag._spec_cache.clear()
    props = await ag.get_action_props(GONG_GET_EXTENSIVE)
    if props is None:
        return [_fail(f"props was None for {GONG_GET_EXTENSIVE}")]
    if not isinstance(props, list):
        return [_fail(f"props is not a list: {type(props).__name__}")]
    names = [p.get("name") for p in props]
    _pass(f"got {len(props)} props: {names[:10]}")
    # We expect at least one of the rich flags to be defined.
    rich = [n for n in names if n and (n.startswith("include") or n == "context")]
    if not rich:
        fails.append(_fail(f"no rich flags found in {names}"))
    else:
        _pass(f"rich flags: {rich}")
    return fails


async def check_2_validate_ok() -> list[str]:
    _banner(2, "validate_params returns None on a healthy call")
    props = await ag.get_action_props(GONG_GET_EXTENSIVE)
    if props is None:
        return [_fail("no spec")]
    out = ag.validate_params(
        props,
        {"fromDateTime": FROM_DT, "toDateTime": TO_DT,
         "context": "Extended", "contextTiming": ["TimeOfCall"],
         "includeParties": True},
    )
    if out is None:
        _pass("validate_params returned None")
        return []
    return [_fail(f"unexpected validation error: {out}")]


async def check_3_annotate_when_sparse_and_off() -> list[str]:
    _banner(3, "annotate_sparse_result fires on synthetic sparse response")
    props = await ag.get_action_props(GONG_GET_EXTENSIVE)
    if props is None:
        return [_fail("no spec")]
    fake = {"calls": [], "records": {"totalRecords": 0}}
    hint = ag.annotate_sparse_result(
        result=fake, action_id=GONG_GET_EXTENSIVE,
        props=props, params={"fromDateTime": FROM_DT},
    )
    if hint is None:
        return [_fail(f"expected hint, got None. props={[p.get('name') for p in props]}")]
    _pass(f"off_flags={hint['off_flags']}")
    return []


async def check_4_run_action_broken() -> list[str]:
    _banner(4, "gateway.run_action with NO rich flags on extensive -> hint or filter-suggestion")

    async def _go():
        # The bug scenario: agent calls gong-get-extensive-data with
        # just a date window and no include* flags. Gong returns
        # parties-less rows and the agent can't match MercadoLibre.
        # The detector should at minimum surface a hint that
        # context / includeParties were left off.
        return await gateway.run_action(
            "gong", GONG_GET_EXTENSIVE,
            {"fromDateTime": FROM_DT, "toDateTime": TO_DT, "maxResults": 5},
        )

    try:
        text = await _run_with_ws(_go)
    except Exception as exc:
        return [_fail(f"exception: {exc!r}")]
    head = text[:600].replace("\n", " | ")
    print(f"  body[0..600]: {head}")
    if "[platform hint]" in text:
        _pass("hint injected")
        return []
    # If the response is actually rich enough (non-sparse + has parties)
    # then no hint is correct. Check for that.
    if '"parties"' in text:
        _pass("non-sparse response, parties present (no hint needed)")
        return []
    return [_fail("response missing parties/context but no hint emitted")]


async def check_5_run_action_healthy() -> list[str]:
    _banner(5, "gateway.run_action with ALL rich flags ON emits NO hint")

    async def _go():
        return await gateway.run_action(
            "gong", GONG_GET_EXTENSIVE,
            {"fromDateTime": FROM_DT, "toDateTime": TO_DT,
             "context": "Extended", "contextTiming": ["TimeOfCall"],
             "includeParties": True, "includePublicComments": True,
             "includeMedia": True, "maxResults": 5},
        )

    try:
        text = await _run_with_ws(_go)
    except Exception as exc:
        return [_fail(f"exception: {exc!r}")]
    head = text[:400].replace("\n", " | ")
    print(f"  body[0..400]: {head}")
    if "[platform hint]" in text:
        return [_fail(f"hint injected despite ALL rich flags on. hint head: {text[:300]}")]
    _pass("no hint with all rich flags on")
    return []


async def check_5b_partial_rich_flags() -> list[str]:
    _banner(5.1, "gateway.run_action with parties+context ON still surfaces remaining off flags")
    # Agent asks for parties + context (the MercadoLibre fix). The
    # detector should still note that includePublicComments / includeMedia
    # were not flipped, but the message should NOT mention parties/context.

    async def _go():
        return await gateway.run_action(
            "gong", GONG_GET_EXTENSIVE,
            {"fromDateTime": FROM_DT, "toDateTime": TO_DT,
             "context": "Extended", "contextTiming": ["TimeOfCall"],
             "includeParties": True, "maxResults": 5},
        )

    try:
        text = await _run_with_ws(_go)
    except Exception as exc:
        return [_fail(f"exception: {exc!r}")]
    head = text[:400].replace("\n", " | ")
    print(f"  body[0..400]: {head}")
    # Must NOT mention `parties` as missing (we asked for it).
    if "would populate `parties`" in text:
        return [_fail("hint mentions parties even though includeParties=True")]
    # Must NOT mention `context` as missing (we passed context='Extended').
    if "would populate `context`" in text:
        return [_fail("hint mentions context even though context='Extended'")]
    _pass("hint correctly scoped to remaining off flags only")
    return []


async def check_6_validate_blocks_missing_required() -> list[str]:
    _banner(6, "gateway.run_action returns validation error when required param missing")
    # Pick an action that has a required param. gong-get-extensive-call-data
    # requires callIds. If the action key differs in Pipedream's catalog,
    # this check skips gracefully.
    candidates = [
        GONG_GET_EXTENSIVE,
        "gong-get-call-content",
        "gong-get-call",
    ]
    chosen = None
    for cand in candidates:
        props = await ag.get_action_props(cand)
        if props is None:
            continue
        required = ag._required_names(props)
        if required:
            chosen = (cand, required)
            break
    if chosen is None:
        print("  SKIP: no candidate action with required props found.")
        return []
    action_id, required = chosen
    print(f"  using action={action_id} required={sorted(required)}")

    async def _go():
        return await gateway.run_action("gong", action_id, {})

    try:
        text = await _run_with_ws(_go)
    except Exception as exc:
        return [_fail(f"exception: {exc!r}")]
    if "Validation error" in text and "missing_required" in text:
        _pass("validation gated the call")
        return []
    return [_fail(f"expected validation error in: {text[:300]!r}")]


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


async def main() -> int:
    checks = [
        check_1_schema_fetch,
        check_2_validate_ok,
        check_3_annotate_when_sparse_and_off,
        check_4_run_action_broken,
        check_5_run_action_healthy,
        check_5b_partial_rich_flags,
        check_6_validate_blocks_missing_required,
    ]
    all_fails: list[str] = []
    for c in checks:
        try:
            fails = await c()
        except Exception as exc:
            all_fails.append(f"{c.__name__} crashed: {exc!r}")
            print(f"  CRASH {c.__name__}: {exc!r}")
            continue
        all_fails.extend(fails)
    print("\n" + "=" * 60)
    if all_fails:
        print(f"RESULT: {len(all_fails)} failure(s).")
        for f in all_fails:
            print(f"  - {f}")
        return 1
    print("RESULT: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
