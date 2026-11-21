# Usagekani implementation plan

Goal: implement the approved 240x240 Codex/Claude usage preview, retaining Clawd.
Architecture: Python 3.12 standard-library companion on the PC, authenticated
small LAN JSON feed, existing ESP8266/STM32 HAL/LVGL display.
Spec: USAGEKANI_BRIEF.md and the UI approved in the conversation.

- [x] Collect and normalize Codex account/rateLimits/read and Claude status-line reports.
- [x] Serve authenticated GET /v1/status, preserve missing/error/stale semantics.
- [x] Parse bounded JSON on MCU and replace OpenClaw polling with usage polling.
- [x] Draw the approved Clawd header, two usage cards and rotating provider pages.
- [x] Install local settings/Windows launcher, build, flash and verify hardware.
- [ ] Review, document evidence, commit and push the implementation branch.

Defaults: Codex every 60s, cube every 30s, page rotation every 8s, stale after
300s. Data stays on the private LAN; provider credentials remain on the PC.
Missing data is never zero. Expired windows await new reports. Only UI task
calls LVGL. Keep the active ClawPetCube_HAL build artifact names.

Tests: provider normalization, invalid/expired/missing windows, independent
failures, status-line ingestion, authentication, actual HTTP framing, MCU
parser truncation/bounds, HAL build, ST-LINK write verification and serial boot.

Isolation: separate clone usagekani/ on codex/usage-monitor; original local
firmware edits are excluded. Use a separate CubeIDE workspace.
