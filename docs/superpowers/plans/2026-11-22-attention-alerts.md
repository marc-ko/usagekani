# Input-needed Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the approved provider-specific input-needed takeover screen for
explicit pending questions/approvals and clear it when the request is resolved.

**Architecture:** Python local event state feeds optional provider attention
fields through the existing authenticated LAN endpoint. A Codex desktop record
adapter and observer-only Codex/Claude hooks capture lifecycle events. HAL
validates the fields and switches the LVGL view without changing quota data.

**Tech Stack:** Python 3.12 standard library, Windows process APIs, STM32 HAL,
LVGL 8.1.1, native C tests.

**Spec:** `docs/superpowers/specs/2026-11-22-attention-alert-design.md`

## Global Constraints

- No provider secrets, command/question text or transcripts stored in new state.
- No hooks return decisions, answers, additional context or nonzero failure.
- Preserve existing local settings and five Clawd faces; use distinct mascots.
- Optional attention is backward compatible with version-1 feeds and <=2 KB.
- Quota polling remains Claude 30s / Codex 60s; cube polls 2s for alerts.

## Review Focus

- Out-of-order or replayed callbacks must not resurrect resolved questions.
- One response must not clear another session's or question's pending request.
- PID reuse, app exit, startup backlog and corrupt files cannot leave false alerts.
- Asynchronous Codex accepted output is not an answered question.
- Automatic approvals and completed/idle turns are not requests for human input.

### Task 1: Bounded local attention state

Files: `companion/usagekani/attention.py`, `process_identity.py`,
`tests/test_attention.py`, `model.py`, `__main__.py`.
Interface: `apply_event(path, provider, session, request, action, kind, owner,
event_ms)`; `read_attention(path, now, alive=...) -> provider attention map`.

- [ ] Write lifecycle/concurrency/restart/malformed/PID tests; observe failures.
- [ ] Implement atomic locked state, request tombstones and owner validation.
- [ ] Feed snapshots with independent optional attention and run full suite.
- [ ] Commit state/feed implementation and record verification in ledger.

### Task 2: Actual event sources and preserved hook installation

Files: `attention_hook.py`, `codex_attention.py`, `attention_install.py`,
wrapper `companion/attention_hook.py`, tests and setup docs.
Interface: observer hooks call Task 1; Codex watcher reads structured records
and checkpoints offsets after state writes, without generating model turns.

- [ ] Write hook and real-format Codex record tests, including partial lines,
  async accepted/reply, multiple questions and rewind/replay; observe failures.
- [ ] Implement sources, bounded disk reading, discovery and settings merge.
- [ ] Test installer idempotence and preservation of unrelated hooks/settings.
- [ ] Run full suite, commit, and update ledger.

### Task 3: HAL parser and approved takeover UI

Files: `usage_data.h/.c`, `usage_dashboard.c`, native tests.
Interface: per-provider `UsageAttention` has kind/count/observed_at; selection
policy validates feed recency and rotates pending providers only.

- [ ] Add optional-field, invalid/count/future/stale and selection tests; RED.
- [ ] Implement bounded parser and takeover view using existing mascot roots.
- [ ] Preserve normal transitions and show request kind/count/PC destination.
- [ ] Run full suite and HAL build, commit, update ledger.

### Task 4: Install, flash and end-to-end verification

- [ ] Merge reviewed local hook configuration with backups; restart companion.
- [ ] Back up and flash/verify the target; check live usage remains independent.
- [ ] Verify actual Codex question appears and its structured reply clears it.
- [ ] Exercise native hook entry points and report Claude live-test limits.
- [ ] Fresh whole-branch review; fix material findings with RED/GREEN tests.
- [ ] Update setup/bring-up documentation and existing draft PR, push commits.
