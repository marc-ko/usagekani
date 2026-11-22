# Usagekani input-needed alerts

Approved direction: full-screen `I need you.` alert from the preview, with the
white terminal badge for Codex and original orange Clawd plus question bubble
for Claude. Approval copy is `Your approval?`. Respond on the PC, not the cube.
Preserve existing five Clawd faces and normal quota behavior.

Only explicit unanswered questions/permission requests create attention. Idle,
finished work, quota level and provider availability do not. Track individual
requests and sessions; one session's answer must not clear another's request.
Pause usage rotation during attention, rotating only pending providers if both
need input. Resume usage when requests are resolved. No auto-approval/answers.

## Integration

Local Python hooks emit minimal request state to an atomic, locked per-user
attention file. Store hashed session/request identifiers, kind, event time and
owning process identity; never store question/command text or credentials.
Check process identity (including start time) before displaying persisted
requests, so exited processes and reused PIDs cannot leave a stuck alert.
Keep bounded request/tombstone collections, validate state, and preserve
unresolved requests through companion restart while their owner remains alive.

Codex desktop records on this PC contain structured question calls and reply
IDs. Observe new records incrementally, including asynchronous question replies;
do not infer state from assistant prose or replay pre-install historical prompts.
Checkpoint offsets and avoid duplicate requests on replay. Codex hooks add local
permission/question lifecycle coverage for supported sessions. Ignore permission
events when local approvals are handled by automatic review.

Claude Code uses question PreToolUse/PostToolUse and permission/lifecycle hooks.
Clear matching tool requests on completion/failure and session requests on new
user prompts/end. Permission alerts clear when the corresponding approved tool
completes; hooks do not expose a general immediate approval-result event.
Notification idle_prompt is intentionally ignored. Existing hook settings and
status line must be preserved; back up before merging Usagekani-owned handlers.

The PC feed adds optional per-provider `attention` with kind (`question` or
`approval`), count (1–64), and observed_at (UTC epoch). Null/absent means none.
Quotas and activity remain independent. Use a local attention reader every
250 ms and cube polling every 2 seconds; external quota fetch rates stay at
30 seconds for Claude and 60 seconds for Codex. A disconnected/stale attention
feed must not be presented as a current actionable request.

## Verification

Test request lifecycle, concurrent writers, delayed/out-of-order callbacks,
PID reuse/death, malformed/oversized state, restart and Codex chunked/replayed
records, including async question replies. Extend native parser tests for
optional attention, invalid bounds and stale feed handling. Build/flash HAL,
check SWD and perform a real Codex question/answer exercise. Do not claim live
Claude UI verification unless an actual Claude session is exercised.
