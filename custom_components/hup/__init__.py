"""The Hup integration."""

from __future__ import annotations

import base64
import logging

import aiohttp

from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant

from .const import CONF_DEVICE_ID, CONF_ENTITIES, CONF_WEBHOOK_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hup from a config entry."""
    webhook_url = entry.data[CONF_WEBHOOK_URL]
    api_key = entry.data[CONF_API_KEY]
    device_id = entry.data[CONF_DEVICE_ID]
    watched_entities: set[str] = set(entry.options.get(CONF_ENTITIES, []))

    async def _post_to_webhook(payload: dict) -> None:
        """Post a payload to the Hup webhook."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug(
                            "Sent event for %s to Hup",
                            payload.get("entityId"),
                        )
                    else:
                        body = await resp.text()
                        _LOGGER.warning(
                            "Hup webhook failed (%s): %s", resp.status, body
                        )
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.error(
                "Failed to send event to Hup for %s", payload.get("entityId")
            )

    async def _on_state_changed(event: Event) -> None:
        """Handle a state change event for watched entities."""
        entity_id = event.data.get("entity_id")
        if entity_id not in watched_entities:
            return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if new_state is None:
            return

        friendly_name = new_state.attributes.get("friendly_name", entity_id)

        # Consistent payload — image is always present (null for non-cameras)
        payload = {
            "deviceId": device_id,
            "entityId": entity_id,
            "name": friendly_name,
            "domain": entity_id.split(".")[0],
            "state": new_state.state,
            "attributes": dict(new_state.attributes),
            "oldState": old_state.state if old_state else None,
            "image": None,
        }

        # For cameras, capture a snapshot into the image field
        if entity_id.startswith("camera."):
            try:
                image = await async_get_image(hass, entity_id)
                payload["image"] = base64.b64encode(
                    image.content
                ).decode("utf-8")
            except Exception:
                _LOGGER.debug(
                    "Could not capture snapshot from %s", entity_id
                )

        await _post_to_webhook(payload)

    def _update_entities() -> None:
        """Update watched entities when options change."""
        nonlocal watched_entities
        watched_entities = set(entry.options.get(CONF_ENTITIES, []))

    entry.async_on_unload(entry.add_update_listener(_on_options_update))

    cancel = hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_changed)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"cancel": cancel, "update": _update_entities}

    return True


async def _on_options_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — refresh watched entities without reloading."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data:
        data["update"]()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        data["cancel"]()
    return True
