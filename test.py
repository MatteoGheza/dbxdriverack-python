#!/usr/bin/env python3
"""Test script to verify exception handling in background threads."""

from time import sleep

from src.dbxdriverack import pa2, ChannelLeft, CmdMuteAll, CmdUnmuteAll
import src.dbxdriverack.pa2.outputband as ob


def on_mute_change(band: str, channel: str, muted: bool) -> None:
    state = "MUTED" if muted else "UNMUTED"
    print(f"[CALLBACK] Mute changed: {band}-{channel} -> {state}")


def on_preset_change(preset_num: int, preset_name: str | None) -> None:
    if preset_name:
        print(f"[CALLBACK] Preset changed: {preset_num} ({preset_name})")
    else:
        print(f"[CALLBACK] Preset changed: {preset_num}")


drack = pa2.PA2(debug=False)
drack.registerMuteChangeCallback(on_mute_change)
drack.registerPresetChangeCallback(on_preset_change)

try:
    print("Attempting to connect to PA2 device...")
    drack.connect(
        host="192.168.167.51",
        password="administrator",
        timeout=5
    )
    
    # Give background threads time to process authentication
    sleep(1)
    drack.muteOutput(ob.BandLow, ChannelLeft, True)
    sleep(2)
    drack.muteOutput(ob.BandLow, ChannelLeft, False)
    sleep(5)
    drack.bulkMute(CmdMuteAll)
    sleep(2)
    drack.bulkMute(CmdUnmuteAll)

    # Print the current mute state of High-Left
    print("High-Left is muted:", drack.isMuted(ob.BandHigh, ChannelLeft))

    # Print names
    print("\nPreset Names:")
    for preset_num, preset_name in drack.presetNames.items():
        print(f"  Preset {preset_num}: {preset_name}")
    
    # Recall preset 1 after 5 seconds
    sleep(5)
    print("\nRecalling preset 1...")
    drack.recallPreset(1, block=False)
    sleep(2)
    drack.bulkMute(CmdUnmuteAll)
except Exception as e:
    print(f"✓ Exception caught: {type(e).__name__}: {e}")
finally:
    del drack
