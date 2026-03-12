"""Tests for ai_bms_pipeline.analysis — deterministic checks + mocked LLM path."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ai_bms_pipeline import analysis as analysis_module
from ai_bms_pipeline.analysis import (
    Finding,
    _check_economizer,
    _check_fan_imbalance,
    _check_hws_reset,
    _check_scheduling,
    _check_simultaneous_heat_cool,
    _check_static_pressure,
    _check_supply_air_temperature,
    _filter_useful_snapshots,
    _snapshot_data_score,
    load_all_buildings,
    load_building_snapshots,
    run_deterministic_checks,
)
from ai_bms_pipeline.utils import DEFAULT_FACTORS

# ─── Snapshot fixtures ────────────────────────────────────────────────────────


def _snap(
    *,
    oat_f: float | None = None,
    rh_pct: float | None = None,
    season: str | None = None,
    hws_actual: float | None = None,
    hws_setpoint: float | None = None,
    hws_reset: dict | None = None,
    vav_heat: float | None = None,
    chws_actual: float | None = None,
    cooling_units: list | None = None,
    air_systems: list | None = None,
    zones: list | None = None,
    timestamp: str = "2026-03-10T10:00:00+00:00",
    building_id: str = "test-bldg",
) -> dict:
    """Build a minimal snapshot dict for testing."""
    return {
        "building_id": building_id,
        "timestamp": timestamp,
        "conditions": {"oat_f": oat_f, "rh_pct": rh_pct, "season": season},
        "heating_plant": {
            "hws_temp_actual_f": hws_actual,
            "hws_temp_setpoint_f": hws_setpoint,
            "hws_oat_reset_active": hws_reset,
            "vav_heat_request_pct": vav_heat,
            "boilers": None,
        },
        "cooling_plant": {
            "chws_temp_actual_f": chws_actual,
            "units": cooling_units,
        },
        "air_systems": air_systems or [],
        "zones": zones or [],
        "anomalies": [],
    }


def _ahu(
    system_id: str = "AHU-1",
    *,
    fans: list | None = None,
    mode: str | None = None,
    out_of_schedule: bool | None = None,
    economizer: dict | None = None,
    sa_actual: float | None = None,
    sa_setpoint: float | None = None,
    return_air: float | None = None,
    sp_actual: float | None = None,
    sp_setpoint: float | None = None,
    vav_demand: float | None = None,
) -> dict:
    return {
        "id": system_id,
        "mode": mode,
        "out_of_schedule": out_of_schedule,
        "fans": fans or [],
        "economizer": economizer or {},
        "temperatures": {
            "supply_air_actual_f": sa_actual,
            "supply_air_setpoint_f": sa_setpoint,
            "return_air_f": return_air,
        },
        "sa_static_pressure_actual_inwc": sp_actual,
        "sa_static_pressure_setpoint_inwc": sp_setpoint,
        "vav_demand_pct": vav_demand,
    }


# ─── _check_hws_reset ─────────────────────────────────────────────────────────


class TestCheckHwsReset:
    def test_flags_high_hws_with_warm_oat(self):
        snap = _snap(hws_actual=160, oat_f=55)
        findings = _check_hws_reset([snap])
        assert len(findings) == 1
        assert findings[0].severity == "high"
        assert "160" in findings[0].description

    def test_no_finding_when_hws_not_elevated(self):
        snap = _snap(hws_actual=120, oat_f=55)
        findings = _check_hws_reset([snap])
        assert findings == []

    def test_no_finding_when_oat_cold(self):
        snap = _snap(hws_actual=160, oat_f=30)
        findings = _check_hws_reset([snap])
        assert findings == []

    def test_flags_fixed_setpoint_without_reset_curve(self):
        snap = _snap(hws_setpoint=145, hws_reset=None)
        findings = _check_hws_reset([snap])
        assert len(findings) == 1
        assert findings[0].severity == "medium"

    def test_no_finding_when_reset_curve_configured(self):
        reset = {"oat_min_f": 20, "oat_max_f": 65, "hws_min_f": 130, "hws_max_f": 180}
        snap = _snap(hws_setpoint=145, hws_reset=reset)
        findings = _check_hws_reset([snap])
        assert findings == []

    def test_no_finding_when_no_data(self):
        snap = _snap()
        findings = _check_hws_reset([snap])
        assert findings == []


# ─── _check_fan_imbalance ─────────────────────────────────────────────────────


class TestCheckFanImbalance:
    def test_flags_large_speed_spread(self):
        fans = [
            {"id": "SF-1", "vfd_pct": 80, "status": "on"},
            {"id": "SF-2", "vfd_pct": 40, "status": "on"},
        ]
        snap = _snap(air_systems=[_ahu(fans=fans)])
        findings = _check_fan_imbalance([snap])
        assert len(findings) == 1
        assert findings[0].severity == "high"  # spread > 30

    def test_medium_severity_for_moderate_spread(self):
        fans = [
            {"id": "SF-1", "vfd_pct": 70, "status": "on"},
            {"id": "SF-2", "vfd_pct": 50, "status": "on"},
        ]
        snap = _snap(air_systems=[_ahu(fans=fans)])
        findings = _check_fan_imbalance([snap])
        assert len(findings) == 1
        assert findings[0].severity == "medium"

    def test_no_finding_when_spread_small(self):
        fans = [
            {"id": "SF-1", "vfd_pct": 60},
            {"id": "SF-2", "vfd_pct": 55},
        ]
        snap = _snap(air_systems=[_ahu(fans=fans)])
        findings = _check_fan_imbalance([snap])
        assert findings == []

    def test_no_finding_with_single_fan(self):
        fans = [{"id": "SF-1", "vfd_pct": 80}]
        snap = _snap(air_systems=[_ahu(fans=fans)])
        findings = _check_fan_imbalance([snap])
        assert findings == []

    def test_fans_without_vfd_ignored(self):
        fans = [
            {"id": "SF-1", "vfd_pct": None},
            {"id": "SF-2", "vfd_pct": None},
        ]
        snap = _snap(air_systems=[_ahu(fans=fans)])
        findings = _check_fan_imbalance([snap])
        assert findings == []


# ─── _check_economizer ────────────────────────────────────────────────────────


class TestCheckEconomizer:
    def test_flags_missed_free_cooling(self):
        econ = {"active": False, "position_pct": 0.0}
        ahu = _ahu(economizer=econ, return_air=72)
        snap = _snap(oat_f=55, air_systems=[ahu])
        findings = _check_economizer([snap])
        assert len(findings) == 1
        assert "free cooling" in findings[0].description.lower()

    def test_no_free_cooling_finding_when_oat_not_that_low(self):
        econ = {"active": False, "position_pct": 0.0}
        ahu = _ahu(economizer=econ, return_air=72)
        snap = _snap(oat_f=70, air_systems=[ahu])  # only 2°F below return
        findings = _check_economizer([snap])
        assert not any("free cooling" in f.description.lower() for f in findings)

    def test_flags_humidity_risk(self):
        econ = {"active": True, "position_pct": 60.0}
        ahu = _ahu(economizer=econ)
        snap = _snap(rh_pct=80, air_systems=[ahu])
        findings = _check_economizer([snap])
        assert len(findings) == 1
        assert "humid" in findings[0].description.lower()

    def test_no_humidity_finding_when_rh_low(self):
        econ = {"active": True, "position_pct": 60.0}
        snap = _snap(rh_pct=50, air_systems=[_ahu(economizer=econ)])
        findings = _check_economizer([snap])
        assert findings == []


# ─── _check_simultaneous_heat_cool ───────────────────────────────────────────


class TestCheckSimultaneousHeatCool:
    def test_flags_simultaneous_active_on_warm_day(self):
        snap = _snap(hws_actual=130, chws_actual=42, oat_f=60)
        findings = _check_simultaneous_heat_cool([snap])
        assert len(findings) == 1
        assert "simultaneously" in findings[0].description.lower()

    def test_no_finding_on_cold_day(self):
        snap = _snap(hws_actual=130, chws_actual=42, oat_f=40)
        findings = _check_simultaneous_heat_cool([snap])
        assert findings == []

    def test_no_finding_when_only_heating(self):
        snap = _snap(hws_actual=130, oat_f=60)
        findings = _check_simultaneous_heat_cool([snap])
        assert findings == []

    def test_no_finding_when_only_cooling(self):
        snap = _snap(chws_actual=42, oat_f=60)
        findings = _check_simultaneous_heat_cool([snap])
        assert findings == []


# ─── _check_scheduling ────────────────────────────────────────────────────────


class TestCheckScheduling:
    def test_flags_out_of_schedule_system(self):
        ahu = _ahu(out_of_schedule=True)
        snap = _snap(air_systems=[ahu])
        findings = _check_scheduling([snap])
        assert len(findings) == 1
        assert findings[0].severity == "high"

    def test_flags_occupied_mode_on_weekend(self):
        # 2026-03-14 is a Saturday
        ahu = _ahu(mode="occupied")
        snap = _snap(air_systems=[ahu], timestamp="2026-03-14T10:00:00+00:00")
        findings = _check_scheduling([snap])
        assert len(findings) == 1
        assert "weekend" in findings[0].description.lower()

    def test_flags_occupied_mode_at_off_hours(self):
        # 2026-03-10 is a Tuesday, 03:00
        ahu = _ahu(mode="occupied")
        snap = _snap(air_systems=[ahu], timestamp="2026-03-10T03:00:00+00:00")
        findings = _check_scheduling([snap])
        assert len(findings) == 1

    def test_no_finding_for_occupied_during_business_hours(self):
        # 2026-03-10 is a Tuesday, 10:00
        ahu = _ahu(mode="occupied")
        snap = _snap(air_systems=[ahu], timestamp="2026-03-10T10:00:00+00:00")
        findings = _check_scheduling([snap])
        assert findings == []

    def test_no_finding_when_no_systems(self):
        snap = _snap()
        findings = _check_scheduling([snap])
        assert findings == []


# ─── _check_static_pressure ───────────────────────────────────────────────────


class TestCheckStaticPressure:
    def test_flags_pressure_over_setpoint(self):
        ahu = _ahu(sp_actual=1.5, sp_setpoint=1.0, vav_demand=60)
        snap = _snap(air_systems=[ahu])
        findings = _check_static_pressure([snap])
        assert len(findings) == 1

    def test_no_finding_when_pressure_at_setpoint(self):
        ahu = _ahu(sp_actual=1.0, sp_setpoint=1.0, vav_demand=60)
        snap = _snap(air_systems=[ahu])
        findings = _check_static_pressure([snap])
        assert findings == []

    def test_no_finding_when_vav_demand_high(self):
        # Pressure elevated but VAV demand is high, so this is expected
        ahu = _ahu(sp_actual=1.5, sp_setpoint=1.0, vav_demand=90)
        snap = _snap(air_systems=[ahu])
        findings = _check_static_pressure([snap])
        assert findings == []

    def test_no_finding_when_setpoint_missing(self):
        ahu = _ahu(sp_actual=1.5, sp_setpoint=None)
        snap = _snap(air_systems=[ahu])
        findings = _check_static_pressure([snap])
        assert findings == []


# ─── _check_supply_air_temperature ───────────────────────────────────────────


class TestCheckSupplyAirTemperature:
    def test_flags_sa_below_setpoint(self):
        ahu = _ahu(sa_actual=52, sa_setpoint=58)
        snap = _snap(air_systems=[ahu])
        findings = _check_supply_air_temperature([snap])
        assert len(findings) >= 1
        assert any("setpoint" in f.description.lower() for f in findings)

    def test_flags_low_sa_in_heating_season(self):
        ahu = _ahu(sa_actual=52)
        snap = _snap(air_systems=[ahu], season="heating", oat_f=45)
        findings = _check_supply_air_temperature([snap])
        assert len(findings) >= 1

    def test_no_finding_when_sa_normal(self):
        ahu = _ahu(sa_actual=58, sa_setpoint=58)
        snap = _snap(air_systems=[ahu])
        findings = _check_supply_air_temperature([snap])
        assert findings == []

    def test_no_finding_when_no_sa_data(self):
        snap = _snap(air_systems=[_ahu()])
        findings = _check_supply_air_temperature([snap])
        assert findings == []


# ─── run_deterministic_checks ────────────────────────────────────────────────


class TestRunDeterministicChecks:
    def test_deduplicates_same_domain_same_system(self):
        """Two HWS findings for same system should deduplicate to one."""
        snap1 = _snap(hws_actual=160, oat_f=55)
        snap2 = _snap(hws_actual=165, oat_f=60)
        findings = run_deterministic_checks([snap1, snap2])
        hws_findings = [f for f in findings if f.domain == "hws_reset"]
        assert len(hws_findings) == 1

    def test_keeps_highest_severity_on_dedup(self):
        """High-severity finding should win over medium for same domain+system."""
        # First snap produces medium (fixed setpoint, no OAT data)
        snap1 = _snap(hws_setpoint=145)
        # Second snap produces high (actual temp above 130 with warm OAT)
        snap2 = _snap(hws_actual=160, oat_f=55)
        findings = run_deterministic_checks([snap1, snap2])
        hws_findings = [f for f in findings if f.domain == "hws_reset"]
        assert len(hws_findings) == 1
        assert hws_findings[0].severity == "high"

    def test_multiple_domains_not_deduplicated(self):
        fans = [
            {"id": "SF-1", "vfd_pct": 80},
            {"id": "SF-2", "vfd_pct": 40},
        ]
        snap = _snap(
            hws_actual=160,
            oat_f=55,
            air_systems=[_ahu(fans=fans)],
        )
        findings = run_deterministic_checks([snap])
        domains = {f.domain for f in findings}
        assert "hws_reset" in domains
        assert "fan_balancing" in domains

    def test_empty_snapshots_returns_empty(self):
        assert run_deterministic_checks([]) == []


# ─── _snapshot_data_score ─────────────────────────────────────────────────────


class TestSnapshotDataScore:
    def test_empty_snapshot_scores_zero(self):
        assert _snapshot_data_score({}) == 0

    def test_oat_counts(self):
        snap = _snap(oat_f=55)
        assert _snapshot_data_score(snap) >= 1

    def test_hws_temp_counts(self):
        snap = _snap(hws_actual=160)
        assert _snapshot_data_score(snap) >= 1

    def test_air_system_sa_temp_counts(self):
        ahu = _ahu(sa_actual=58)
        snap = _snap(air_systems=[ahu])
        assert _snapshot_data_score(snap) >= 1

    def test_fan_vfd_counts(self):
        fans = [{"id": "SF-1", "vfd_pct": 75}]
        snap = _snap(air_systems=[_ahu(fans=fans)])
        assert _snapshot_data_score(snap) >= 1

    def test_rich_snapshot_scores_higher_than_sparse(self):
        sparse = _snap(oat_f=55)
        rich = _snap(
            oat_f=55,
            rh_pct=50,
            hws_actual=160,
            chws_actual=44,
            air_systems=[_ahu(sa_actual=58, return_air=72)],
        )
        assert _snapshot_data_score(rich) > _snapshot_data_score(sparse)


# ─── _filter_useful_snapshots ────────────────────────────────────────────────


class TestFilterUsefulSnapshots:
    def test_keeps_snapshot_with_enough_data(self):
        snap = _snap(oat_f=55, hws_actual=160)
        result = _filter_useful_snapshots([snap], "bldg")
        assert len(result) == 1

    def test_removes_snapshot_with_insufficient_data(self):
        snap = _snap()  # all nulls → score = 0
        result = _filter_useful_snapshots([snap], "bldg")
        assert result == []

    def test_removes_classifier_rejected_snapshot(self):
        snap = _snap(oat_f=55, hws_actual=160)
        snap["classifier"] = {"is_bms_screenshot": False, "reason": "not bms"}
        result = _filter_useful_snapshots([snap], "bldg")
        assert result == []

    def test_keeps_snapshot_without_classifier_field(self):
        snap = _snap(oat_f=55, hws_actual=160)
        snap.pop("classifier", None)
        result = _filter_useful_snapshots([snap], "bldg")
        assert len(result) == 1

    def test_mixed_list_filters_correctly(self):
        good = _snap(oat_f=55, hws_actual=160)
        bad = _snap()
        result = _filter_useful_snapshots([good, bad], "bldg")
        assert len(result) == 1
        assert result[0] is good


# ─── load_building_snapshots ──────────────────────────────────────────────────


class TestLoadBuildingSnapshots:
    def test_loads_json_files_sorted(self, tmp_path: Path):
        building_dir = tmp_path / "bldg-1"
        building_dir.mkdir()
        snap1 = {"building_id": "bldg-1", "timestamp": "2026-03-10T10:00:00+00:00"}
        snap2 = {"building_id": "bldg-1", "timestamp": "2026-03-10T11:00:00+00:00"}
        (building_dir / "2026-03-10T10.JSON").write_text(json.dumps(snap1))
        (building_dir / "2026-03-10T11.JSON").write_text(json.dumps(snap2))

        result = load_building_snapshots(building_dir)
        assert len(result) == 2
        assert result[0]["timestamp"] == "2026-03-10T10:00:00+00:00"

    def test_ignores_invalid_json(self, tmp_path: Path):
        building_dir = tmp_path / "bldg-1"
        building_dir.mkdir()
        (building_dir / "good.JSON").write_text(
            '{"building_id": "bldg-1", "timestamp": "t"}'
        )
        (building_dir / "bad.JSON").write_text("not json")

        result = load_building_snapshots(building_dir)
        assert len(result) == 1

    def test_empty_directory_returns_empty(self, tmp_path: Path):
        building_dir = tmp_path / "empty"
        building_dir.mkdir()
        assert load_building_snapshots(building_dir) == []

    def test_ignores_lowercase_json_extension(self, tmp_path: Path):
        """Only .JSON (uppercase) files are loaded — lowercase .json are not snapshots."""
        building_dir = tmp_path / "bldg-1"
        building_dir.mkdir()
        (building_dir / "snap.JSON").write_text(
            '{"building_id": "b", "timestamp": "t"}'
        )
        (building_dir / "other.json").write_text(
            '{"building_id": "b", "timestamp": "t"}'
        )

        result = load_building_snapshots(building_dir)
        assert len(result) == 1


# ─── load_all_buildings ───────────────────────────────────────────────────────


class TestLoadAllBuildings:
    def test_loads_multiple_buildings(self, tmp_path: Path):
        for bid in ("bldg-1", "bldg-2"):
            d = tmp_path / bid
            d.mkdir()
            snap = {"building_id": bid, "timestamp": "2026-03-10T10:00:00+00:00"}
            (d / "snap.JSON").write_text(json.dumps(snap))

        buildings = load_all_buildings(tmp_path)
        assert set(buildings.keys()) == {"bldg-1", "bldg-2"}

    def test_uses_building_id_from_snapshot(self, tmp_path: Path):
        d = tmp_path / "dir-name"
        d.mkdir()
        snap = {"building_id": "actual-bldg-id", "timestamp": "t"}
        (d / "snap.JSON").write_text(json.dumps(snap))

        buildings = load_all_buildings(tmp_path)
        assert "actual-bldg-id" in buildings

    def test_skips_empty_subdirs(self, tmp_path: Path):
        (tmp_path / "empty").mkdir()
        d = tmp_path / "bldg-1"
        d.mkdir()
        (d / "snap.JSON").write_text('{"building_id": "bldg-1", "timestamp": "t"}')

        buildings = load_all_buildings(tmp_path)
        assert "bldg-1" in buildings
        assert "empty" not in buildings

    def test_skips_files_in_extracted_dir(self, tmp_path: Path):
        (tmp_path / "stray.json").write_text("{}")
        d = tmp_path / "bldg-1"
        d.mkdir()
        (d / "snap.JSON").write_text('{"building_id": "bldg-1", "timestamp": "t"}')

        buildings = load_all_buildings(tmp_path)
        assert len(buildings) == 1


# ─── analyze_building (mocked LLM) ───────────────────────────────────────────


def _flat_ecm_response(
    building_id: str = "test-bldg",
    therms_yr: float | None = 500.0,
) -> dict:
    """Fake LLM output in the flat ECM schema format."""
    return {
        "building_id": building_id,
        "analysis_date": "2026-03-10",
        "analyst_notes": None,
        "ecms": [
            {
                "id": "ECM-01",
                "name": "HWS OAT Reset",
                "category": "setpoint_reset",
                "description": "Reset HWS temperature based on outdoor air temperature.",
                "affected_systems": ["heating_plant"],
                "assumptions": ["Gas-fired boiler"],
                "kwh_yr": None,
                "therms_yr": therms_yr,
                "mlb_yr": None,
                "capital_cost_usd": 5000.0,
                "complexity": "low",
                "priority": "high",
                "confidence": "medium",
                "implementation_notes": "Adjust BAS setpoint curve.",
            }
        ],
        "key_findings": ["HWS elevated relative to OAT"],
        "priority_actions": ["Implement OAT reset on HWS"],
        "open_questions": ["What is the current BAS control logic?"],
    }


def _rich_snap() -> dict:
    """Snapshot with enough data to pass the quality filter."""
    return _snap(oat_f=55, hws_actual=160, chws_actual=44)


class TestAnalyzeBuilding:
    def test_expands_flat_ecm_to_nested_savings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ai_bms_pipeline.image_ingest as _ii

        fake_output = _flat_ecm_response()

        class FakeMessages:
            def create(self, **kwargs):
                return object()

        class FakeClient:
            messages = FakeMessages()

        monkeypatch.setattr(
            analysis_module, "_get_client", lambda client=None: FakeClient()
        )
        monkeypatch.setattr(
            _ii, "_extract_json_from_response", lambda _: dict(fake_output)
        )

        result = analysis_module.analyze_building("test-bldg", [_rich_snap()])

        ecm = result["ecms"][0]
        assert "savings" in ecm
        assert "gas" in ecm["savings"]
        assert ecm["savings"]["gas"]["therms_yr"] == pytest.approx(500.0)
        assert ecm["savings"]["gas"]["cost_usd_yr"] == pytest.approx(400.0)
        assert "electricity" not in ecm["savings"]

    def test_expands_implementation_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ai_bms_pipeline.image_ingest as _ii

        fake_output = _flat_ecm_response()
        monkeypatch.setattr(
            analysis_module, "_get_client", lambda client=None: _FakeClient()
        )
        monkeypatch.setattr(
            _ii, "_extract_json_from_response", lambda _: dict(fake_output)
        )

        result = analysis_module.analyze_building("test-bldg", [_rich_snap()])
        impl = result["ecms"][0]["implementation"]
        assert impl["capital_cost_usd"] == pytest.approx(5000.0)
        assert impl["requires_capital"] is True
        assert impl["complexity"] == "low"
        assert impl["payback_years"] is not None  # capital / savings

    def test_injects_totals_factors_building_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ai_bms_pipeline.image_ingest as _ii

        fake_output = _flat_ecm_response()
        monkeypatch.setattr(
            analysis_module, "_get_client", lambda client=None: _FakeClient()
        )
        monkeypatch.setattr(
            _ii, "_extract_json_from_response", lambda _: dict(fake_output)
        )

        result = analysis_module.analyze_building("test-bldg", [_rich_snap()])
        assert result["building_id"] == "test-bldg"
        assert "totals" in result
        assert "factors" in result
        assert result["totals"]["total"]["cost_usd_yr"] > 0

    def test_raises_when_no_useful_snapshots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            analysis_module, "_get_client", lambda client=None: _FakeClient()
        )
        empty_snap = _snap()  # all nulls → filtered out
        with pytest.raises(ValueError, match="No usable snapshots"):
            analysis_module.analyze_building("test-bldg", [empty_snap])

    def test_totals_sums_ecm_savings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ai_bms_pipeline.image_ingest as _ii

        fake_output = _flat_ecm_response(therms_yr=500.0)
        monkeypatch.setattr(
            analysis_module, "_get_client", lambda client=None: _FakeClient()
        )
        monkeypatch.setattr(
            _ii, "_extract_json_from_response", lambda _: dict(fake_output)
        )

        result = analysis_module.analyze_building("test-bldg", [_rich_snap()])
        # 500 therms × $0.80 = $400
        assert result["totals"]["total"]["cost_usd_yr"] == pytest.approx(400.0)

    def test_regulatory_impact_defaults_to_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ai_bms_pipeline.image_ingest as _ii

        fake_output = _flat_ecm_response()
        monkeypatch.setattr(
            analysis_module, "_get_client", lambda client=None: _FakeClient()
        )
        monkeypatch.setattr(
            _ii, "_extract_json_from_response", lambda _: dict(fake_output)
        )

        result = analysis_module.analyze_building("test-bldg", [_rich_snap()])
        assert result.get("regulatory_impact") == []


class _FakeClient:
    class messages:
        @staticmethod
        def create(**kwargs):
            return object()


# ─── scripts/analyze_buildings.py ────────────────────────────────────────────


def _load_analyze_buildings_script():
    script_path = (
        Path(__file__).resolve().parent.parent / "scripts" / "analyze_buildings.py"
    )
    spec = importlib.util.spec_from_file_location(
        "analyze_buildings_script", script_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAnalyzeBuildingsScript:
    def _write_snap(self, building_dir: Path, snap: dict) -> None:
        building_dir.mkdir(parents=True, exist_ok=True)
        fname = snap["timestamp"].replace(":", "_") + ".JSON"
        (building_dir / fname).write_text(json.dumps(snap))

    def test_dry_run_writes_findings_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _load_analyze_buildings_script()
        in_dir = tmp_path / "extracted"
        out_dir = tmp_path / "analyses"
        building_dir = in_dir / "bldg-1"
        snap = {
            "building_id": "bldg-1",
            "timestamp": "2026-03-10T10:00:00+00:00",
            "conditions": {"oat_f": 55, "rh_pct": None, "season": "heating"},
            "heating_plant": {
                "hws_temp_actual_f": 160,
                "hws_temp_setpoint_f": None,
                "hws_oat_reset_active": None,
                "vav_heat_request_pct": None,
                "boilers": None,
            },
            "cooling_plant": {"chws_temp_actual_f": None, "units": None},
            "air_systems": [],
            "zones": [],
        }
        self._write_snap(building_dir, snap)

        written = script.run(in_dir, out_dir, dry_run=True)

        assert len(written) == 1
        doc = json.loads(written[0].read_text())
        assert doc["dry_run"] is True
        assert doc["building_id"] == "bldg-1"
        assert "deterministic_findings" in doc

    def test_returns_empty_when_no_buildings(self, tmp_path: Path) -> None:
        script = _load_analyze_buildings_script()
        in_dir = tmp_path / "empty_extracted"
        in_dir.mkdir()
        out_dir = tmp_path / "analyses"

        result = script.run(in_dir, out_dir)
        assert result == []

    def test_building_id_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _load_analyze_buildings_script()
        in_dir = tmp_path / "extracted"
        out_dir = tmp_path / "analyses"

        for bid in ("bldg-1", "bldg-2"):
            building_dir = in_dir / bid
            snap = {
                "building_id": bid,
                "timestamp": "2026-03-10T10:00:00+00:00",
                "conditions": {"oat_f": 55, "rh_pct": None, "season": None},
                "heating_plant": {
                    "hws_temp_actual_f": 160,
                    "hws_temp_setpoint_f": None,
                    "hws_oat_reset_active": None,
                    "vav_heat_request_pct": None,
                    "boilers": None,
                },
                "cooling_plant": {"chws_temp_actual_f": None, "units": None},
                "air_systems": [],
                "zones": [],
            }
            self._write_snap(building_dir, snap)

        written = script.run(in_dir, out_dir, building_ids=["bldg-1"], dry_run=True)
        written_names = {p.stem for p in written}
        assert "bldg-1" in written_names
        assert "bldg-2" not in written_names

    def test_safe_filename_sanitizes_special_chars(self) -> None:
        script = _load_analyze_buildings_script()
        assert script._safe_filename("bldg/1:test") == "bldg_1_test"
        assert script._safe_filename("normal-id_123") == "normal-id_123"
