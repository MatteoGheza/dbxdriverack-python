#!/usr/bin/env python3
"""
DBX DriveRack PA2 - HiQnet Telnet Server Emulator
Listens on port 19272 and emulates the device communication protocol
as observed in network capture logs.

Protocol conventions (from packet capture):
  <- client sends to device
  -> device sends to client

Supported commands:
  connect <user> "<password>"   - Authenticate
  get "<path>"                  - Get value for a path
  set "<path>" "<value>"        - Set value (replies setr)
  sub "<path>"                  - Subscribe (device sends subr on change)
  unsub "<path>"                - Unsubscribe (device sends unsubr)
  asyncget "<path>"             - Async get (device replies with get)
  ls "<path>"                   - List children of path

Both single-quoted and double-quoted paths are supported.
Multiple commands may arrive in one TCP segment, newline-separated.
"""

import asyncio
import argparse
import logging
import random
import re
from time import sleep

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("PA2")

PORT = 19272
RX_DEBUG = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DBX DriveRack PA2 HiQnet emulator"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help=f"TCP listen port (default: {PORT})",
    )
    parser.add_argument(
        "--debug-rx",
        action="store_true",
        help="Log every received command line (RX) at INFO level",
    )
    parser.add_argument(
        "--debug-protocol",
        action="store_true",
        help="Enable DEBUG-level protocol logs (RX/TX)",
    )
    return parser.parse_args()

# ---------------------------------------------------------------------------
# Canonical path constants  (double leading backslash, no quotes, no trailing slash)
# ---------------------------------------------------------------------------

# Paths the device always returns "error" for (unavailable hardware features)
ERROR_PATHS = {
    "\\Node\\SV\\HardwareVersion",
    "\\Node\\Config\\SV\\Command",
    "\\Node\\Config\\SV\\Result",
    "\\Node\\Config\\SV\\Progress",
    "\\Node\\Config\\SV\\Command:Status",
    "\\Preset\\LeftGEQ\\SV\\GraphicEQ",
    "\\Preset\\RightGEQ\\SV\\GraphicEQ",
    "\\Preset\\Mid Outputs PEQ\\SV\\ParametricEQ",
    "\\Preset\\Mid Outputs Limiter\\SV\\Limiter",
    "\\Preset\\Mid Outputs Delay\\SV\\Delay",
    "\\Preset\\Mid Outputs Limiter\\SV\\ThresholdMeter",
}

# Preset names stored in the device (captured from real traffic)
PRESET_NAMES = {
    1: "Preset A",
    2: "Preset B",
    3: "Preset C",
    4: "Preset D",
    5: "M.2WaywST.Sub",
    6: "M.2WaywM.Sub",
    7: "ST.3WaywST.Sub",
    8: "ST.3WaywM.Sub",
    9: "M.3WaywST.Sub",
    10: "M.3WaywM.Sub",
    11: "ST.Bi-ampMains",
    12: "M.Bi-ampMains",
    13: "ST.6FR",
    14: "ST.4FRwST.Sub",
    15: "ST.4FRwM.Sub",
    16: "JRX115w118S",
    17: "JRX125w118S",
    18: "EON305",
    19: "EON315",
    20: "EON510w518S",
    21: "EON515XTw518S",
    22: "K-10wK-Sub",
    23: "K-12wK-Sub",
    24: "PR12wPRSub",
    25: "PV115wPV118",
    26: "ST.FullRange",
    27: "M.FullRange",
    28: "ST.2WaywST.Sub",
    29: "ST.2WaywM.Sub",
    30: "M.2WaywST.Sub",
    31: "M.2WaywM.Sub",
    32: "ST.3WaywST.Sub",
    33: "ST.3WaywM.Sub",
    34: "M.3WaywST.Sub",
    35: "M.3WaywM.Sub",
    36: "ST.Bi-ampMains",
}

# Children returned by ls \\Preset
PRESET_CHILDREN = [
    "RTA", "SignalGenerator", "InputMeters", "StereoMixer",
    "StereoGEQ", "RoomEQ", "Afs", "SubharmonicSynth", "Compressor",
    "Back Line Delay", "Crossover", "High Outputs PEQ", "Low Outputs PEQ",
    "High Outputs Limiter", "Low Outputs Limiter", "High Outputs Delay",
    "Low Outputs Delay", "OutputGains", "OutputMeters", "SV", "AT",
]

# Attributes returned by ls \\Preset\\Crossover\\AT
CROSSOVER_AT_ATTRS = [
    ("Class_Name", "DriveRackCrossover"),
    ("Instance_Name", "Crossover"),
    ("Flags", "0"),
    ("NumSlots", "2"),
    ("NumBands", "2"),
    ("MonoSub", "0"),
]


# ---------------------------------------------------------------------------
# Device state
# ---------------------------------------------------------------------------

class DeviceState:
    """Shared mutable state for the emulated device."""

    def __init__(self):
        self.current_preset: int = 1
        self.preset_changed: str = "Changed"
        self.error_paths = {self.canonicalize(path) for path in ERROR_PATHS}

        # All known SV/AT values keyed by canonical path
        raw_store: dict[str, str] = {
            "\\Node\\AT\\Class_Name": "dbxDriveRackPA2",
            "\\Node\\AT\\Instance_Name": "My DriveRack",
            "\\Node\\AT\\Software_Version": "1.2.0.1",
            "\\Node\\Wizard\\SV\\WizardState": "Inactive",
            "\\Node\\Wizard\\SV\\LevelAssistOutput": "None",
            "\\Preset\\OutputGains\\SV\\LowLeftOutputMute": "Off",
            "\\Preset\\OutputGains\\SV\\LowRightOutputMute": "Off",
            "\\Preset\\OutputGains\\SV\\MidLeftOutputMute": "Off",
            "\\Preset\\OutputGains\\SV\\MidRightOutputMute": "Off",
            "\\Preset\\OutputGains\\SV\\HighLeftOutputMute": "Off",
            "\\Preset\\OutputGains\\SV\\HighRightOutputMute": "Off",
            "\\Preset\\Crossover\\AT\\NumBands": "2",
            "\\Preset\\Crossover\\AT\\MonoSub": "0",
            "\\Preset\\SignalGenerator\\SV\\Signal Generator": "Off",
            "\\Preset\\Afs\\SV\\AFS": "On",
            "\\Preset\\StereoGEQ\\SV\\GraphicEQ": "Off",
            "\\Preset\\RoomEQ\\SV\\ParametricEQ": "On",
            "\\Preset\\SubharmonicSynth\\SV\\SubharmonicSynth": "Off",
            "\\Preset\\Compressor\\SV\\Compressor": "Off",
            "\\Preset\\Back Line Delay\\SV\\Delay": "Off",
            "\\Preset\\High Outputs PEQ\\SV\\ParametricEQ": "On",
            "\\Preset\\High Outputs Limiter\\SV\\Limiter": "On",
            "\\Preset\\High Outputs Delay\\SV\\Delay": "Off",
            "\\Preset\\Low Outputs PEQ\\SV\\ParametricEQ": "Off",
            "\\Preset\\Low Outputs Limiter\\SV\\Limiter": "On",
            "\\Preset\\Low Outputs Delay\\SV\\Delay": "Off",
            # Meters
            "\\Preset\\High Outputs Limiter\\SV\\ThresholdMeter": "Under",
            "\\Preset\\Low Outputs Limiter\\SV\\ThresholdMeter": "Under",
            "\\Preset\\OutputMeters\\SV\\HighLeftOutput": "-109.9dB",
            "\\Preset\\OutputMeters\\SV\\HighRightOutput": "-110.4dB",
            "\\Preset\\OutputMeters\\SV\\MidLeftOutput": "-120.0dB",
            "\\Preset\\OutputMeters\\SV\\MidRightOutput": "-120.0dB",
            "\\Preset\\OutputMeters\\SV\\LowLeftOutput": "-120.0dB",
            "\\Preset\\OutputMeters\\SV\\LowRightOutput": "-120.0dB",
            "\\Preset\\InputMeters\\SV\\LeftInput": "-98.4dB",
            "\\Preset\\InputMeters\\SV\\RightInput": "-97.7dB",
            "\\Preset\\InputMeters\\SV\\LeftInputClip": "0",
            "\\Preset\\InputMeters\\SV\\RightInputClip": "0",
        }
        self.store: dict[str, str] = {
            self.canonicalize(path): value for path, value in raw_store.items()
        }

        # Subscriptions: canonical_path -> list of asyncio.Queue
        self.subscriptions: dict[str, list[asyncio.Queue]] = {}

    @staticmethod
    def canonicalize(path: str) -> str:
        """
        Convert any path string from the wire to canonical form:
          - strip surrounding quotes (" or ')
          - strip trailing backslash
                    - normalize separators and collapse duplicate delimiters
                Result always starts with a double leading backslash,
                e.g. \\Node\\AT\\Class_Name
        """
        p = path.strip().strip('"').strip("'")
        p = p.replace("/", "\\")
        p = p.rstrip("\\")

        # PA2 paths are expected in the form: \\Section\Subsection\Item
        # Preserve path segments, normalize separators, and enforce double-leading slash.
        segments = [segment for segment in re.split(r"\\+", p) if segment]
        if not segments:
            return "\\\\"
        return "\\\\" + "\\".join(segments)

    def is_error(self, path: str) -> bool:
        return self.canonicalize(path) in self.error_paths

    def get(self, path: str):
        canon = self.canonicalize(path)
        # Preset names  e.g.  \\Storage\\Presets\\SV\\Name_3
        m = re.match(r"^\\+Storage\\Presets\\SV\\Name_(\d+)$", canon)
        if m:
            return PRESET_NAMES.get(int(m.group(1)))
        if "Storage\\Presets\\SV\\CurrentPreset" in canon:
            return str(self.current_preset)
        if "Storage\\Presets\\SV\\Changed" in canon:
            return self.preset_changed
        return self.store.get(canon)

    def set(self, path: str, value: str) -> list[tuple[str, str]]:
        canon = self.canonicalize(path)
        changes: list[tuple[str, str]] = [(canon, value)]

        if "Storage\\Presets\\SV\\CurrentPreset" in canon:
            try:
                self.current_preset = int(value)
                changes = [(canon, str(self.current_preset))]
            except ValueError:
                pass
            return changes

        # Preset recall writes to ...\Storage\Presets\SV\Recall but changes
        # the live value at ...\Storage\Presets\SV\CurrentPreset.
        if "Storage\\Presets\\SV\\Recall" in canon:
            sleep(3)
            try:
                self.current_preset = int(value)
                current_path = "\\\\Storage\\Presets\\SV\\CurrentPreset"
                changes.append((current_path, str(self.current_preset)))
            except ValueError:
                pass
            return changes

        if "Storage\\Presets\\SV\\Changed" in canon:
            self.preset_changed = value
            return changes

        self.store[canon] = value
        return changes

    def subscribe(self, path: str, queue: asyncio.Queue):
        canon = self.canonicalize(path)
        self.subscriptions.setdefault(canon, []).append(queue)

    def unsubscribe(self, path: str, queue: asyncio.Queue):
        canon = self.canonicalize(path)
        lst = self.subscriptions.get(canon, [])
        if queue in lst:
            lst.remove(queue)

    async def notify(self, path: str, value: str, exclude=None):
        canon = self.canonicalize(path)
        for q in list(self.subscriptions.get(canon, [])):
            if q is not exclude:
                await q.put((canon, value))


# Singleton state shared across all connections
state = DeviceState()


# ---------------------------------------------------------------------------
# Background meter simulation
# ---------------------------------------------------------------------------

def _drift(base: float, spread: float = 1.5) -> str:
    return f"{base + random.uniform(-spread, spread):.1f}dB"


async def meter_loop():
    """Periodically update meter values and push notifications to subscribers."""
    meter_paths = {
        "\\Preset\\OutputMeters\\SV\\HighLeftOutput":  (-110.0, 1.5),
        "\\Preset\\OutputMeters\\SV\\HighRightOutput": (-110.5, 1.5),
        "\\Preset\\OutputMeters\\SV\\MidLeftOutput":   (-120.0, 0.3),
        "\\Preset\\OutputMeters\\SV\\MidRightOutput":  (-120.0, 0.3),
        "\\Preset\\OutputMeters\\SV\\LowLeftOutput":   (-120.0, 0.3),
        "\\Preset\\OutputMeters\\SV\\LowRightOutput":  (-120.0, 0.3),
        "\\Preset\\InputMeters\\SV\\LeftInput":        (-98.2, 0.5),
        "\\Preset\\InputMeters\\SV\\RightInput":       (-98.0, 0.5),
    }
    while True:
        await asyncio.sleep(0.5)
        for path, (base, spread) in meter_paths.items():
            val = _drift(base, spread)
            canon = state.canonicalize(path)
            state.store[canon] = val
            await state.notify(canon, val)


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

def parse_tokens(line: str) -> list[str]:
    """
    Split a HiQnet line into tokens.
    Respects both 'single' and "double" quoted strings.
    Unquoted tokens are split on whitespace.
    """
    tokens: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch in (" ", "\t"):
            i += 1
            continue
        if ch in ('"', "'"):
            try:
                j = line.index(ch, i + 1)
            except ValueError:
                j = len(line)
            tokens.append(line[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < len(line) and line[j] not in (" ", "\t"):
                j += 1
            tokens.append(line[i:j])
            i = j
    return tokens


# ---------------------------------------------------------------------------
# Client connection handler
# ---------------------------------------------------------------------------

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    log.info("New connection from %s", addr)

    sub_queue: asyncio.Queue = asyncio.Queue()

    async def tx(msg: str):
        data = msg + "\r\n"
        writer.write(data.encode())
        await writer.drain()
        log.debug("TX  %s", msg)

    # The device sends its banner immediately on connection
    await tx("HiQnet Console\n")

    # Background task: relay subscription notifications to this client
    async def relay_subs():
        while True:
            try:
                canon, value = await asyncio.wait_for(sub_queue.get(), timeout=0.1)
                await tx(f'subr "{canon}" "{value}"')
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    relay_task = asyncio.create_task(relay_subs())

    try:
        while True:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=120.0)
            except asyncio.TimeoutError:
                log.info("Client %s timed out", addr)
                break
            if not raw:
                break

            # A single readline may contain multiple \n-joined commands
            for raw_line in raw.decode(errors="replace").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if RX_DEBUG:
                    log.info("RX  %s", line)
                else:
                    log.debug("RX  %s", line)
                tokens = parse_tokens(line)
                if not tokens:
                    continue
                cmd = tokens[0].lower()

                # ── connect ──────────────────────────────────────────────
                if cmd == "connect":
                    user = tokens[1] if len(tokens) > 1 else "user"
                    await tx(f"connect logged in as {user}")

                # ── delay ────────────────────────────────────────────────
                elif cmd == "delay":
                    # Discovery clients may send `delay <ms>` to throttle probes.
                    # Real devices don't send a response; emulate that behavior.
                    if len(tokens) > 1:
                        try:
                            ms = max(0, int(tokens[1]))
                            await asyncio.sleep(ms / 1000)
                        except ValueError:
                            pass

                # ── get ──────────────────────────────────────────────────
                elif cmd == "get":
                    if len(tokens) < 2:
                        continue
                    raw_path = tokens[1]
                    canon = state.canonicalize(raw_path)
                    if state.is_error(raw_path):
                        await tx(f'error "{canon}\\"')
                    else:
                        val = state.get(raw_path)
                        if val is not None:
                            await tx(f'get "{canon}" "{val}"')
                        else:
                            await tx(f'error "{canon}\\"')

                # ── set ──────────────────────────────────────────────────
                elif cmd == "set":
                    if len(tokens) < 3:
                        continue
                    raw_path, value = tokens[1], tokens[2]
                    canon = state.canonicalize(raw_path)
                    for notify_path, notify_value in state.set(raw_path, value):
                        await state.notify(notify_path, notify_value)
                    await tx(f'setr "{canon}" "{value}"')

                # ── sub ──────────────────────────────────────────────────
                elif cmd == "sub":
                    if len(tokens) < 2:
                        continue
                    raw_path = tokens[1]
                    canon = state.canonicalize(raw_path)
                    state.subscribe(raw_path, sub_queue)
                    if state.is_error(raw_path):
                        await tx(f'error "{canon}\\"')
                    else:
                        val = state.get(raw_path)
                        if val is not None:
                            await tx(f'subr "{canon}" "{val}"')

                # ── unsub ────────────────────────────────────────────────
                elif cmd == "unsub":
                    if len(tokens) < 2:
                        continue
                    raw_path = tokens[1]
                    canon = state.canonicalize(raw_path)
                    state.unsubscribe(raw_path, sub_queue)
                    await tx(f'unsubr "{canon}"')

                # ── asyncget ─────────────────────────────────────────────
                elif cmd == "asyncget":
                    if len(tokens) < 2:
                        continue
                    raw_path = tokens[1]
                    canon = state.canonicalize(raw_path)
                    if state.is_error(raw_path):
                        await tx(f'error "{canon}\\"')
                    else:
                        val = state.get(raw_path)
                        if val is not None:
                            await tx(f'get "{canon}" "{val}"')
                        else:
                            await tx(f'error "{canon}\\"')

                # ── ls ───────────────────────────────────────────────────
                elif cmd == "ls":
                    if len(tokens) < 2:
                        continue
                    raw_path = tokens[1]
                    canon = state.canonicalize(raw_path)

                    if canon == "\\\\Preset":
                        await tx(f'ls "{canon}"')
                        await tx("\t.. : ")
                        for child in PRESET_CHILDREN:
                            await tx(f"\t{child} : ")
                        await tx("endls")

                    elif canon.endswith("\\Crossover\\AT"):
                        await tx(f'ls "{canon}"')
                        await tx("\t.. : ")
                        for k, v in CROSSOVER_AT_ATTRS:
                            await tx(f"\t{k} : {v}")
                        await tx("endls")

                    else:
                        # Generic empty listing
                        await tx(f'ls "{canon}"')
                        await tx("\t.. : ")
                        await tx("endls")

                else:
                    log.warning("Unknown command: %r", line)

    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        relay_task.cancel()
        # Remove this client from all subscriptions
        for lst in state.subscriptions.values():
            if sub_queue in lst:
                lst.remove(sub_queue)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        log.info("Client %s disconnected", addr)


# ---------------------------------------------------------------------------
# Multi-command TCP framing shim
# ---------------------------------------------------------------------------

async def client_wrapper(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """
    Buffer raw TCP data and re-feed it line by line so that handle_client
    always gets one logical command per readline(), even when the client
    bundles several commands in one TCP segment.
    """
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def feeder():
        buf = b""
        while True:
            try:
                chunk = await reader.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                idx = buf.index(b"\n")
                await queue.put(buf[: idx + 1])
                buf = buf[idx + 1:]
        await queue.put(b"")  # EOF sentinel

    feed_task = asyncio.create_task(feeder())

    class LineReader:
        async def readline(self) -> bytes:
            return await queue.get()

    await handle_client(LineReader(), writer)  # type: ignore[arg-type]
    feed_task.cancel()


# ---------------------------------------------------------------------------
# UDP discovery and query handler
# ---------------------------------------------------------------------------

class UDPProtocol(asyncio.DatagramProtocol):
    """Handle UDP requests used for device discovery and simple queries."""

    _ARGC_BY_CMD: dict[str, int] = {
        "connect": 2,
        "delay": 1,
        "get": 1,
        "set": 2,
        "sub": 1,
        "unsub": 1,
        "asyncget": 1,
        "ls": 1,
    }

    def __init__(self):
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def _tx(self, msg: str, addr: tuple[str, int]) -> None:
        if self._transport is None:
            return
        self._transport.sendto(msg.encode(), addr)
        log.debug("TX(UDP)  %s", msg)

    def _split_command_stream(self, tokens: list[str]) -> list[list[str]]:
        """Split a token stream into sequential protocol commands."""
        commands: list[list[str]] = []
        i = 0
        while i < len(tokens):
            cmd = tokens[i].lower()
            argc = self._ARGC_BY_CMD.get(cmd)
            if argc is None:
                log.debug("Unknown UDP token in command stream: %r", tokens[i])
                i += 1
                continue

            end = i + 1 + argc
            if end > len(tokens):
                log.debug("Incomplete UDP command in stream: %r", tokens[i:])
                break

            commands.append(tokens[i:end])
            i = end
        return commands

    def _handle_udp_command(self, tokens: list[str], addr: tuple[str, int]) -> str | None:
        cmd = tokens[0].lower()

        if cmd == "delay":
            # Discovery clients may send delay to spread responses.
            # Real devices do not answer this command over UDP.
            return None

        if cmd == "get":
            raw_path = tokens[1]
            canon = state.canonicalize(raw_path)
            if state.is_error(raw_path):
                return f'error "{canon}\\"'

            val = state.get(raw_path)
            if val is not None:
                return f'get "{canon}" "{val}"'
            return f'error "{canon}\\"'

        if cmd == "asyncget":
            raw_path = tokens[1]
            canon = state.canonicalize(raw_path)
            if state.is_error(raw_path):
                return f'error "{canon}\\"'

            val = state.get(raw_path)
            if val is not None:
                return f'get "{canon}" "{val}"'
            return f'error "{canon}\\"'

        if cmd == "set":
            raw_path, value = tokens[1], tokens[2]
            canon = state.canonicalize(raw_path)
            state.set(raw_path, value)
            return f'setr "{canon}" "{value}"'

        log.debug("Unknown UDP command from %s: %r", addr, " ".join(tokens))
        return None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            payload = data.decode(errors="replace")
        except Exception:
            return

        response_parts: list[str] = []
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if RX_DEBUG:
                log.info("RX(UDP)  %s", line)
            else:
                log.debug("RX(UDP)  %s", line)

            tokens = parse_tokens(line)
            if not tokens:
                continue

            for command_tokens in self._split_command_stream(tokens):
                response = self._handle_udp_command(command_tokens, addr)
                if response is not None:
                    response_parts.append(response)

        if response_parts:
            # Reply with one UDP packet per incoming UDP packet.
            self._tx("\n".join(response_parts) + "\n", addr)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main(port: int = PORT):
    loop = asyncio.get_running_loop()

    tcp_server = await asyncio.start_server(client_wrapper, "0.0.0.0", port)

    udp_transport, _ = await loop.create_datagram_endpoint(
        UDPProtocol,
        local_addr=("0.0.0.0", port),
    )

    log.info("DBX DriveRack PA2 emulator ready on port %d", port)
    log.info("  Protocols       : TCP and UDP")
    log.info("  Class_Name      : dbxDriveRackPA2")
    log.info("  Instance_Name   : My DriveRack")
    log.info("  Software_Version: 1.2.0.1")
    log.info("  Preset slots    : 36  (current: %d - %s)",
             state.current_preset, PRESET_NAMES.get(state.current_preset, "?"))

    meter_task = asyncio.create_task(meter_loop())

    try:
        async with tcp_server:
            await tcp_server.serve_forever()
    finally:
        meter_task.cancel()
        udp_transport.close()


if __name__ == "__main__":
    args = parse_args()
    RX_DEBUG = args.debug_rx
    if args.debug_protocol:
        log.setLevel(logging.DEBUG)
    asyncio.run(main(port=args.port))
