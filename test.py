#!/usr/bin/env python3
"""Run a PA2 action sequence and print callback/state logs."""

from __future__ import annotations

import argparse
from datetime import datetime
from time import sleep

from src.dbxdriverack import ChannelLeft, CmdMuteAll, CmdUnmuteAll, pa2
import src.dbxdriverack.pa2.outputband as ob


DEFAULT_HOST = "127.0.0.1"


def ts() -> str:
    """Formatted local timestamp for log lines."""

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{ts()}] {message}")


def on_mute_change(band: str, channel: str, muted: bool) -> None:
    state = "MUTED" if muted else "UNMUTED"
    log(f"[CALLBACK] Mute changed: {band}-{channel} -> {state}")


def on_preset_change(preset_num: int, preset_name: str | None) -> None:
    if preset_name:
        log(f"[CALLBACK] Preset changed: {preset_num} ({preset_name})")
    else:
        log(f"[CALLBACK] Preset changed: {preset_num}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to a DriveRack PA2 and run a mute/preset test sequence."
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"PA2 host or IP address (default: {DEFAULT_HOST})",
    )
    parser.add_argument("--password", default="administrator", help="PA2 admin password")
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Connection/query timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable PA2 debug mode",
    )
    parser.add_argument(
        "--recall",
        type=int,
        default=2,
        help="Preset number to recall near the end of the test (default: 2)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    drack = pa2.PA2(debug=args.debug)
    drack.registerMuteChangeCallback(on_mute_change)
    drack.registerPresetChangeCallback(on_preset_change)

    try:
        log(f"Connecting to {args.host}...")
        drack.connect(host=args.host, password=args.password, timeout=args.timeout)
        log("Connected. Running test sequence.")

        # Give background threads time to process authentication.
        sleep(1)

        log("Muting Low-Left output")
        drack.muteOutput(ob.BandLow, ChannelLeft, True)
        sleep(2)

        log("Unmuting Low-Left output")
        drack.muteOutput(ob.BandLow, ChannelLeft, False)
        sleep(5)

        log("Muting all outputs")
        drack.bulkMute(CmdMuteAll)
        sleep(2)

        log("Unmuting all outputs")
        drack.bulkMute(CmdUnmuteAll)

        high_left_muted = drack.isMuted(ob.BandHigh, ChannelLeft)
        log(f"High-Left is muted: {high_left_muted}")

        log("Preset names currently known:")
        for preset_num, preset_name in drack.presetNames.items():
            log(f"  Preset {preset_num}: {preset_name}")

        sleep(5)
        log(f"Recalling preset {args.recall}...")
        drack.recallPreset(args.recall, block=False)
        sleep(2)

        log("Final safety unmute")
        drack.bulkMute(CmdUnmuteAll)
        log("Test sequence complete")
    except KeyboardInterrupt:
        log("Stopping (Ctrl+C).")
    except Exception as exc:
        log(f"Exception: {type(exc).__name__}: {exc}")
        raise
    finally:
        del drack
        log("Disconnected.")


if __name__ == "__main__":
    main()
