# Agent-tool usability candidate

Branch: `codex/agent-tool-usability`, based on `5b693ba`. Publish to Raja's
`fork` remote, not the upstream `tradermonty` repository. This branch was deployed
for user testing on 2026-09-20 with explicit authorization, but remains **not
merge-ready**. The known full-suite failures are not resolved.

Deployed source: `f9b5b4b` (including `78b3275` and `21be7c4`). Companion Brain:
`fc43712` on its `codex/agent-tool-usability` branch. Full status, review
disposition, measured output sizes, remaining gates, and rollback guidance:
[Brain candidate status](https://github.com/Rj7/brain-mcp/blob/codex/agent-tool-usability/docs/reviews/2026-09-20-agent-tool-candidate.md).
The [deployment and rollback record](https://github.com/Rj7/brain-mcp/blob/codex/agent-tool-usability/docs/DEPLOYMENT.md)
documents the ThinkPad-hosted services and existing Cloudflare Tunnel endpoint.

Verification on 2026-09-20:

- Focused contract suites: **74 passed**, including actual MCP error flags and
  partial filing batches, provider-error fixtures, options identity, pagination,
  exact fundamentals fields, canonical discovery and metadata.
- Exact deployed copy including preserved local validator tests: **77 passed**.
- Full offline suite: **173 failed, 179 passed**; failed test IDs matched the
  pre-fix candidate (173 failed, 164 passed). Clean `5b693ba` baseline with
  the same setup: 37 failed, 260 passed. Migration assertions and faulty mocks
  explain many differences, but failures are not all individually triaged.
  Do not treat the full suite as green or ready for release.
- Actual two-server catalog gate: all 31 curated upstream tools register in
  Brain, for 41 total tools and 28 exposed output schemas.
- No fresh autonomous agent evaluation or real ChatGPT/Claude canary run yet.
- Both services were restarted into versioned releases. Credentials, OAuth
  configuration, and original checkout modifications remained unchanged. A live
  NVDA price request succeeded; other provider endpoints are not comprehensively verified.

Follow-up review fixes preserve ticker identity in both fundamentals fallback
paths, prohibit fuzzy field substitution, and reject invalid news inputs as MCP
errors while accepting valid string/list ticker inputs. Final independent scoped
re-review reported no findings.

## Deployed runtime

The service runs on `aarthy-ThinkPad-T470`, not a cloud application server:

`/home/raja/.local/share/finviz-mcp/releases/20260920-agent-tools-f9b5b4b/serve-http.py`

A systemd user-service drop-in at
`/home/raja/.config/systemd/user/finviz-mcp.service.d/50-agent-tools-release.conf`
overrides only `ExecStart`. The original Python venv, working directory, credentials,
and tailnet-only binding on `100.97.90.23:8850` remain in use. Brain reaches this
service on the same laptop and exposes its curated tools through the public tunnel.

The deployed copy preserves pre-existing uncommitted `src/utils/validators.py` and
`tests/test_ticker_validators.py` from the original checkout. Those files were not
committed as part of this candidate; the deployment record gives the validator hash.
Rebuilding from the Git revision alone will not reproduce this overlay.

Rollback both coordinated releases with:

```sh
bash /home/raja/.local/share/brain-mcp/releases/20260920-agent-tools-fc43712/rollback.sh
```

This moves the release drop-ins aside and restarts the original runtimes without
resetting branches or overwriting user edits. Check for later deployments before
using this deployment-specific script.

## Reproduce focused checks

Run focused tests with:

```sh
python -m pytest tests/test_agent_tool_contracts.py tests/test_options_contract.py tests/test_raw_field_formatting.py tests/test_review_regressions.py
```

Use an isolated test environment with `pytest-asyncio` available. The verification
run used a temporary dependency directory instead of modifying the production
venv. Full offline comparisons disabled network requests and delay sleeps in
both baseline and candidate. Keep full-suite migration, remaining legacy
error/data contracts, live-provider validation and end-client checks as explicit
release gates. Do not merge or deploy based only on the focused suite.
