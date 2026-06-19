"""Structural eval: Preferences loader validates YAML and rejects malformed input.

No LLM calls required — runs in <5s.
"""

import tempfile
from pathlib import Path

import pytest

from src.config.loader import load_preferences


class TestPreferencesLoader:
    def test_valid_preferences_load(self):
        prefs = load_preferences()
        assert prefs.vehicle.vin == "SAMPLEVIN00000000"
        assert prefs.vehicle.mileage == 50000
        assert prefs.search.location_zip == "10001"
        assert prefs.scoring.threshold_notify == 0.75

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_preferences(Path("/nonexistent/path.yaml"))

    def test_malformed_yaml_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("not: valid: yaml: [broken")
            f.flush()
            with pytest.raises(Exception):
                load_preferences(Path(f.name))

    def test_missing_required_field_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("vehicle:\n  vin: test\n")
            f.flush()
            with pytest.raises(Exception):
                load_preferences(Path(f.name))

    def test_invalid_threshold_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            # threshold_notify > 1.0 should fail validation
            f.write("""
vehicle:
  vin: "TEST"
  mileage: 50000
  condition: "good"
  trade_in_floor_usd: 18000
search:
  location_zip: "10001"
  radius_miles: 75
  max_vehicle_age_years: 3
  body_styles: ["sedan"]
excluded_brands: []
excluded_models: []
deal_criteria:
  max_effective_monthly_usd: 450
  min_discount_off_msrp_pct: 8.0
  acceptable_apr_max: 2.9
  lease_to_own_preferred: true
  zero_percent_financing_preferred: true
scoring:
  threshold_notify: 5.0
  threshold_draft_email: 0.8
""")
            f.flush()
            with pytest.raises(Exception):
                load_preferences(Path(f.name))


class TestTierCapsAtLoad:
    """The DEFAULT_TIER_CAPS clamp must apply at LOAD time, not just at
    write time. Without this, a user whose stored overrides don't pin
    max_pages falls through to the YAML default and exceeds MarketCheck's
    free-tier pagination limit, returning a 422.
    """

    def test_default_tier_clamps_max_pages_from_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default-tier user with stored max_pages=10 → load-time clamp to 1."""
        import src.config.loader as loader_mod

        # Stub the user lookup so we can simulate a default-tier user whose
        # overrides predate the load-time cap.
        monkeypatch.setattr(
            loader_mod,
            "_load_user_overrides",
            lambda _u: ({"search": {"max_pages": 10}}, "default"),
        )
        monkeypatch.setattr(loader_mod, "_load_global_overrides", lambda: {})
        monkeypatch.setattr(loader_mod, "_is_test_mode", lambda: False)

        prefs = load_preferences(user_id="user-default")
        assert prefs.search.max_pages == 1

    def test_byok_tier_does_not_clamp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BYOK users on paid MarketCheck tiers can legitimately use more pages."""
        import src.config.loader as loader_mod

        monkeypatch.setattr(
            loader_mod,
            "_load_user_overrides",
            lambda _u: ({"search": {"max_pages": 10}}, "byok"),
        )
        monkeypatch.setattr(loader_mod, "_load_global_overrides", lambda: {})
        monkeypatch.setattr(loader_mod, "_is_test_mode", lambda: False)

        prefs = load_preferences(user_id="user-byok")
        assert prefs.search.max_pages == 10

    def test_default_tier_no_user_overrides_still_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the YAML default itself exceeded the cap, the clamp still applies."""
        import src.config.loader as loader_mod

        monkeypatch.setattr(
            loader_mod,
            "_load_user_overrides",
            lambda _u: ({}, "default"),  # no overrides; YAML defaults flow through
        )
        # Force a higher YAML default into the merged dict via global overrides.
        monkeypatch.setattr(
            loader_mod, "_load_global_overrides", lambda: {"search": {"max_pages": 10}}
        )
        monkeypatch.setattr(loader_mod, "_is_test_mode", lambda: False)

        prefs = load_preferences(user_id="user-default")
        assert prefs.search.max_pages == 1
