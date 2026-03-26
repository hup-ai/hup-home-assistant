"""Config flow for Hup integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    API_URL,
    CONF_CAMERA_ENTITY,
    CONF_DEVICE_ID,
    CONF_SNAPSHOT_INTERVAL,
    DEFAULT_SNAPSHOT_INTERVAL,
    DOMAIN,
)


class HupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]

            if not api_key.startswith("hup_ext_"):
                errors["base"] = "invalid_key_format"
            else:
                valid = await self._validate_api_key(api_key)
                if valid:
                    camera = user_input[CONF_CAMERA_ENTITY]
                    return self.async_create_entry(
                        title=f"Hup ({camera})",
                        data=user_input,
                    )
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Required(CONF_DEVICE_ID): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_CAMERA_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="camera")
                    ),
                    vol.Required(
                        CONF_SNAPSHOT_INTERVAL,
                        default=DEFAULT_SNAPSHOT_INTERVAL,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=60,
                            step=1,
                            unit_of_measurement="minutes",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def _validate_api_key(self, api_key: str) -> bool:
        """Validate the API key by making a test request."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    API_URL,
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "image/jpeg",
                    },
                    data=b"\xff\xd8\xff\xe0",  # minimal JPEG header
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    # 401 = bad key, anything else means the key is valid
                    return resp.status != 401
        except (aiohttp.ClientError, TimeoutError):
            return False
