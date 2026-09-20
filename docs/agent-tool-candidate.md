# Agent-tool usability candidate

Branch: `codex/agent-tool-usability`, based on `5b693ba`. Publish to Raja's
`fork` remote, not the upstream `tradermonty` repository. This branch is an
unreleased checkpoint, **not merge-ready or deployed**.

Implementation commits: `78b3275` and `21be7c4`. Companion Brain implementation:
`1e97d9c` on its `codex/agent-tool-usability` branch. Full status, review
disposition, measured output sizes, remaining gates, and rollback guidance:
[Brain candidate status](https://github.com/Rj7/brain-mcp/blob/codex/agent-tool-usability/docs/reviews/2026-09-20-agent-tool-candidate.md).

Verification on 2026-09-20:

- Focused contract suites: **59 passed**, including actual MCP error flags and
  partial filing batches, provider-error fixtures, options identity, pagination,
  exact fundamentals fields, canonical discovery and metadata.
- Full offline suite: **173 failed, 164 passed**. Clean `5b693ba` baseline with
  the same setup: 37 failed, 260 passed. Migration assertions and faulty mocks
  explain many differences, but failures are not all individually triaged.
  Do not treat the full suite as green or ready for release.
- Actual two-server catalog gate: all 31 curated upstream tools register in
  Brain, for 41 total tools and 28 exposed output schemas.
- No fresh autonomous agent evaluation or real ChatGPT/Claude canary run yet.
- No production service, credentials, OAuth configuration or original checkout
  modifications were changed. Live provider entitlement is unverified (403 probe).

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
