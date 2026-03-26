"""The Hup integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import API_URL, CONF_CAMERA_ENTITY, CONF_SNAPSHOT_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hup from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    camera_entity = entry.data[CONF_CAMERA_ENTITY]
    interval = int(entry.data[CONF_SNAPSHOT_INTERVAL])

    async def _capture_and_upload(_now=None) -> None:
        """Capture a snapshot and upload to Hup."""
        try:
            image = await async_get_image(hass, camera_entity)
        except Exception:
            _LOGGER.error("Failed to get snapshot from %s", camera_entity)
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    API_URL,
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "image/jpeg",
                    },
                    data=image.content,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug(
                            "Uploaded snapshot from %s to Hup", camera_entity
                        )
                    else:
                        body = await resp.text()
                        _LOGGER.warning(
                            "Hup upload failed (%s): %s", resp.status, body
                        )
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.error("Failed to upload snapshot to Hup")

    # Run immediately on setup, then on interval
    hass.async_create_task(_capture_and_upload())

    cancel = async_track_time_interval(
        hass, _capture_and_upload, timedelta(minutes=interval)
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = cancel

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    cancel = hass.data[DOMAIN].pop(entry.entry_id, None)
    if cancel:
        cancel()
    return True
