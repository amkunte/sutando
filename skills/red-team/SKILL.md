---
name: red-team
description: "Adversarial pre-push code review. Before any upstream PR (or non-trivial local merge), red-team your OWN diff: enumerate the invariants the code claims, try to break each with a concrete scenario, trace data flows multiple hops, separate blocking bugs from nits. Built to catch the 2-hop / builder-blind-spot bugs that principle-based reviews (and the author) miss."
user-invocable: true
---

# Red Team

Adversarial self-review **gate**. Run before pushing ANY PR to `sonichi/sutando` (upstream), and before merging non-trivial changes to `local-main`.

**Why this exists (2026-06-02):** a real bug — trip-radar's calendar `uid` churned whenever a trip's start date shifted (`uid` derived from `trip_id`, which embedded `trip.start` → markers stopped matching → duplicate events) — survived a Gemini peer review and the author's own eyes, but an independent node (Lucy) caught it by **tracing the code and attacking its "reschedule-stable" claim**. The Gemini pass missed it (it reviewed the *concept* at architecture altitude) AND threw false positives (UTC-date, "spoofable gate") by reasoning from general principles instead of the actual code. This skill institutionalizes the lens that worked.

## Core stance

You are NOT confirming the code works. You are trying to **break it**. Assume a bug exists and hunt it. The builder defends; the red-teamer attacks. "Looks good" is not an output — "I tried to break claims A/B/C with these scenarios and couldn't" is.

## Procedure

1. **Scope the diff.** `git diff <base>...HEAD` — base = `main` for an upstream PR, else the PR/merge base. Read every changed hunk PLUS enough surrounding code to trace each value to its definition. A diff-only read misses the 2-hop bugs.

2. **Enumerate the CLAIMS.** Write down every invariant the change implicitly asserts — these are your attack targets:
   `idempotent` · `stable / deterministic` · `deduped` · `atomic` · `gated / authorized` · `validated` · `bounded` · `timezone-correct` · `matches X` · `can't collide` · `always set / non-empty` · `preserved across runs`.

3. **Attack each claim with a CONCRETE scenario.** For every claim, construct a specific input/sequence that violates it. Trace data dependencies **multiple hops** (the bug above was 2 hops: `uid → trip_id → start_date`). Probe:
   - **Provenance:** what's the source of every value in the claim? Follow it to its definition. Does anything upstream of it change when it shouldn't (or vice-versa)?
   - **State across runs:** does a re-scan / restart / migration / reschedule / rename break it? (Markers, dedup keys, ids derived from volatile fields.)
   - **Concurrency:** two writers? read-modify-write race on a shared file?
   - **Edge inputs:** empty / null / missing-field / duplicate / reordered / unicode / huge / format-variant (spaces vs dashes in an id).
   - **The "stable EXCEPT…":** every invariant has an except. Find it.

4. **Code-ground every finding — no exceptions.** Each finding MUST cite `file:line` + a concrete failing scenario ("if X then Y → bug"). If you cannot construct the failing case, it is NOT a finding — drop it. This discipline is what keeps out the principle-based false positives that discredit a review.

5. **Cross-check the known Sutando bug classes** (grep the diff for each):
   - workspace-path: repo-relative `tasks/`/`state/`/`results/` vs `$SUTANDO_WORKSPACE` ([[reference_workspace_mismatch_pattern]]).
   - localhost gate: a 0.0.0.0-bound endpoint that writes an owner-tier task / mutates state without the loopback check ([[reference_web_endpoint_localhost_gate]]).
   - py3.9: `X | None` annotations / `datetime.UTC` without `from __future__ import annotations` ([[reference_py39_pep604_bug_class]]).
   - channel-routing / default-channel assumptions; stale-marker or id-churn on migration; silent-failure (bare except / void return / hardcoded path).

6. **Independent lens (recommended for non-trivial diffs).** Also hand THIS prompt + the diff to a fresh reviewer with no investment in the code — a subagent (Agent tool, `Explore`/`general-purpose`) or `claude-codex` / `claude-gemini` — then synthesize. Reviewer *diversity* is what caught the bug; replicate it, don't rely on the author + one tool.

7. **Verdict** (always state the attacked-claim list so "clean" is earned):
   - 🔴 **BLOCK** — ≥1 blocking bug (correctness / security / data-loss with a concrete repro). Do not push; fix first.
   - 🟡 **SHIP-WITH-NITS** — only non-blocking items; list them; push if the owner accepts.
   - 🟢 **CLEAN** — attacked claims A/B/C…, found nothing real.

## When to run

- **MANDATORY** before any PR to `sonichi/sutando`.
- Recommended before merging non-trivial changes to `local-main`.
- Findings are for the owner: fix the blockers or surface them — never silently pass.
