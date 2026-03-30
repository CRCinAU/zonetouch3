"""Constants for the ZoneTouch 3 integration."""

DOMAIN = "zonetouch3"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_PORT = 7030
DEFAULT_POLL_INTERVAL = 30

# Protocol constants
HEADER = bytes([0x55, 0x55, 0x55, 0xAA])

# Zone set sub-type
SUBTYPE_ZONE_SET = bytes([0x20, 0x00])

# Zone states
ZONE_STATE_OFF = 0x02
ZONE_STATE_ON = 0x03
ZONE_STATE_PERCENT = 0x80
