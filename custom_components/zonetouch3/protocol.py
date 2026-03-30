"""ZoneTouch 3 binary protocol implementation."""

from __future__ import annotations

import asyncio
import logging
import struct
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
        ADDRESS_CONSOLE,   # Destination
        ADDRESS_REMOTE,    # Source
        msg_id,            # Message ID
        MESSAGE_TYPE_EXPAND,  # Type 0x1F
        0x0002,            # Length
        EX_DATA_FULL_STATE,  # 0xFFF0
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
            0x00, 0x01,      # Repeat Data Count (1 group)
            0x00, 0x04,      # Each Repeat Data Length (4 bytes)
            zone,            # Zone number
            state,           # State
            percent,         # Value
            0x00,
        ])
        packets.append(_build_packet(payload))

    return packets


# ---------------------------------------------------------------------------
# FullState response parser
# ---------------------------------------------------------------------------

def parse_fullstate(raw: bytes) -> DeviceState | None:
    """Parse a FullState response into a DeviceState.

    Returns None if the response cannot be parsed.
    """
    if len(raw) < 12:
        return None

    # Strip redundant bytes (0x00 after three consecutive 0x55s) from the
    # portion after the 4-byte header, per protocol Section 3h.
    raw = raw[:4] + _strip_redundant_bytes(raw[4:])

    # Unpack header: 4-byte magic, dest, src, msg_id, msg_type, data_length
    header, addr_hi, addr_lo, msg_id, msg_type, data_length = struct.unpack(
        ">IBBBBH", raw[:10]
    )

    if header != 0x555555AA:
        _LOGGER.debug("Invalid header: 0x%08X", header)
        return None

    if msg_type != MESSAGE_TYPE_EXPAND:
        _LOGGER.debug("Not an expand response, type=0x%02X", msg_type)
        return None

    # Extract data payload (excluding 2-byte CRC at end)
    data = raw[10 : 10 + data_length]
    if len(data) < 2:
        return None

    data_type = struct.unpack(">H", data[:2])[0]
    if data_type != EX_DATA_FULL_STATE:
        _LOGGER.debug("Not a FullState response, data_type=0x%04X", data_type)
        return None

    state = DeviceState()

    try:
        pos = _parse_system_info(data, state)
        _parse_group_info(data, pos, state)
    except (struct.error, IndexError, UnicodeDecodeError) as err:
        _LOGGER.warning("Error parsing FullState response: %s", err)

    return state


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


# ---------------------------------------------------------------------------
# TCP Client
# ---------------------------------------------------------------------------

class ZoneTouch3Client:
    """Async TCP client for communicating with a ZoneTouch 3 device."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize the client."""
        self._host = host
        self._port = port
        self._lock = asyncio.Lock()

    async def _open_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a TCP connection to the device."""
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=10,
            )
        except (TimeoutError, OSError) as err:
            raise ConnectionError(
                f"Cannot connect to ZoneTouch 3 at {self._host}:{self._port}"
            ) from err

    async def _close(self, writer: asyncio.StreamWriter) -> None:
        """Close a connection."""
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def async_query_state(self) -> DeviceState:
        """Query the device for the full state via the FullState command."""
        async with self._lock:
            reader, writer = await self._open_connection()
            try:
                writer.write(build_fullstate_query())
                await writer.drain()

                # Read the response — FullState responses can be large,
                # read in a loop until we have enough data or timeout
                chunks = []
                try:
                    while True:
                        chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
                        if not chunk:
                            break
                        chunks.append(chunk)
                except TimeoutError:
                    pass

                raw = b"".join(chunks)
                if not raw:
                    raise ConnectionError("No response from ZoneTouch 3")

                state = parse_fullstate(raw)
                if state is None:
                    raise ConnectionError("Could not parse FullState response")

                return state

            finally:
                await self._close(writer)

    async def async_set_zone(self, zone: int, percent: int) -> None:
        """Set a zone's percentage value."""
        packets = build_zone_set(zone, percent)

        async with self._lock:
            _, writer = await self._open_connection()
            try:
                for i, packet in enumerate(packets):
                    writer.write(packet)
                    await writer.drain()
                    if i < len(packets) - 1:
                        await asyncio.sleep(0.2)
            finally:
                await self._close(writer)

    async def async_test_connection(self) -> bool:
        """Test if the device is reachable and responds."""
        try:
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
