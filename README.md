# Polyaire ZoneTouch 3 for Home Assistant

A Home Assistant custom integration for the [Polyaire ZoneTouch 3](https://www.polyaire.com.au/zonetouch-3-touchpad) air conditioning zone controller. Communicates directly with the ZoneTouch 3 over your local network via TCP — no cloud or bridge required.

## Features

- **Zone damper control** — Each zone is exposed as a slider (0–100%) to control airflow
- **Temperature sensor** — Reports the controller's built-in temperature reading
- **Zone names** — Automatically imports zone names configured on the ZoneTouch 3 touchscreen
- **Device info** — Reports serial number, firmware version, and hardware version in the HA device registry
- **Auto-discovery of zones** — Zones are detected automatically from the device; no manual configuration needed
- **Local polling** — Periodically queries the device over TCP (default every 30 seconds)

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** > click the three-dot menu > **Custom repositories**
3. Enter `https://github.com/CRCinAU/zonetouch3` as the repository URL and select **Integration** as the category
4. Click **Add**
5. Search for "Polyaire ZoneTouch 3" in HACS and click **Download**
6. Restart Home Assistant

### Manual

1. Copy the `custom_components/zonetouch3` directory into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for "Polyaire ZoneTouch 3"
3. Enter the following:
   - **IP Address** — The IP address of your ZoneTouch 3 controller
   - **Port** — TCP port (default: `7030`)
   - **Poll interval** — How often to query the device in seconds (default: `30`, range: 5–300)

The integration will test the connection before saving. If it fails, verify the IP address and that port 7030 is reachable from your Home Assistant host.

## Entities

| Entity type | Description | Example entity ID |
|---|---|---|
| **Number** (slider) | One per zone, 0–100% in 5% steps. Setting to 0% turns the zone off. | `number.living_room` |
| **Sensor** | Controller temperature reading in °C | `sensor.temperature` |

## Device info

The device card in Home Assistant will display:

- **Device name** — Derived from the owner name configured on the controller
- **Serial number** — The controller's device ID
- **Firmware version** — Current firmware installed on the controller
- **Hardware version** — Hardware revision of the controller

## Network requirements

The ZoneTouch 3 must be on the same network (or a routable network) as your Home Assistant instance. The integration communicates over TCP port **7030** by default. Ensure this port is not blocked by any firewall rules.

It is recommended to assign a static IP address or DHCP reservation to your ZoneTouch 3 controller to prevent the IP from changing.

## Troubleshooting

- **"Unable to connect"** during setup — Verify the IP address and port. Try `telnet <ip> 7030` from the HA host to confirm connectivity.
- **Zones not appearing** — The integration discovers zones from the device response. If the device returns no zones, check that the ZoneTouch 3 is fully booted and has zones configured.
- **Stale values** — Reduce the poll interval in the integration config if you need faster updates.

## Protocol

This integration communicates with the ZoneTouch 3 using its native binary protocol over TCP. It sends a FullState request and parses the response to extract system info, zone names, zone statuses, and temperature data. Zone control commands use the standard group control subcommand format with CRC16-Modbus checksums.

## License

MIT
