"""Config flow for Hup integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ENTITIES,
    CONF_WEBHOOK_URL,
    DOMAIN,
)


class HupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hup."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HupOptionsFlow:
        """Get the options flow for this handler."""
        return HupOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — connection details only."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            webhook_url = user_input[CONF_WEBHOOK_URL]

            if not api_key.startswith("hup_ext_"):
                errors["base"] = "invalid_key_format"
            else:
                valid = await self._validate_api_key(webhook_url, api_key)
                if valid:
                    return self.async_create_entry(
                        title="Hup",
                        data=user_input,
                    )
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_WEBHOOK_URL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def _validate_api_key(self, webhook_url: str, api_key: str) -> bool:
        """Validate the API key by making a test request to the webhook URL."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={"type": "validation"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    # 401 = bad key, anything else means the key is valid
                    return resp.status != 401
        except (aiohttp.ClientError, TimeoutError):
            return False


class HupOptionsFlow(OptionsFlow):
    """Handle Hup options — add/remove monitored entities."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the monitored entities."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_entities = self.config_entry.options.get(CONF_ENTITIES, [])

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENTITIES, default=current_entities
                    ): EntitySelector(EntitySelectorConfig(multiple=True)),
                }
            ),
        )
