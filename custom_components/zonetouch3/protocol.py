"""ZoneTouch 3 binary protocol implementation."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

from .const import (
    HEADER,
    SUBTYPE_ZONE_SET,
    ZONE_STATE_OFF,
    ZONE_STATE_ON,
    ZONE_STATE_PERCENT,
)

_LOGGER = logging.getLogger(__name__)

# Addresses
ADDRESS_MAIN_BOARD = 0x80
ADDRESS_CONSOLE = 0x90
ADDRESS_REMOTE = 0xB0

# Message types
MESSAGE_TYPE_EXPAND = 0x1F
MESSAGE_TYPE_SUBCOMMAND = 0xC0

# Sub-types under MESSAGE_TYPE_SUBCOMMAND (0xC0)
_SUBTYPE_GROUP_CONTROL = 0x20
_SUBTYPE_GROUP_STATUS  = 0x21
_SUBTYPE_TEMPERATURE   = 0x2B

# FullState data type
EX_DATA_FULL_STATE = 0xFFF0


@dataclass
class ZoneStatus:
    """Represents the status of a single zone."""

    zone_id: int
    name: str
    is_on: bool
    percent: int
    spill: bool = False
    turbo: bool = False


@dataclass
class DeviceInfo:
    """Device identification and version info."""

    device_id: str = ""
    owner: str = ""
    firmware_version: str = ""
    hardware_version: str = ""
    boot_version: str = ""
    console_version: str = ""
    console_id: str = ""


@dataclass
class DeviceState:
    """Represents the full device state."""

    zones: dict[int, ZoneStatus] = field(default_factory=dict)
    temperature: float | None = None
    device_info: DeviceInfo = field(default_factory=DeviceInfo)


def _crc16_modbus(data: bytes) -> int:
    """Calculate CRC16 Modbus checksum."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _insert_redundant_bytes(data: bytes) -> bytes:
    """Insert 0x00 after every three consecutive 0x55 bytes (Section 3h).

    This prevents the payload from containing the header pattern 0x555555AA.
    The inserted bytes are not included in the CRC or data length.
    """
    result = bytearray()
    consecutive_55 = 0
    for b in data:
        result.append(b)
        if b == 0x55:
            consecutive_55 += 1
            if consecutive_55 == 3:
                result.append(0x00)
                consecutive_55 = 0
        else:
            consecutive_55 = 0
    return bytes(result)


def _strip_redundant_bytes(data: bytes) -> bytes:
    """Strip 0x00 bytes inserted after every three consecutive 0x55 bytes (Section 3h).

    The redundant bytes are not part of the actual data, CRC, or length.
    """
    result = bytearray()
    consecutive_55 = 0
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x55:
            consecutive_55 += 1
            result.append(b)
            if consecutive_55 == 3:
                # Skip the next byte if it's the redundant 0x00
                if i + 1 < len(data) and data[i + 1] == 0x00:
                    i += 1
                consecutive_55 = 0
        else:
            consecutive_55 = 0
            result.append(b)
        i += 1
    return bytes(result)


def _build_packet(payload: bytes) -> bytes:
    """Build a full packet with header and CRC.

    CRC is computed over the payload bytes (before redundant byte insertion).
    The two CRC bytes are appended in high-byte-first order.
    Redundant bytes are then inserted into the payload+CRC portion.
    """
    crc = _crc16_modbus(payload)
    crc_bytes = bytes([crc >> 8, crc & 0xFF])
    return HEADER + _insert_redundant_bytes(payload + crc_bytes)


# ---------------------------------------------------------------------------
# Message ID counter
# ---------------------------------------------------------------------------
_msg_id_counter = 0


def _next_msg_id() -> int:
    """Return the next message ID (1-255, wrapping)."""
    global _msg_id_counter
    _msg_id_counter = (_msg_id_counter % 255) + 1
    return _msg_id_counter


# ---------------------------------------------------------------------------
# Packet builders
# ---------------------------------------------------------------------------

def build_fullstate_query() -> bytes:
    """Build a FullState request packet.

    This single command returns device info (serial, firmware, hardware versions),
    zone names, zone statuses, and temperature.
    """
    msg_id = _next_msg_id()
    payload = struct.pack(
        ">BBBBHH",
        ADDRESS_CONSOLE,      # Destination
        ADDRESS_REMOTE,       # Source
        msg_id,               # Message ID
        MESSAGE_TYPE_EXPAND,  # Type 0x1F
        0x0002,               # Length
        EX_DATA_FULL_STATE,   # 0xFFF0
    )
    return _build_packet(payload)


def build_zone_set(zone: int, percent: int) -> list[bytes]:
    """Build packets to set a zone's value. Returns a list of packets to send."""
    packets = []

    if percent == 0:
        states = [ZONE_STATE_OFF]
    else:
        states = [ZONE_STATE_ON, ZONE_STATE_PERCENT]

    for state in states:
        msg_id = _next_msg_id()
        payload = bytes([
            ADDRESS_MAIN_BOARD, ADDRESS_REMOTE,
            msg_id,
            MESSAGE_TYPE_SUBCOMMAND,
            0x00, 0x0C,      # Length
        ]) + SUBTYPE_ZONE_SET + bytes([
            0x00, 0x00,      # Common Data Length
            0x00, 0x04,      # Each Repeat Data Length (4 bytes)
            0x00, 0x01,      # Repeat Data Count (1 group)
            zone,            # Zone number
            state,           # State
            percent,         # Value
            0x00,
        ])
        packets.append(_build_packet(payload))

    return packets


# ---------------------------------------------------------------------------
# Streaming frame reassembly
# ---------------------------------------------------------------------------

def _wire_bytes_for(wire_data: bytes, needed_stripped: int) -> int:
    """Return how many wire bytes produce exactly `needed_stripped` stripped bytes."""
    stripped = n55 = 0
    i = 0
    while i < len(wire_data) and stripped < needed_stripped:
        b = wire_data[i]
        stripped += 1
        n55 = (n55 + 1) if b == 0x55 else 0
        if n55 == 3:
            i += 2  # this 0x55 + the redundant 0x00 that follows
            n55 = 0
        else:
            i += 1
    return i


class FrameReader:
    """Reassembles complete unescaped packets from a streaming TCP connection.

    Wire format per packet:
        4 bytes  header  (0x555555AA, never escaped)
        N bytes  escaped payload  (dest, src, msg_id, type, data_len, data, crc)

    The FrameReader syncs to the header, determines data_length from the first
    6 stripped bytes after the header, then reads the complete packet.
    """

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader
        self._buf = bytearray()

    async def _fill(self, timeout: float) -> bool:
        """Read more bytes into the buffer. Returns False on timeout, raises on EOF."""
        try:
            chunk = await asyncio.wait_for(self._reader.read(1024), timeout=timeout)
            if not chunk:
                raise ConnectionResetError("Connection closed by device")
            self._buf.extend(chunk)
            return True
        except asyncio.TimeoutError:
            return False

    async def read_packet(self, timeout: float = 60.0) -> bytes | None:
        """Read and return the next complete unescaped packet, or None on timeout/EOF."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout

        # Sync to the 0x555555AA header
        while True:
            idx = self._buf.find(b"\x55\x55\x55\xAA")
            if idx == 0:
                break
            if idx > 0:
                _LOGGER.debug("FrameReader: skipped %d pre-header byte(s)", idx)
                del self._buf[:idx]
                break
            # Not found yet; keep the last 3 bytes in case header straddles chunks
            if len(self._buf) > 3:
                del self._buf[:-3]
            t = end - loop.time()
            if t <= 0:
                return None
            await self._fill(min(t, 1.0))  # False = 1s sub-timeout; retry until t <= 0

        # Read until we have 6 stripped bytes after the header:
        # dest(1) src(1) msg_id(1) msg_type(1) data_length(2)
        while True:
            stripped_tail = _strip_redundant_bytes(bytes(self._buf[4:]))
            if len(stripped_tail) >= 6:
                break
            t = end - loop.time()
            if t <= 0:
                return None
            await self._fill(min(t, 1.0))

        data_length = struct.unpack(">H", stripped_tail[4:6])[0]

        # Read until we have the complete unescaped packet:
        # 6 fixed fields + data_length data bytes + 2 CRC bytes
        needed_tail = 6 + data_length + 2
        while True:
            stripped_tail = _strip_redundant_bytes(bytes(self._buf[4:]))
            if len(stripped_tail) >= needed_tail:
                break
            t = end - loop.time()
            if t <= 0:
                return None
            await self._fill(min(t, 1.0))

        # Consume the exact wire bytes and return the unescaped packet
        wire_consumed = _wire_bytes_for(bytes(self._buf[4:]), needed_tail)
        packet = HEADER + bytes(stripped_tail[:needed_tail])
        del self._buf[:4 + wire_consumed]
        return packet


# ---------------------------------------------------------------------------
# Packet parsers
# ---------------------------------------------------------------------------

def _decode_fixed_string(data: bytes) -> str:
    """Decode a fixed-length byte field, truncating at the first null byte."""
    null_idx = data.find(0x00)
    if null_idx != -1:
        data = data[:null_idx]
    return data.decode("utf-8", errors="replace").strip()


def _decode_length_prefixed_string(data: bytes, pos: int) -> tuple[str, int]:
    """Decode a length-prefixed string and return (decoded_string, new_position)."""
    length = data[pos]
    pos += 1
    value = data[pos : pos + length].decode("utf-8", errors="replace").strip()
    return value, pos + length


def _parse_system_info(data: bytes, state: DeviceState) -> int:
    """Parse system info from FullState data. Returns the byte offset after system info."""
    info = state.device_info

    # Offsets relative to start of data payload
    # 0-1:   data type (already consumed)
    # 2-9:   device_id (serial number), 8 bytes
    info.device_id = _decode_fixed_string(data[2:10])

    # 10-25: owner, 16 bytes
    info.owner = _decode_fixed_string(data[10:26])

    # 26: opt byte (not used in HA)
    # 27: service_due
    # 28-35: password (8 bytes, not used)
    # 36-45: installer (10 bytes, not used)
    # 46-57: telephone (12 bytes, not used)

    # 58-59: temperature, signed 16-bit
    temp_raw = struct.unpack(">H", data[58:60])[0]
    temp = (temp_raw - 500) / 10
    if temp <= 50.0:
        state.temperature = temp

    # 60+: length-prefixed version strings
    pos = 60
    info.hardware_version, pos = _decode_length_prefixed_string(data, pos)
    info.firmware_version, pos = _decode_length_prefixed_string(data, pos)
    info.boot_version, pos = _decode_length_prefixed_string(data, pos)
    info.console_version, pos = _decode_length_prefixed_string(data, pos)
    info.console_id, pos = _decode_length_prefixed_string(data, pos)

    _LOGGER.debug(
        "Device: id=%s owner=%s temp=%s°C hw=%s fw=%s boot=%s console=%s (%s)",
        info.device_id, info.owner, state.temperature,
        info.hardware_version, info.firmware_version, info.boot_version,
        info.console_version, info.console_id,
    )

    return pos


def _parse_group_info(data: bytes, pos: int, state: DeviceState) -> None:
    """Parse group (zone) info from FullState data starting at the given offset."""
    if pos + 4 > len(data):
        return

    group_count = data[pos]
    data_len = data[pos + 1]  # stride per group record
    name_len = data[pos + 2]  # length of each name string
    # pos + 3 is padding
    base = pos + 4

    for idx in range(group_count):
        record_offset = base + (data_len * idx)
        if record_offset + 2 > len(data):
            break

        index_byte = data[record_offset]
        zone_id = index_byte & 0x3F          # lower 6 bits
        power_bits = (index_byte >> 6) & 0x03  # upper 2 bits: 0b00=OFF, 0b01=ON, 0b11=TURBO

        position = data[record_offset + 1] & 0x7F  # lower 7 bits = open percentage

        # Flags at offset 6 within the record
        flags = 0
        if record_offset + 6 < len(data):
            flags = data[record_offset + 6]
        spill_active = bool(flags & 0x02)

        is_on = power_bits >= 1
        is_turbo = power_bits == 3

        if not is_on:
            position = 0

        # Zone name at offset 10 within the group data block
        name = f"Zone {zone_id}"
        name_offset = base + (data_len * idx) + 10
        if name_offset + name_len <= len(data):
            raw_name = data[name_offset : name_offset + name_len]
            decoded = _decode_fixed_string(raw_name)
            if decoded:
                name = decoded

        state.zones[zone_id] = ZoneStatus(
            zone_id=zone_id,
            name=name,
            is_on=is_on,
            percent=position,
            spill=spill_active,
            turbo=is_turbo,
        )
        _LOGGER.debug(
            "  Zone %d (%s): %s @ %d%%%s%s",
            zone_id, name,
            "TURBO" if is_turbo else ("ON" if is_on else "OFF"),
            position,
            " [SPILL]" if spill_active else "",
            f"  raw=[{data[record_offset]:02X} {data[record_offset+1]:02X}]",
        )


def _parse_fullstate_data(data: bytes) -> DeviceState:
    """Parse the data payload of a FullState (0x1F/0xFFF0) response."""
    state = DeviceState()
    try:
        pos = _parse_system_info(data, state)
        _parse_group_info(data, pos, state)
    except (struct.error, IndexError, UnicodeDecodeError) as err:
        _LOGGER.warning("Error parsing FullState response: %s", err)
    return state


def _parse_group_status_data(data: bytes) -> dict[int, ZoneStatus]:
    """Parse the data payload of a Group Status (0xC0/0x21) push packet.

    The device firmware sends each_repeat_data_length before repeat_data_count
    (opposite of the spec) — the same quirk as the 0x20 Group Control command.

    Wire layout (data[] = payload bytes after the 10-byte fixed header):
        data[0]    = 0x21 (sub-type)
        data[1]    = 0x00
        data[2-3]  = common data length  (0x00 0x00)
        data[4-5]  = each repeat data length  (e.g. 0x00 0x08 = 8 bytes/zone)
        data[6-7]  = repeat data count        (e.g. 0x00 0x05 = 5 zones)

    Per-zone record (8 bytes):
        byte[0]  bits 7-6 = power state (00=OFF, 01=ON, 11=TURBO)
                 bits 5-0 = zone number (0-15)
        byte[1]  bits 6-0 = current open percentage
        byte[6]            = flags: bit 1 = spill active
        (other bytes not used by this integration)

    Returns dict[zone_id -> ZoneStatus] with names set to "Zone N" — names must
    be merged from FullState data by the caller.
    """
    zones: dict[int, ZoneStatus] = {}
    try:
        # Note: length comes before count (device firmware quirk — same as 0x20 Group Control)
        repeat_len   = struct.unpack(">H", data[4:6])[0]
        repeat_count = struct.unpack(">H", data[6:8])[0]

        if repeat_len < 2:
            _LOGGER.warning(
                "0x21 Group Status: unusable repeat_len=%d, data=%s",
                repeat_len, data.hex(" "),
            )
            return zones

        flags_idx = 6  # byte[6] in an 8-byte record

        for i in range(repeat_count):
            off = 8 + i * repeat_len
            if off + repeat_len > len(data):
                break

            rec = data[off : off + repeat_len]
            index_byte = rec[0]
            zone_id    = index_byte & 0x3F
            power_bits = (index_byte >> 6) & 0x03
            percent    = rec[1] & 0x7F
            flags      = rec[flags_idx] if len(rec) > flags_idx else 0
            is_on      = power_bits >= 1
            is_turbo   = power_bits == 3
            if not is_on:
                percent = 0

            zones[zone_id] = ZoneStatus(
                zone_id=zone_id,
                name=f"Zone {zone_id}",
                is_on=is_on,
                percent=percent,
                spill=bool(flags & 0x02),
                turbo=is_turbo,
            )

    except (struct.error, IndexError) as err:
        _LOGGER.warning("Error parsing Group Status (0x21): %s", err)

    return zones


def _parse_temperature_data(data: bytes) -> float | None:
    """Parse the data payload of a Temperature notification (0xC0/0x2B).

    The device pushes this unsolicited when temperature changes.
    Identifier byte at data[8] must be 0x9F.
    Temperature value is a big-endian uint16 at data[10:12].
    Formula: (value - 500) / 10 → °C.
    """
    try:
        if len(data) < 12:
            return None
        if data[8] != 0x9F:
            _LOGGER.debug("0x2B Temperature: unexpected identifier 0x%02X", data[8])
            return None
        temp_raw = struct.unpack(">H", data[10:12])[0]
        temp = (temp_raw - 500) / 10
        if temp > 50.0:
            return None
        return temp
    except (struct.error, IndexError) as err:
        _LOGGER.warning("Error parsing Temperature (0x2B): %s", err)
        return None


def _dispatch_packet(packet: bytes) -> tuple[str, object]:
    """Parse a complete unescaped packet and return (kind, payload).

    Kinds and payload types:
        "fullstate"          → DeviceState
        "group_status"       → dict[int, ZoneStatus]
        "temperature"        → float
        "group_control_echo" → None
        "unknown"            → None
    """
    if len(packet) < 10:
        return "unknown", None

    msg_type = packet[7]
    data_length = struct.unpack(">H", packet[8:10])[0]
    data = packet[10 : 10 + data_length]

    _LOGGER.debug(
        "RX dest=0x%02X src=0x%02X id=0x%02X type=0x%02X data_len=%d  raw=%s",
        packet[4], packet[5], packet[6], msg_type, data_length, packet.hex(" "),
    )

    if msg_type == MESSAGE_TYPE_EXPAND:
        if len(data) < 2:
            return "unknown", None
        data_type = struct.unpack(">H", data[:2])[0]
        if data_type == EX_DATA_FULL_STATE:
            return "fullstate", _parse_fullstate_data(data)
        return "unknown", None

    if msg_type == MESSAGE_TYPE_SUBCOMMAND:
        if not data:
            return "unknown", None
        sub = data[0]
        if sub == _SUBTYPE_GROUP_STATUS:
            return "group_status", _parse_group_status_data(data)
        if sub == _SUBTYPE_TEMPERATURE:
            temp = _parse_temperature_data(data)
            if temp is not None:
                return "temperature", temp
            return "unknown", None
        if sub == _SUBTYPE_GROUP_CONTROL:
            return "group_control_echo", None
        return "unknown", None

    return "unknown", None


# ---------------------------------------------------------------------------
# TCP Client
# ---------------------------------------------------------------------------

class ZoneTouch3Client:
    """Async TCP client for communicating with a ZoneTouch 3 device.

    Maintains a single persistent TCP connection. A background reader task
    dispatches incoming packets; zone status and temperature updates are
    delivered immediately via registered callbacks.  The FullState query
    (used as a 30s keepalive by the coordinator) is sent over the same
    connection and its response is returned to the caller via asyncio.Future.
    """

    def __init__(self, host: str, port: int) -> None:
        """Initialize the client."""
        self._host = host
        self._port = port
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._framer: FrameReader | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending_fullstate: list[asyncio.Future] = []
        self._known_zone_ids: set[int] = set()
        self._zone_status_callbacks: list[Callable[[dict[int, ZoneStatus]], None]] = []
        self._temperature_callbacks: list[Callable[[float], None]] = []

    def register_zone_status_callback(
        self, callback: Callable[[dict[int, ZoneStatus]], None]
    ) -> None:
        """Register a callback invoked on unsolicited zone status updates (0x21)."""
        self._zone_status_callbacks.append(callback)

    def register_temperature_callback(
        self, callback: Callable[[float], None]
    ) -> None:
        """Register a callback invoked on unsolicited temperature updates (0x2B)."""
        self._temperature_callbacks.append(callback)

    async def async_connect(self) -> None:
        """Open a persistent TCP connection and start the background reader task."""
        if self._writer is not None:
            return  # already connected
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=10,
            )
        except (TimeoutError, OSError) as err:
            raise ConnectionError(
                f"Cannot connect to ZoneTouch 3 at {self._host}:{self._port}"
            ) from err
        self._reader = reader
        self._writer = writer
        self._framer = FrameReader(reader)
        self._reader_task = asyncio.get_running_loop().create_task(
            self._reader_loop(), name="zonetouch3_reader"
        )
        self._reader_task.add_done_callback(self._on_reader_task_done)
        _LOGGER.debug("Connected to ZoneTouch 3 at %s:%s", self._host, self._port)

    def _on_reader_task_done(self, task: asyncio.Task) -> None:
        """Log if the reader task exits with an unexpected exception."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.error(
                "ZT3 reader task raised an unexpected exception: %s",
                exc, exc_info=exc,
            )

    async def async_disconnect(self) -> None:
        """Close the persistent connection and stop the background reader task."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
        self._reader = None
        self._framer = None
        _LOGGER.debug("Disconnected from ZoneTouch 3")

    async def _reader_loop(self) -> None:
        """Background task: read and dispatch incoming packets from the device."""
        _LOGGER.debug("ZT3 reader loop started")
        try:
            while True:
                if self._framer is None:
                    _LOGGER.error("BUG: _reader_loop started without framer")
                    return
                # 90s timeout: coordinator sends FullState every 30s so this
                # should never expire in normal operation.
                packet = await self._framer.read_packet(timeout=90.0)
                if packet is None:
                    _LOGGER.warning(
                        "ZT3 reader: no data for 90s, assuming connection lost"
                    )
                    break

                kind, payload = _dispatch_packet(packet)

                if kind == "fullstate":
                    state: DeviceState = payload
                    self._known_zone_ids = set(state.zones.keys())
                    _LOGGER.debug(
                        "FullState: %d zone(s), temp=%s°C",
                        len(state.zones), state.temperature,
                    )
                    # Resolve the oldest pending async_query_state() call.
                    # Acquire the lock so this is atomic with respect to
                    # concurrent appends in async_query_state().
                    async with self._lock:
                        if self._pending_fullstate:
                            fut = self._pending_fullstate.pop(0)
                            if not fut.done():
                                fut.set_result(state)

                elif kind == "group_status":
                    updates: dict[int, ZoneStatus] = payload
                    # Only pass through zones that have been seen in a FullState
                    filtered = {
                        zid: z
                        for zid, z in updates.items()
                        if zid in self._known_zone_ids
                    }
                    if filtered:
                        _LOGGER.debug(
                            "0x21 Group Status: %d zone(s) updated", len(filtered)
                        )
                        for cb in self._zone_status_callbacks:
                            cb(filtered)

                elif kind == "temperature":
                    temp: float = payload
                    _LOGGER.debug("0x2B Temperature: %s°C", temp)
                    for cb in self._temperature_callbacks:
                        cb(temp)

                elif kind == "group_control_echo":
                    _LOGGER.debug("Group Control echo (our command acknowledged)")

                else:
                    _LOGGER.debug("Unknown packet, ignoring")

        except asyncio.CancelledError:
            _LOGGER.debug("ZT3 reader loop cancelled")
            raise
        except (OSError, asyncio.IncompleteReadError) as err:
            _LOGGER.warning("ZT3 connection lost: %s", err)
        finally:
            # Reject any callers waiting on async_query_state()
            for fut in self._pending_fullstate:
                if not fut.done():
                    fut.set_exception(ConnectionError("ZT3 connection lost"))
            self._pending_fullstate.clear()
            self._writer = None
            self._reader = None
            self._framer = None
            _LOGGER.debug("ZT3 reader loop exited")

    async def async_query_state(self) -> DeviceState:
        """Send a FullState query and return the response.

        Reconnects automatically if the connection is down.
        Waits up to 15s for the device response.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[DeviceState] = loop.create_future()

        # Hold the lock for the entire connect-then-write sequence so that
        # no other coroutine can observe a partially-connected state or race
        # to connect simultaneously.
        async with self._lock:
            if self._writer is None:
                await self.async_connect()
            self._pending_fullstate.append(fut)
            pkt = build_fullstate_query()
            _LOGGER.debug("TX FullState query: %s", pkt.hex(" "))
            self._writer.write(pkt)
            await self._writer.drain()

        try:
            return await asyncio.wait_for(fut, timeout=15)
        except TimeoutError:
            # Explicitly cancel the future so _reader_loop's `if not fut.done()`
            # guard skips it if the FullState response arrives after we give up.
            fut.cancel()
            async with self._lock:
                try:
                    self._pending_fullstate.remove(fut)
                except ValueError:
                    pass
            raise ConnectionError("Timeout waiting for FullState response from ZoneTouch 3")

    async def async_set_zone(self, zone: int, percent: int) -> None:
        """Set a zone's percentage value over the persistent connection."""
        packets = build_zone_set(zone, percent)
        _LOGGER.debug(
            "Setting zone %d to %d%% (%d packet(s))", zone, percent, len(packets)
        )

        # Hold the lock for the entire connect-then-write sequence (same
        # reasoning as async_query_state).
        async with self._lock:
            if self._writer is None:
                await self.async_connect()
            for i, packet in enumerate(packets):
                _LOGGER.debug("  TX[%d]: %s", i, packet.hex(" "))
                self._writer.write(packet)
                await self._writer.drain()
                if i < len(packets) - 1:
                    await asyncio.sleep(0.2)

    async def async_test_connection(self) -> bool:
        """Test if the device is reachable and responds to a FullState query."""
        try:
            await self.async_connect()
            state = await self.async_query_state()
            if len(state.zones) == 0:
                _LOGGER.error(
                    "Connected to %s:%s but no zones found in response",
                    self._host,
                    self._port,
                )
                return False
            return True
        except (ConnectionError, TimeoutError) as err:
            _LOGGER.error(
                "Failed to connect to ZoneTouch 3 at %s:%s: %s",
                self._host,
                self._port,
                err,
            )
            return False
        finally:
            await self.async_disconnect()
