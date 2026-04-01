"""The Hup integration."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.components.camera import async_get_image
from homeassistant.components.webhook import (
    async_generate_url,
    async_register,
    async_unregister,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_get_all_descriptions

from datetime import timedelta

from .const import (
    CONF_CAMERA_INTERVAL,
    CONF_ENTITIES,
    CONF_WEBHOOK_URL,
    DEFAULT_CAMERA_INTERVAL,
    DOMAIN,
    SOURCE_TAG,
    WEBHOOK_ID_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hup from a config entry."""
    webhook_url = entry.data[CONF_WEBHOOK_URL]
    api_key = entry.data[CONF_API_KEY]
    watched_entities: set[str] = set(entry.options.get(CONF_ENTITIES, []))
    camera_interval: int = entry.options.get(CONF_CAMERA_INTERVAL, DEFAULT_CAMERA_INTERVAL)
    webhook_id = f"{WEBHOOK_ID_PREFIX}{entry.entry_id}"

    # Streaming URL — discovered from registration response
    streaming_url: str | None = None
    cancel_camera_timer = None

    # --- Outbound: push events to Hup ---

    async def _post_to_webhook(payload: dict) -> dict | None:
        """Post a payload to the Hup webhook. Returns response JSON if available."""
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
                        _LOGGER.debug("Sent event for %s to Hup", payload.get("entityId"))
                        try:
                            return await resp.json()
                        except Exception:
                            return None
                    else:
                        body = await resp.text()
                        _LOGGER.warning("Hup webhook failed (%s): %s", resp.status, body)
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.error("Failed to send event to Hup for %s", payload.get("entityId"))
        return None

    async def _stream_image(entity_id: str) -> bool:
        """Capture and stream a camera image directly to hup-server."""
        nonlocal streaming_url
        if not streaming_url:
            return False

        device_id = f"ha_{entity_id.split('.', 1)[1]}" if "." in entity_id else entity_id

        try:
            image = await async_get_image(hass, entity_id)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    streaming_url,
                    headers={
                        "x-api-key": api_key,
                        "x-device-id": device_id,
                        "Content-Type": "image/jpeg",
                    },
                    data=image.content,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Streamed image from %s to Hup", entity_id)
                        return True
                    else:
                        body = await resp.text()
                        _LOGGER.warning("Hup stream failed (%s): %s", resp.status, body)
        except Exception:
            _LOGGER.debug("Could not stream snapshot from %s", entity_id)
        return False

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
        is_camera = entity_id.startswith("camera.")

        if is_camera:
            # Camera state changes — stream image if we have a streaming URL,
            # send metadata to webhook
            image_streamed = await _stream_image(entity_id)
            await _post_to_webhook({
                "source": SOURCE_TAG,
                "type": "image",
                "entityId": entity_id,
                "name": friendly_name,
                "domain": "camera",
                "state": new_state.state,
                "attributes": dict(new_state.attributes),
                "oldState": old_state.state if old_state else None,
                "imageStreamed": image_streamed,
            })
        else:
            await _post_to_webhook({
                "source": SOURCE_TAG,
                "type": "data",
                "entityId": entity_id,
                "name": friendly_name,
                "domain": entity_id.split(".")[0],
                "state": new_state.state,
                "attributes": dict(new_state.attributes),
                "oldState": old_state.state if old_state else None,
            })

    # --- Periodic camera capture ---

    async def _capture_cameras(_now=None) -> None:
        """Periodically capture and stream all watched camera entities."""
        camera_entities = [e for e in watched_entities if e.startswith("camera.")]
        for entity_id in camera_entities:
            await _stream_image(entity_id)

    def _setup_camera_timer() -> None:
        """Set up or restart the periodic camera capture timer."""
        nonlocal cancel_camera_timer
        if cancel_camera_timer:
            cancel_camera_timer()
            cancel_camera_timer = None

        if camera_interval <= 0:
            _LOGGER.info("Camera capture disabled (interval=0)")
            return

        camera_entities = [e for e in watched_entities if e.startswith("camera.")]
        if not camera_entities:
            return

        cancel_camera_timer = async_track_time_interval(
            hass, _capture_cameras, timedelta(seconds=camera_interval)
        )
        _LOGGER.info(
            "Camera capture: %d camera(s) every %ds",
            len(camera_entities), camera_interval,
        )

    # --- Inbound: receive commands from Hup ---

    async def _handle_inbound_webhook(
        hass: HomeAssistant, webhook_id: str, request
    ):
        """Handle inbound requests from Hup."""
        from aiohttp import web

        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        req_api_key = request.headers.get("x-api-key")
        if req_api_key != api_key:
            return web.json_response({"error": "Unauthorized"}, status=401)

        action = data.get("action")

        if action == "get_entities":
            return await _handle_get_entities()
        elif action == "execute_command":
            return await _handle_execute_command(data)
        else:
            return web.json_response({"error": f"Unknown action: {action}"}, status=400)

    async def _handle_get_entities():
        from aiohttp import web

        service_descriptions = await async_get_all_descriptions(hass)
        entities = []

        for entity_id in watched_entities:
            state = hass.states.get(entity_id)
            if state is None:
                continue

            domain = entity_id.split(".")[0]
            domain_services = service_descriptions.get(domain, {})

            services = []
            for service_name, desc in domain_services.items():
                service_info = {
                    "service": f"{domain}.{service_name}",
                    "name": desc.get("name", service_name),
                    "description": desc.get("description", ""),
                    "fields": {},
                }
                for field_name, field in desc.get("fields", {}).items():
                    service_info["fields"][field_name] = {
                        "name": field.get("name", field_name),
                        "description": field.get("description", ""),
                        "required": field.get("required", False),
                        "example": field.get("example"),
                    }
                services.append(service_info)

            entities.append({
                "entityId": entity_id,
                "name": state.attributes.get("friendly_name", entity_id),
                "domain": domain,
                "state": state.state,
                "attributes": dict(state.attributes),
                "services": services,
            })

        return web.json_response({
            "source": SOURCE_TAG,
            "type": "data_contract",
            "entities": entities,
        })

    async def _handle_execute_command(data: dict):
        from aiohttp import web

        entity_id = data.get("entityId")
        service = data.get("service")
        service_data = data.get("data", {})

        if not entity_id or not service:
            return web.json_response({"error": "entityId and service are required"}, status=400)

        if entity_id not in watched_entities:
            return web.json_response({"error": f"Entity {entity_id} is not monitored by Hup"}, status=403)

        parts = service.split(".", 1)
        if len(parts) != 2:
            return web.json_response({"error": "service must be in domain.service format"}, status=400)

        domain, service_name = parts
        service_data["entity_id"] = entity_id

        try:
            await hass.services.async_call(domain, service_name, service_data, blocking=True)
        except Exception as err:
            _LOGGER.error("Failed to execute %s on %s: %s", service, entity_id, err)
            return web.json_response({"error": f"Failed to execute: {err}"}, status=500)

        state = hass.states.get(entity_id)
        return web.json_response({
            "source": SOURCE_TAG,
            "type": "data",
            "success": True,
            "entityId": entity_id,
            "state": state.state if state else None,
            "attributes": dict(state.attributes) if state else None,
        })

    # --- Register inbound webhook and event listener ---

    async_register(hass, DOMAIN, "Hup Inbound", webhook_id, _handle_inbound_webhook)

    inbound_url = async_generate_url(hass, webhook_id)
    _LOGGER.info("Hup inbound webhook registered at %s", inbound_url)

    # Send registration to Hup — response includes the streaming URL
    resp = await _post_to_webhook({
        "source": SOURCE_TAG,
        "type": "registration",
        "inboundUrl": inbound_url,
    })

    if resp and resp.get("streamingUrl"):
        streaming_url = resp["streamingUrl"]
        _LOGGER.info("Hup streaming URL: %s", streaming_url)

    def _update_entities() -> None:
        """Update watched entities and camera timer when options change."""
        nonlocal watched_entities, camera_interval
        watched_entities = set(entry.options.get(CONF_ENTITIES, []))
        camera_interval = entry.options.get(CONF_CAMERA_INTERVAL, DEFAULT_CAMERA_INTERVAL)
        _setup_camera_timer()

    entry.async_on_unload(entry.add_update_listener(_on_options_update))

    cancel = hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_changed)

    # Start camera capture timer
    _setup_camera_timer()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "cancel": cancel,
        "update": _update_entities,
        "webhook_id": webhook_id,
    }

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
        async_unregister(hass, data["webhook_id"])
    return True
