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
