#!/usr/bin/env python3
"""Keep a PA2 connection open and print callback events."""

from __future__ import annotations

import argparse
from datetime import datetime
from time import sleep

from src.dbxdriverack import pa2


DEFAULT_HOST = "127.0.0.1"


def ts() -> str:
    """Formatted local timestamp for log lines."""

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def on_mute_change(band: str, channel: str, muted: bool) -> None:
    state = "MUTED" if muted else "UNMUTED"
    print(f"[{ts()}] [CALLBACK] Mute changed: {band}-{channel} -> {state}")


def on_preset_change(preset_num: int, preset_name: str | None) -> None:
    if preset_name:
        print(f"[{ts()}] [CALLBACK] Preset changed: {preset_num} ({preset_name})")
    else:
        print(f"[{ts()}] [CALLBACK] Preset changed: {preset_num}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to a DriveRack PA2 and log callback events until interrupted."
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    drack = pa2.PA2(debug=args.debug)
    drack.registerMuteChangeCallback(on_mute_change)
    drack.registerPresetChangeCallback(on_preset_change)

    try:
        print(f"[{ts()}] Connecting to {args.host}...")
        drack.connect(host=args.host, password=args.password, timeout=args.timeout)
        drack.waitingForUser(True)
        print(f"[{ts()}] Connected. Callback-only mode active. Press Ctrl+C to stop.")

        while True:
            sleep(1)
    except KeyboardInterrupt:
        print(f"\n[{ts()}] Stopping (Ctrl+C).")
    except Exception as exc:
        print(f"[{ts()}] Exception: {type(exc).__name__}: {exc}")
        raise
    finally:
        drack.waitingForUser(False)
        del drack
        print(f"[{ts()}] Disconnected.")


if __name__ == "__main__":
    main()
