"""Regression tests for synchronized Home Assistant timing controls."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
TIMING_KEYS = {
    "settling_seconds",
    "feedback_sample_count",
    "feedback_timeout_minutes",
    "input_max_age_minutes",
    "next_load_delay_minutes",
}


class ConfigurationSurfaceTests(unittest.TestCase):
    """Keep device entities, Settings, and the panel on one option contract."""

    def test_every_timing_key_is_exposed_on_all_three_surfaces(self) -> None:
        sources = {
            "device entities": (
                ROOT / "custom_components/solar_spender/number.py"
            ).read_text(),
            "Settings Configure form": (
                ROOT / "custom_components/solar_spender/config_flow.py"
            ).read_text(),
            "sidebar panel": (
                ROOT / "frontend/src/solar-spender-panel.js"
            ).read_text(),
        }

        for surface, source in sources.items():
            with self.subTest(surface=surface):
                for key in TIMING_KEYS:
                    self.assertIn(key.upper(), source.upper())

    def test_retired_spacing_control_is_not_exposed(self) -> None:
        exposed_sources = "\n".join(
            (
                ROOT / path
            ).read_text()
            for path in (
                "custom_components/solar_spender/number.py",
                "custom_components/solar_spender/config_flow.py",
                "frontend/src/solar-spender-panel.js",
            )
        )

        self.assertNotIn("feedback_sample_interval_minutes", exposed_sources)

    def test_probe_limits_and_load_power_expiry_are_exposed(self) -> None:
        backend = (
            ROOT / "custom_components/solar_spender/models.py"
        ).read_text()
        panel = (
            ROOT / "frontend/src/solar-spender-panel.js"
        ).read_text()

        for key in (
            "probe_grid_import_allowance_w",
            "probe_max_fallback_energy_wh",
            "power_zero_after_minutes",
        ):
            with self.subTest(key=key):
                self.assertIn(key, backend)
                self.assertIn(key, panel)


if __name__ == "__main__":
    unittest.main()
