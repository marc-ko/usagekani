# Usagekani bring-up — 2026-11-21

## Implemented and checked

- Python companion running on the PC, configured for user-login startup.
- Live Codex account quota reads through the installed CLI, without model turns.
- Claude Code adapter installed and its command executed successfully under Git
  Bash. It remains waiting for the first real quota report from Claude Code.
- Authenticated private LAN feed polled by the ESP8266/STM32.
- Approved 240x240 two-page layout, original orange Clawd geometry/faces,
  bounded header animations, quota bars and reset countdowns.
- Fourteen automated tests passed, including the real native C parser, segmented
  HTTP and continuous receive buffer, missing/expired readings, cache recovery,
  repeated old Claude-session reports, installer preservation, and LAN-bind retry.
- Full forced HAL build completed with zero compiler warnings/errors. Build
  size: text 269612 bytes, initialized data 680, BSS 110960.
- Existing 1 MB flash backed up locally before programming. ST-LINK programming
  and verification succeeded. Backup and locally configured firmware are ignored.

## Hardware evidence

STM32CubeProgrammer identified a powered STM32F405-class target, device ID
0x413, with 1 MB flash. The first new firmware stopped inside the inherited
`HAL_SD_Init` / `SD_FindSCR`, confirmed by reading the core PC/LR. Usagekani
does not use the unvalidated card, so startup now leaves it uninitialized.

SWD readings after successful polling showed:

- UI heartbeat advancing and both page values 0/1 observed.
- Network state 2 (online), successful poll count increasing, fetch error 0.
- Codex 720/350 and later 820/360 in tenths of a percent, matching the PC feed
  at those observations. These are historical readings, not fixed demo values.
- Window validity mask 3: both Codex windows valid; Claude windows unavailable.
- Free FreeRTOS heap 7024 bytes, UI minimum remaining stack 1110 words,
  network minimum remaining stack 1556 words during successful reception.

The user then connected the USB-C serial cable. COM6 opened at **115200 8N1**.
During a controlled companion outage, SWD confirmed network state 3 (offline),
last fetch error 3, and retained prior quotas. Restarting the companion produced
this real UART sequence:

```text
ESP8266 TCP connect attempt 1 failed
ESP8266 link: disconnected
ESP8266 TCP connected after retry
Usagekani feed OK: codex=820/360 claude=0/0
Usagekani feed OK: codex=820/360 claude=0/0
```

The zero Claude log slots are invalid/unavailable fields (mask bits clear).
The LCD shows `--` and waiting text, not 0% usage. Subsequent SWD inspection
showed online state, poll count 6 and advancing UI heartbeat. The companion was
left running and COM6 was closed/released after capture.

No additional Windows firewall rule was required for the successful live LAN
path. A narrow-rule creation attempt was denied for lack of administrator rights;
firewall settings were not broadened or disabled.

## Review and remaining limits

An independent review identified seven integration issues. Fixes and regression
coverage address optional-header dependency discovery, Git Bash quoting, ordered
Claude observations, expired-window warnings, startup before LAN readiness,
invalid-cache recovery and dropped inter-burst UART bytes.

Live Claude account quota acquisition remains unverified until a supported
Claude Code session supplies real status-line fields. No fabricated quota was
inserted into the live device feed. Physical LCD appearance still needs direct
inspection/photo; SWD/UART evidence proves execution and transport, not visual
fidelity. A multi-hour soak remains future work.

The original ClawPetCube checkout's three pre-existing firmware edits were not
modified. Usagekani runs from its separate clone and implementation branch.

## Follow-up: live Claude HTTPS exporter — 2026-11-22

The user supplied a private Claude usage exporter and bearer key. Its public
health check returned HTTP 200; unauthenticated usage returned HTTP 401 and
authenticated usage returned HTTP 200. The real schema matches the documented
`five_hour.utilization`, `seven_day.utilization`, ISO `resets_at` and `fetched_at`
fields. The real response reported plan `pro`, 20% five-hour and 15% weekly;
extra optional per-model, credit and breakdown fields are intentionally ignored.

The PC companion now polls this configured HTTPS source every 30 seconds. The
original source timestamp controls freshness. Transport/API failures preserve
prior values, mark the provider error/auth state and back off to at most five
minutes. A successful older cached reply clears collection errors while keeping
newer known quota values and their original observation time.

Only local PC configuration contains the private endpoint and key. The bearer
header goes to Windows curl over stdin, with user curl configuration disabled,
TLS certificate verification enabled and no redirect following. Windows' trusted
HTTPS client was required because the installed MinGW Python CA store could not
validate this service's issuer. No TLS verification was bypassed.

The authenticated PC LAN feed reported Claude 20% / 15% with source age 13 seconds.
COM6 was no longer present, so verification used the still-connected ST-LINK:
`Usage_Debug` contained Claude quota words 200/150, validity mask 15 (all four
windows available), online state and increasing successful polls. Existing
firmware handled the new source without rebuilding or flashing. Clawd and the
approved two-page display are unchanged.

The expanded suite has 23 passing tests, including HTTPS command/key handling,
malformed-response recovery, cached-source age, auth recovery and all earlier
native C transport/parser checks. Independent review findings were addressed
with regressions. The companion was restarted on the reviewed code and left
running. Direct LCD appearance remains unphotographed; live Claude acquisition
is now verified.

## Follow-up: animated provider rotation — 2026-11-22

The user requested a modern slide when changing Codex/Claude pages. The firmware
now uses a single 360 ms eased animation for outgoing/incoming usage cards,
provider heading and status footer, with a gliding accent-colored page indicator.
Codex slides left into Claude and the return slides right. Separate clipped
viewports prevent the labels/cards from crossing Clawd or the clock. Both page
widget sets remain allocated and hold their own data throughout the transition;
no per-transition page allocation or mid-slide provider replacement is needed.

The existing firmware was backed up locally, the HAL build passed without
warnings/errors, and programming/verification succeeded. All 23 existing
regression tests passed. A focused independent review found no material
rendering/lifecycle issue.

SWD confirmed 12 completed slides over 102 seconds, both provider pages observed,
online polling, all four quota windows valid and no fetch errors. Free FreeRTOS
heap was 7032 bytes; LVGL free heap remained around 26 KB. The most recent slide
completed in approximately 350 ms. Its callback count includes initial setup
placements; this is execution/timing evidence, not a measured LCD frame rate or
proof of visual smoothness. The existing blocking SPI flush path is retained.
Direct viewing of the physical panel is still needed to judge the motion.

## Follow-up: reduce animation lag — 2026-11-22

The user reported that the full-width slide felt laggy. The refresh timer was
already set to 10 ms, but hardware counters showed only about five timed
animation callbacks in a roughly 430 ms transition. Rewriting large card areas
through the blocking display link limits intermediate motion updates.

A DMA experiment ran without transfer errors/timeouts but did not meaningfully
increase the number of intermediate updates. That experiment was discarded;
the shipped display driver and SPI clock remain unchanged. This avoids adding
DMA recovery complexity without a demonstrated motion benefit.

The replacement keeps card backgrounds and the status footer fixed, revealing
the incoming name, percentages and bars with only 8–12 pixels of movement and
a 240 ms fade. Both providers retain independent widgets and readings. Clawd,
clock, reset text, page cadence and network collectors are preserved.

The new build passed with no warnings/errors, all 23 regression tests passed,
and ST-LINK programming/verification succeeded. A first lighter version measured
four timed updates in roughly 235 ms. Removing the extra 10 ms UI-loop sleep
only during the reveal improved this to six timed updates over about 269 ms,
versus the original five over 430 ms: roughly twice the animation-update cadence
over a much shorter travel distance. The normal idle UI delay remains unchanged.
Counts exclude the two setup callbacks and are not a direct LCD frame-rate
measurement. Live polling remained online with all four windows valid. The
subjective feel still requires looking at the physical screen.

## Follow-up: correct idle Claude data, faces and slide lifecycle — 2026-11-22

The user observed `Provider unavailable` alongside an old 5% Claude reading,
a concerned face at low usage, and a transition that snapped instead of sliding.
Direct inspection found the exporter healthy (HTTP 200): five-hour utilization
was 0 with `resets_at: null`, while weekly usage was 16%. The collector rejected
that valid idle session and therefore retained its older 5% snapshot as an error.

The companion now accepts this specific idle response and sends a known 0%
window with reset sentinel 0. The MCU validates that sentinel only for zero
usage and displays `No active window`, rather than fabricating a countdown or
rejecting the whole provider. A missing window remains distinct from idle.

Face selection is now a shared, native-tested policy. Provider/auth/network
errors choose X.X before considering cached usage. Fresh low usage is happy;
only an active quota at 80% or above is concerned. Waiting/stale/exhausted states
are sleepy. Failed-provider values explicitly say `Last known` and include age.
Quota warnings name the affected window, including weekly limits.

The short fade previously hid the outgoing page at the beginning; the user was
correct that it was not a push slide. Shared static card backgrounds now contain
clipped per-provider value/caption/reset slots. Both providers' foregrounds,
headings and footers move in opposite positions through a 420 ms eased slide;
the outgoing content is hidden only at completion. Bars interpolate without
moving entire opaque card backgrounds, and there is no opacity fade.

All 25 tests passed, the HAL build had no warnings/errors, and firmware flash
and verification succeeded. Live PC feed and SWD both showed Claude 0% current
and 16% weekly with online polling and validity mask 15. On Claude page 1, the
actual `current_face` byte was 0 (happy). Error X.X, low-usage happiness, stale
sleepiness and high-weekly/expired-window behavior are covered by native tests.
The animation reported both providers unhidden (mask 3) during an intermediate
callback, with opposite value positions -95/+4. These diagnose intended widget
state and do not prove a particular frame was displayed on the physical LCD.
Physical visual confirmation was requested from the user.

## Follow-up: provider-specific Codex mascot — 2026-11-22

The user supplied a terminal badge and approved its white-on-dark screen preview.
Codex now displays that badge; Claude retains the existing orange Clawd geometry,
palette, float/blink behavior and face policy. Both mascot roots join the existing
420 ms page push through a clipped header viewport. The clock remains fixed.
Codex provider/auth/network errors show X.X in place of the terminal glyph.

The original supplied JPEG is preserved under `assets/usagekani/`. A deterministic
PowerShell converter applies the approved preview's inverse/high-contrast mask
and emits a 92 x 94, 8,648-byte alpha image in flash. The firmware requires no
runtime JPEG decoder, scaling or image-buffer allocation. Regenerating the header
produced an identical hash. The converted normal and X.X images were inspected.

All 25 existing regression tests passed and the HAL build completed without
warnings/errors. Focused independent review found no material issue in LVGL
image layout, clipping, page lifecycle or error-state behavior. The previous
firmware was backed up locally; ST-LINK programming and verification succeeded.
Initial SWD diagnostics showed page rotation, online polling, all four quota
windows valid and no fetch errors. LVGL free heap was 24,784 bytes and FreeRTOS
free heap was 7,024 bytes. These verify execution, not physical LCD appearance;
the on-device look and motion still require the user's observation.

## Follow-up: investigate intermittent Claude unavailability — 2026-11-22

The user reported Claude unavailable again. On inspection the API health and
authenticated usage requests both returned HTTP 200. Its 0%/null-reset current
window and 16% weekly window normalized successfully. The PC feed marked both
providers OK; SWD showed online polling, no fetch error, all four windows valid
and the same Claude readings. The outage had recovered before inspection.

The existing log contained only startup messages, so the earlier failure's cause
could not be established. The companion now records bounded failure codes for
HTTP status, curl failures, timeout, invalid JSON and invalid schema. Repeated
identical failures are suppressed and recovery is logged once. Upstream text,
exception strings, URLs and credentials are excluded. Poll/backoff cadence,
last-known values, freshness rules and device firmware are unchanged.

Three diagnostic regression tests were observed failing before implementation.
All 28 companion/native tests now pass, including secret exclusion and duplicate
failure/recovery handling. This improves evidence for the next interruption;
it does not establish or claim to fix the previous upstream failure.
