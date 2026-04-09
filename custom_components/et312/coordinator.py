"""Data coordinator for the ET312 integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN
from .et312 import ET312Client, ET312ConnectionError, ET312State


class ET312DataUpdateCoordinator(DataUpdateCoordinator[ET312State]):
    """Coordinate ET312 state updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client: ET312Client,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> ET312State:
        """Fetch data from the ET312 device."""
        try:
            return await self.client.async_get_state()
        except ET312ConnectionError as err:
            raise UpdateFailed(f"Unable to update ET312 state: {err}") from err
