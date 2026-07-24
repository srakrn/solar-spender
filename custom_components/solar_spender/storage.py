"""Versioned persistence for conservative learned AC draw hints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .feedback import LearnedDrawEstimate

_STORAGE_VERSION = 1


class LearningStore:
    """Persist learned draw estimates independently from user configuration."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store[dict[str, Any]](
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}.learning_{entry_id}",
        )

    async def async_load(self) -> dict[str, LearnedDrawEstimate]:
        """Load valid estimates and ignore malformed records."""
        data = await self._store.async_load() or {}
        estimates: dict[str, LearnedDrawEstimate] = {}
        for entity_id, value in data.get("draw_estimates", {}).items():
            try:
                estimates[entity_id] = LearnedDrawEstimate(
                    estimate_w=float(value["estimate_w"]),
                    samples=int(value["samples"]),
                    updated_at=datetime.fromisoformat(value["updated_at"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return estimates

    async def async_save(
        self,
        estimates: dict[str, LearnedDrawEstimate],
    ) -> None:
        """Atomically save the current learned hints."""
        await self._store.async_save(
            {
                "draw_estimates": {
                    entity_id: {
                        "estimate_w": estimate.estimate_w,
                        "samples": estimate.samples,
                        "updated_at": estimate.updated_at.isoformat(),
                    }
                    for entity_id, estimate in estimates.items()
                }
            }
        )
