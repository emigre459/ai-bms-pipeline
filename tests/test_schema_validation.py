"""Tests for config.validate_against_schema — deterministic, no LLM."""

from pathlib import Path

import pytest

from ai_bms_pipeline.config import validate_against_schema

CONF = Path(__file__).resolve().parents[1] / "conf"
ANALYSIS_SCHEMA = CONF / "analysis-output.schema.yaml"
BMS_SCHEMA = CONF / "bms-snapshot.schema.yaml"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _minimal_analysis(overrides: dict | None = None) -> dict:
    """Smallest valid analysis output (all required fields populated)."""
    doc = {
        "building_id": "test-building",
        "analysis_date": "2026-03-12",
        "analyst_notes": None,
        "factors": {
            "electricity": {"rate_usd_kwh": 0.12, "co2e_tons_per_kwh": 0.000288},
            "sources": ["eGRID 2023"],
        },
        "ecms": [],
        "totals": {
            "total": {
                "cost_usd_yr": 0.0,
                "co2e_tons_yr": 0.0,
                "pct_cost_of_baseline": 0.0,
                "pct_co2e_of_baseline": 0.0,
            },
            "total_capital_usd": None,
            "aggregate_payback_years": None,
            "ecm_count_by_category": {
                "scheduling": 0,
                "setpoint_reset": 0,
                "controls_sequence": 0,
                "recommissioning": 0,
                "equipment_upgrade": 0,
                "other": 0,
            },
        },
        "key_findings": ["Finding one"],
        "priority_actions": ["Action one"],
        "open_questions": [],
    }
    if overrides:
        doc.update(overrides)
    return doc


def _minimal_ecm(overrides: dict | None = None) -> dict:
    """Minimal valid ECM entry — electricity savings block is optional, so omit it."""
    ecm = {
        "id": "ECM-01",
        "name": "Static Pressure Reset",
        "category": "setpoint_reset",
        "description": "Reset static pressure setpoint based on VAV demand.",
        "affected_systems": ["AHU-1"],
        "assumptions": ["Fan cube law applies"],
        "savings": {
            "total": {"cost_usd_yr": 600.0, "co2e_tons_yr": 1.44},
        },
        "implementation": {
            "capital_cost_usd": None,
            "requires_capital": False,
            "complexity": "low",
            "payback_years": None,
            "notes": None,
        },
        "priority": "high",
        "confidence": "medium",
    }
    if overrides:
        ecm.update(overrides)
    return ecm


# ─── Analysis output schema tests ────────────────────────────────────────────


class TestAnalysisSchema:
    def test_minimal_valid_passes(self):
        violations = validate_against_schema(_minimal_analysis(), ANALYSIS_SCHEMA)
        assert violations == [], violations

    def test_with_valid_ecm_passes(self):
        doc = _minimal_analysis({"ecms": [_minimal_ecm()]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert violations == [], violations

    def test_missing_required_top_level_field(self):
        doc = _minimal_analysis()
        del doc["building_id"]
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any(
            "building_id" in v and "missing" in v for v in violations
        ), violations

    def test_wrong_type_building_id(self):
        doc = _minimal_analysis({"building_id": 12345})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("building_id" in v for v in violations), violations

    def test_analyst_notes_null_is_valid(self):
        doc = _minimal_analysis({"analyst_notes": None})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert violations == [], violations

    def test_analyst_notes_string_is_valid(self):
        doc = _minimal_analysis({"analyst_notes": "Some notes here."})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert violations == [], violations

    def test_analyst_notes_wrong_type(self):
        doc = _minimal_analysis({"analyst_notes": 42})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("analyst_notes" in v for v in violations), violations

    def test_key_findings_must_be_array(self):
        doc = _minimal_analysis({"key_findings": "not an array"})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("key_findings" in v for v in violations), violations

    def test_ecm_invalid_category_enum(self):
        ecm = _minimal_ecm({"category": "made_up_category"})
        doc = _minimal_analysis({"ecms": [ecm]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("category" in v for v in violations), violations

    def test_ecm_valid_categories(self):
        valid_categories = [
            "scheduling",
            "setpoint_reset",
            "controls_sequence",
            "recommissioning",
            "equipment_upgrade",
            "other",
        ]
        for cat in valid_categories:
            ecm = _minimal_ecm({"category": cat})
            doc = _minimal_analysis({"ecms": [ecm]})
            violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
            cat_violations = [v for v in violations if "category" in v]
            assert (
                cat_violations == []
            ), f"category={cat!r} wrongly rejected: {cat_violations}"

    def test_ecm_invalid_priority_enum(self):
        ecm = _minimal_ecm({"priority": "critical"})
        doc = _minimal_analysis({"ecms": [ecm]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("priority" in v for v in violations), violations

    def test_ecm_invalid_confidence_enum(self):
        ecm = _minimal_ecm({"confidence": "very_high"})
        doc = _minimal_analysis({"ecms": [ecm]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("confidence" in v for v in violations), violations

    def test_ecm_missing_required_field(self):
        ecm = _minimal_ecm()
        del ecm["name"]
        doc = _minimal_analysis({"ecms": [ecm]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("name" in v and "missing" in v for v in violations), violations

    def test_ecm_savings_wrong_type(self):
        ecm = _minimal_ecm()
        ecm["savings"]["electricity"] = {
            "kwh_yr": "five thousand",
            "kwh_yr_range": None,
            "cost_usd_yr": 0.0,
            "co2e_tons_yr": 0.0,
        }
        doc = _minimal_analysis({"ecms": [ecm]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("kwh_yr" in v for v in violations), violations

    def test_multiple_ecms_second_has_violation(self):
        good = _minimal_ecm({"id": "ECM-01"})
        bad = _minimal_ecm({"id": "ECM-02", "priority": "URGENT"})
        doc = _minimal_analysis({"ecms": [good, bad]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("priority" in v for v in violations), violations
        # First ECM should not be flagged
        assert not any("ecms[0]" in v and "priority" in v for v in violations)

    def test_boolean_not_accepted_as_number(self):
        ecm = _minimal_ecm()
        ecm["savings"]["electricity"] = {
            "kwh_yr": True,
            "kwh_yr_range": None,
            "cost_usd_yr": 0.0,
            "co2e_tons_yr": 0.0,
        }
        doc = _minimal_analysis({"ecms": [ecm]})
        violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
        assert any("kwh_yr" in v for v in violations), violations

    def test_real_analysis_file_passes(self):
        """Smoke-test actual pipeline output files.

        Tests all files; normalises legacy ``regulatory_impact: null`` to ``[]``
        so that files produced before the null→[] fix don't count as failures.
        """
        import json

        analyses_dir = Path(__file__).resolve().parents[1] / "data" / "analyses"
        files = sorted(analyses_dir.glob("*.json"))
        if not files:
            pytest.skip("No analysis files found in data/analyses/")

        all_violations: dict[str, list[str]] = {}
        for path in files:
            doc = json.loads(path.read_text())
            # Normalise legacy null → [] so old files don't trigger a false failure
            if doc.get("regulatory_impact") is None:
                doc["regulatory_impact"] = []
            violations = validate_against_schema(doc, ANALYSIS_SCHEMA)
            if violations:
                all_violations[path.name] = violations

        assert (
            not all_violations
        ), f"{len(all_violations)} file(s) have schema violations:\n" + "\n".join(
            f"  {name}:\n" + "\n".join(f"    {v}" for v in vs)
            for name, vs in all_violations.items()
        )


# ─── BMS snapshot schema tests ───────────────────────────────────────────────


class TestBmsSnapshotSchema:
    def _minimal_snapshot(self) -> dict:
        """Only building_id and timestamp are required; all sub-objects are optional."""
        return {
            "building_id": "bldg-1",
            "timestamp": "2024-09-15T08:00:00-07:00",
        }

    def test_minimal_snapshot_passes(self):
        violations = validate_against_schema(self._minimal_snapshot(), BMS_SCHEMA)
        assert violations == [], violations

    def test_invalid_season_enum(self):
        snap = self._minimal_snapshot()
        snap["conditions"] = {"oat_f": 65.0, "rh_pct": 50.0, "season": "spring"}
        violations = validate_against_schema(snap, BMS_SCHEMA)
        assert any("season" in v for v in violations), violations

    def test_valid_seasons(self):
        for season in ("heating", "cooling", "shoulder"):
            snap = {
                **self._minimal_snapshot(),
                "conditions": {"oat_f": 65.0, "rh_pct": 50.0, "season": season},
            }
            violations = validate_against_schema(snap, BMS_SCHEMA)
            season_violations = [v for v in violations if "season" in v]
            assert season_violations == [], f"season={season!r} wrongly rejected"

    def test_missing_building_id(self):
        snap = self._minimal_snapshot()
        del snap["building_id"]
        violations = validate_against_schema(snap, BMS_SCHEMA)
        assert any(
            "building_id" in v and "missing" in v for v in violations
        ), violations
