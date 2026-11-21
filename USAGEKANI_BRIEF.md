# Usagekani product brief

Updated: 2026-11-21
Status: PC-companion approach and the two-page Clawd preview approved on
2026-11-21; first implementation is running on hardware. This brief preserves the original
research. See [current setup and behavior](docs/USAGEKANI_SETUP.md) for the
implementation contract and configuration instructions.

See [bring-up evidence and remaining checks](docs/USAGEKANI_BRINGUP.md) for
current validation; the findings below record the initial research stage.

## Purpose

Turn the existing ClawPetCube hardware into a small desk device for monitoring
Codex and Claude subscription usage at a glance. Keep the Clawd mascot already
built for this project.

The user supplied two visual references: a compact dark usage display with two
bars, and a larger dashboard with per-provider pages. Use them as visual
inspiration. Their example numbers and extra features are not requirements or
real account data.

## Confirmed requirements

- New project repository: `marc-ko/usagekani`, based on ClawPetCube.
- Monitor Codex and Claude usage.
- Preserve the existing orange pixel Clawd mascot and its character.
- Reuse the existing board as the starting point.
- Document the direction before deciding the implementation details.

## Existing foundation

Inherited from ClawPetCube:

- STM32F405RGTx, 240x240 ST7789-style SPI display.
- LVGL 8.1.1, FreeRTOS, STM32Cube HAL.
- ESP-12F / ESP8266 AT Wi-Fi over USART2.
- USART1 115200 serial debug via the board USB-C TTL bridge.
- RTC/time display, network state and an existing HTTP polling path.
- Clawd drawn from LVGL rectangles in
  `hal_firmware/Core/Src/app_tasks.c`: happy, blink, sleepy, concerned,
  and X-eye faces, with float and wander behavior.

Preserve the mascot drawing, proportions, colors and expressions when adapting
the surrounding dashboard. Reuse existing behavior where it fits; keep movement
out of the usage bars. Clawd remains the companion on both provider pages.

The inherited application currently monitors OpenClaw. Codex/Claude usage
collection and the Usagekani display are not implemented.

On 2026-11-21, STM32CubeProgrammer listed an attached ST-LINK probe. This confirms
probe detection only; the target MCU was not connected, reset, read or flashed
during this documentation task. It does not prove the current firmware state.
The local source checkout has three pre-existing modified firmware files; they
were not incorporated into Usagekani or altered by this task.

## Approaches to discuss

| Approach | Benefit | Tradeoff |
| --- | --- | --- |
| Computer companion plus Wi-Fi cube | Reuses ESP8266 polling; computer handles account integration and HTTPS | Computer or another collector host must stay on and be reachable |
| Computer companion plus USB serial | Simple wired prototype; avoids Wi-Fi setup | Needs a new framed input protocol and handling alongside debug output |
| Fully standalone device | No companion computer needed | Provider authentication and HTTPS are much harder on the current hardware |

Recommendation, not yet selected: computer companion plus Wi-Fi cube.

Proposed data path:

```text
Codex account rate-limit reader ----+
                                   +--> local companion --> small LAN status feed
Claude Code status-line adapter ----+                              |
                                                                  v
                                                     ESP8266 -> STM32 -> LVGL
```

Keep provider credentials and authentication on the computer. The cube should
receive only the display data it needs. The inherited ESP8266 path has known
HTTPS limitations, so do not assume direct provider API polling will work.
A LAN HTTP feed is a candidate for this hardware, not a public unauthenticated
internet service. Keep private hostnames, pairing values and credentials out
of Git and serial logs.

## Usage sources: findings, not account validation

### Codex

The installed Codex CLI reported version 0.171.0 and exposes `app-server`.
Official documentation describes `account/rateLimits/read` and
`account/rateLimits/updated`, including usage percentage, window duration,
reset timestamp and optional multiple limit buckets.

A companion could normalize these read-only values without running model turns.
Do not hardcode that primary always means five hours or secondary always means
weekly: use the returned window duration and bucket identity. Handle missing
windows and authentication errors.

No live account usage was queried. Availability for the user's logged-in account,
local protocol compatibility, bucket selection and refresh behavior remain to
be checked in a small read-only feasibility step.

Source: [Codex App Server](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

### Claude

Official Claude Code status-line documentation exposes
`rate_limits.five_hour.used_percentage`,
`rate_limits.five_hour.resets_at`, and corresponding `seven_day` fields.

A local status-line adapter could save only those usage values into the
companion's cache. Preserve any existing status-line output rather than replacing
the user's configuration without inspection.

These fields depend on subscription/provider support and appear only after the
session's first API response; individual windows can be absent. Re-running a
status-line script is not proof that the upstream quota has refreshed.
When Claude Code is idle or closed, show data age and stale/waiting state.
This adapter does not establish a fully independent, continuously refreshed
Claude web/Desktop usage reader.

Do not turn API-equivalent cost, token totals or context-window occupancy into
a subscription quota percentage. They measure different things.

Source: [Claude Code status line](https://code.claude.com/docs/en/statusline#rate-limit-usage).

## Proposed first screen

Keep the design readable on the actual 240x240 panel:

- Dark background with the existing Clawd, provider name, small clock/status.
- One provider page at a time, with two readable usage rows.
- Show percentage **used**, a progress bar and reset countdown for each
  available quota window.
- Candidate colors: teal for Codex and orange for Claude; Clawd keeps its
  existing orange identity on both pages.
- Small freshness indicator when data is old; readable waiting/disconnected
  states instead of fake 0%.
- Candidate automatic page rotation; manual switching only after the existing
  USART6 input events have been verified.

An overview with both providers is an alternative to discuss if seeing both
without waiting matters more than larger numbers. Do not copy the large
reference dashboard's weather, battery, crowdfunding charts or unrelated
providers into the first version.

Clawd could blink normally, look concerned near a limit, and rest when a quota
is exhausted. The text must still explain whether a problem is quota,
authentication, stale data or network connectivity; expression alone is not
enough. Activity labels such as "Thinking" need a real activity source and
are outside the quota-only first version.

## Data-handling rules for a future design

- Preserve provider, bucket/window identity, used percentage, reset timestamp,
  source observation time, and availability/error state.
- Use UTC epoch seconds on the wire; render local time/countdowns on the device.
- Keep provider source-observation time separate from HTTP fetch time: repeatedly
  serving cached values must not make stale usage appear fresh.
- Missing or unsupported is not zero. An expired window needs a new observation;
  do not invent a reset to 0% when its countdown reaches zero.
- Keep a last-known value visibly stale after collection/network failures.
- Keep each provider independent: one adapter failing must not hide the other.
- Account-level quota values from multiple sessions are snapshots, not numbers
  to add together.
- Bound payload size against the current 4096-byte ESP8266 receive buffer,
  including HTTP/AT framing overhead. A wire schema, parser limits and freshness
  policy still need to be designed.

## Suggested sequence after direction is agreed

1. Validate real read-only Codex quota and Claude status-line samples on the PC.
2. Agree the 240x240 layout using Clawd and real field availability.
3. Define a small versioned status contract and cache/freshness behavior.
4. Build the companion and adapt the inherited LVGL dashboard.
5. Compile the active HAL target, then flash and inspect the physical display.
6. Check stale data, missing windows, reset boundaries and one-provider failures.

Keep existing HAL build target names and paths until there is a reason to rename
them. The inherited build remains `make -C Release all`, producing
`Release/ClawPetCube_HAL.elf` and `Release/ClawPetCube_HAL.hex`.

## Open decisions

- Is a companion computer acceptable, or must the device work while it is off?
- Is Claude Code the intended Claude source, or is independent web/Desktop
  monitoring required?
- Prefer rotating provider pages, or both providers visible together?
- Which account/plan and quota buckets should be displayed?

## Relationship to inherited documentation

This brief records Usagekani's new product direction.
`CLAWPETCUBE_PLAN.md` and the bring-up journals remain the inherited OpenClaw
technical baseline and history; their OpenClaw product goals are not Usagekani
requirements. Existing firmware behavior and historical hardware validation
must not be mistaken for completed Usagekani functionality.

