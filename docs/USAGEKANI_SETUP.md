# Usagekani setup

Usagekani is a PC-on desk monitor for Codex and Claude subscription usage.
The existing STM32F405/ESP8266 cube displays two rotating pages: the approved
white terminal badge for Codex and the original orange pixel Clawd for Claude.
It uses no cloud service of its own.

## Windows companion

Requires Python 3.12+, Codex CLI signed in to the intended ChatGPT account,
Claude Code, and Git Bash (the standard Claude Code Windows shell).
No Python packages are required.

From the repository root:

```powershell
./scripts/install-companion.ps1
./scripts/start-companion.ps1
```

The installer chooses the PC's private LAN interface. To select one explicitly,
pass `-HostAddress <private-LAN-IP>` to the installer. Keep that address stable
with a DHCP reservation; re-run installation and rebuild/reflash if it changes.

The installer:

- Saves local settings and the device pairing token under
  `%LOCALAPPDATA%/Usagekani/`.
- Adds a Claude Code status-line command using shell-safe paths. Unrelated
  Claude settings remain intact; an unrelated existing status line is refused.
  A pre-install backup is kept locally.
- Creates `Usagekani.lnk` in the user's Startup folder, starting the companion
  without a console at Windows login. It retries while the LAN interface is
  unavailable.
- Writes host/port/pairing values to ignored `app_config_local.h`, and updates
  the parent configuration header's modification time so existing build
  dependency files discover the newly created optional header.

Allow inbound TCP port 8765 only for the Python companion on the private local
subnet. Do not expose this HTTP feed to the public internet or forward the port.
The ESP8266 AT firmware's HTTPS limitation is why the last hop uses LAN HTTP.
The endpoint requires a device token; provider credentials never reach the cube.

For foreground troubleshooting:

```powershell
python -m companion.usagekani
```

Run one companion instance. Logs are at
`%LOCALAPPDATA%/Usagekani/companion.log`; no provider tokens or private hosts are
logged. Stop the existing background companion before starting a foreground one.
Collection failures record only the provider, status and a bounded reason code
(for example `http_502`, `curl_6`, `timeout` or `invalid_response`). Identical
repeated failures stay quiet; changed causes and recovery are logged. Neither
upstream response bodies nor exception strings, keys or URLs are logged.

## Claude readings

### HTTPS usage exporter (preferred when configured)

Add these fields to the local `%LOCALAPPDATA%/Usagekani/config.json`, preserving
its existing device settings:

```json
{
  "claude_usage_url": "https://your-usage-service.example/usage",
  "claude_usage_key": "your-private-bearer-key"
}
```

Restart only the Usagekani companion after changing these settings. The URL and
key stay on the PC; they are not copied into firmware, logs, Git or the LAN
response. HTTPS certificate verification stays enabled. Windows' bundled curl
uses the OS certificate store; its default user configuration is disabled and
the bearer header is supplied over stdin, not command-line arguments. Redirects
are not followed.

The API is sampled every 30 seconds. It must return ISO 8601 `fetched_at` and
`five_hour`/`seven_day` objects with numeric `utilization` (percent used) and
ISO 8601 `resets_at`. Extra model windows/spending fields are ignored for the
two-bar display. Source `fetched_at` controls data age: serving the same cached
response again never makes it fresh. Missing windows remain unavailable.

HTTP 401 shows `Login required`; server/upstream/transport failures show provider
unavailability while retaining the last known values. Failed polls back off
from 30 seconds to at most five minutes; success restores the 30-second cadence.
When configured, this API is authoritative and does not silently switch to
older status-line readings on errors. No Claude Code session needs to be running.

### Status-line fallback when no API is configured

Restart an already-running Claude Code session after installing its status line.
The first normal provider response supplies quota fields when the signed-in plan
supports them. The adapter does not create prompts or call private usage APIs.
Until then, the cube shows **Waiting for Claude**.

The adapter saves usage fields only, not transcripts or credentials. It deduplicates
identical quota reports per session and serializes writers so an old session
redrawing cannot replace a newer account reading. Unchanged readings age
conservatively; opening/redrawing the status line is not proof of an upstream
quota refresh.

## Firmware

Set Wi-Fi locally in ignored `hal_firmware/Core/Inc/app_config_local.h`:

```c
#define VPC_WIFI_SSID "your-local-ssid"
#define VPC_WIFI_PASSWORD "your-local-password"
```

The installer adds `USAGEKANI_HTTP_HOST`, `USAGEKANI_HTTP_PORT` and
`USAGEKANI_DEVICE_TOKEN`. Never commit this header, compiled firmware containing
local values, flash backups, or companion state.

Use a separate CubeIDE workspace outside the repository directory when importing
the project. Retain the project name `ClawPetCube` and its HAL Release target.
After CubeIDE generates managed makefiles, prepend its bundled make and ARM GCC
to PATH as described in the inherited build instructions, then run:

```powershell
make -C Release all
```

If creating local configuration manually after a previous build, force its
discovery with `make -B -C Release all` once. Do not reuse a stale object compiled
with empty default settings.

Outputs remain `Release/ClawPetCube_HAL.elf` and `.hex`. Flash and verify via
ST-LINK. SD-card initialization is intentionally bypassed: Usagekani does not
use storage, and the inherited initialization blocked startup on the current
board in `SD_FindSCR`.

## Display and transport behavior

- Provider pages rotate every eight seconds. Card surfaces stay fixed while
  outgoing and incoming headings, readings, captions, reset text and footers
  push through clipped slots over 420 ms. Both pages remain visible until the
  slide completes; there is no fade or immediate hiding of the old page. Bars
  interpolate between providers. Each provider's mascot moves through a separate
  clipped header viewport with its page; the clock stays fixed. The indicator
  glides and changes provider accent color.
- Two bars show percentage used and reset countdowns. Window labels follow the
  provider's actual duration; unavailable values show `--`.
- Codex is sampled every 60 seconds; the cube polls every 30 seconds.
- Reports older than five minutes are marked stale. Failed collection preserves
  last-known values. A failed Claude cache cannot invalidate Codex.
- Expired windows are dimmed and show `Refreshing`; they do not trigger active
  quota warnings. The device never invents a reset to zero.
- Claude's Clawd blinks, floats and wanders within its header area; concerned/sleepy
  expressions communicate quota state alongside explicit text.
- Clawd uses **X.X** for provider/auth/network errors, including errors with
  cached low usage. Fresh low usage is happy; only an active window at 80%+
  causes the concerned face. Exhausted, waiting and stale data are sleepy.
  Near-limit text names the affected window (for example `Weekly near limit`).
- Codex keeps the supplied terminal glyph at healthy usage. Provider/auth/network
  errors replace only that glyph with **X.X**, retaining the white silhouette.
  Other quota/freshness states remain explicit in the readings and status text.
  The source image is `assets/usagekani/codex-mascot-source.jpg`; regenerate its
  92 x 94 alpha-only LVGL header with `./scripts/convert_codex_mascot.ps1` on
  Windows PowerShell 7+. The checked-in header needs no build-time image tools.
- Failed-provider readings are dimmed and marked `Last known` with their age,
  so a previous percentage is not presented as a fresh reading.
- Continuous USART2 buffering handles multiple ESP8266 `+IPD` bursts. A complete
  HTTP 200 response with valid Content-Length and bounded JSON is required.

The status endpoint is `GET /v1/status` with an Authorization bearer device
token. Its JSON has `version: 1`, UTC epoch `now`, and separate `codex`/`claude`
objects containing `status`, `observed_at`, and two optional `windows`. Each
window contains `used_pct`, `duration_mins`, `resets_at`. Null means unavailable.
For a known idle window, the exporter can report 0% with a null reset; the
companion encodes this as `used_pct: 0, resets_at: 0`. The cube shows **0% / No
active window**. This sentinel is only valid for zero usage; no reset time is
invented and the weekly window remains independently available.
Responses stay below 2 KB and are not chunked. Fixtures are under `tests/fixtures/`.

## Checks

```powershell
python -m unittest discover -s tests -v
make -C Release all
git diff --check
```

The native parser test also requires host GCC. It compiles the real MCU decoder,
checks truncated/fragmented HTTP, expiration and the receive ring without board
dependencies. The UART receive ring is single-producer (ISR), single-consumer
(network task).

Without a USB serial cable, `Usage_Debug` is an SWD-readable 16-word diagnostic
array. Words are: magic `0x554b414e`, UI seconds, page, network state, successful
polls, last fetch error, four quota values in tenths of a percent, validity mask,
free FreeRTOS heap bytes, UI/network stack high-water marks in words, last bridge
epoch, completed slide count. `Usage_AnimationDebug` adds completed slide count,
frame-callback count for the latest slide, its elapsed milliseconds and free
LVGL heap bytes, followed by progress/visibility/positions from an intermediate
animation callback. Visibility mask 3 means both providers were unhidden; the
positions describe intended widget geometry, not a capture of the LCD. The
callback count includes two initial positioning calls;
subtract those when interpreting timed animation updates. It contains no
credentials or account identifiers. Runtime
checks do not substitute for a photo or visual inspection of the actual LCD.
