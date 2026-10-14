# Regression repair and truthful errors — 2026-09-20

Status: implemented and tested in the candidate checkout; **not deployed**.
This follow-up does not change service configuration, release directories, OAuth,
or the original checkout's uncommitted ticker-validator changes.

## Results

- Full offline suite: **837 passed in 15.83 seconds**, zero skips or warnings;
  runtime warnings were promoted to errors.
- Truthful-failure suite: 33 passed, including real in-memory MCP client/server
  calls that check `isError`, not just direct Python exceptions.
- Independent review found two additional malformed-row false successes. Five
  regressions failed before their fixes and passed afterward. Scoped re-review
  reported no remaining findings.
- Brain's companion checks: 59 tests passed, typecheck/build passed. The real
  two-server catalog gate still exposes 41 tools (31 bridged), 28 output schemas,
  hash `90b41742bcaeaabe5b9a87974069f3c408dc593e8d21e78a9a78d69ee000c4d5`.

## What was repaired

SEC summaries and EDGAR filings/facts/concepts now propagate provider failures as
MCP errors instead of ordinary successful text, empty lists, or error dictionaries.
Missing CIKs are distinguishable from upstream failures. Malformed EDGAR envelopes,
required filing columns/values/dates, and malformed SEC rows are rejected before
filtering and limiting. Legitimate empty responses remain successful.

Custom-screener invalid filters, signal, ordering and result counts are tool errors.
Price validation rejects invalid strings, booleans and nonfinite numbers while
preserving valid unset, numeric and supported filter-token inputs. Earnings
screener rejects a non-list provider result instead of treating it as empty data.

## Why the old suite was red (and sometimes falsely green)

The initial run under the new fail-closed network guard was 175 failed / 177 passed.
The earlier harness recorded 173 failed / 179 passed: two EDGAR tests swallowed
real network failures. Neither baseline was a trustworthy release gate.

| Failure class | Repair |
|---|---|
| Legacy TextContent-list assertions after structured migration | Assert actual structured values, provenance, schemas and paging |
| Dict/list mocks inconsistent with provider return contracts | Use real model objects and realistic CSV/JSON fixtures |
| Unsupported preset kwargs silently ignored | Exercise supported custom-screener transport; assert forwarded arguments |
| Async discovery not awaited / incomplete fake tool inventories | Await SDK discovery and check the real registered catalog |
| Test functions returning False or merely printing success | Replace with actual assertions and deterministic handlers |
| Unmocked network or swallowed exceptions | Autouse requests guard fails the test, outside ordinary Exception catches |
| Provider singleton mock attributes leaking across tests | Restore singleton attributes after monkeypatch teardown |
| Genuine validation and false-success defects | Add failing regressions, fix implementation, rerun focused and full suites |

The increase to 837 is largely conversion of loops into individually reported
parameter cases; it is not 485 new independent capabilities. No blanket skips,
xfails or error-tolerance switches were added to obtain the green result.
Raw-filter tests establish literal forwarding, **not live Finviz interpretation**.

## Reproduce

In an isolated test environment with project dependencies and pytest-asyncio:

```sh
python -m pytest -q --tb=short -W error::RuntimeWarning
python -m pytest tests/test_truthful_failures.py -q
```

`tests/conftest.py` blocks `requests.Session.request` by default. A future test
must both carry the `live` marker and be explicitly selected with `--live` to
escape that guard. No current suite test needs that exception. The guard is not
an OS network sandbox; other network libraries would need equivalent blocking.

This run used the existing Python interpreter with pytest-asyncio supplied via
`PYTHONPATH=/tmp/finviz-agent-test-deps`; the production environment was not changed.
Full-run log: `/tmp/finviz-followup-final.log`; JUnit: `/tmp/finviz-followup-final.xml`.
Those temporary logs are local evidence, not committed artifacts.

## Limits

Passing fixtures do not verify live subscriptions, provider conventions, freshness,
every unchanged error path, or autonomous agent success. The deployed older copy
has not acquired these fixes. The original validator overlay was not merged into
this candidate and must remain explicitly accounted for when deploying later.
