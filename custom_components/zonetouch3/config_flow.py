"""Config flow for ZoneTouch 3 integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_HOST,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)
from .protocol import ZoneTouch3Client

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(
            int, vol.Range(min=5, max=300)
        ),
    }
)


class ZoneTouch3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZoneTouch 3."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]

            # Prevent duplicate entries for the same host:port
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})

            client = ZoneTouch3Client(host, port)
            if await client.async_test_connection():
                return self.async_create_entry(
                    title=f"ZoneTouch 3 ({host})",
                    data=user_input,
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
