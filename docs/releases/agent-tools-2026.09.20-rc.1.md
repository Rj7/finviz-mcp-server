# Agent tools — 2026-09-20 RC1

Testing prerelease, **not stable or merge-ready**. Published only to Raja's fork;
the candidate remains on `codex/agent-tool-usability`, without merging into main.

Includes bounded options and filing results, typed arguments and field discovery,
explicit provider errors, exact fundamentals field identity, fallback ticker
preservation, and news ticker validation. Companion Brain preserves these MCP
contracts and provides the shared authenticated endpoint.

- Wrap-up focused regression/contract verification: **74 passed**.
- Last full offline suite: **173 failed, 179 passed**. Not rerun for this
  documentation-only wrap-up; unresolved failures block stable release.
- ChatGPT web Work-mode OAuth successfully fetched NVDA's requested price field
  through Brain. This is a small canary, not comprehensive provider validation.
- Runtime remains `f9b5b4b` plus the previously documented, uncommitted validator
  overlay. This tag adds documentation, not a redeployment. Original user edits
  are preserved and excluded from these commits.

Both repositories use `agent-tools-2026.09.20-rc.1`. See the
[candidate/deployment record](../agent-tool-candidate.md) for checks, known gates,
the overlay, and coordinated rollback. No credentials or private data assets
are attached to this release.
