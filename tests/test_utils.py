"""Tests for ai_bms_pipeline.utils — all deterministic, no LLM."""

from __future__ import annotations

import pytest

from ai_bms_pipeline.utils import (
    DEFAULT_FACTORS,
    aggregate_totals,
    ecm_savings_block,
    electricity_carbon,
    electricity_cost,
    gas_carbon,
    gas_cost,
    simple_payback,
    steam_carbon,
    steam_cost,
)

# ─── Per-commodity arithmetic ─────────────────────────────────────────────────


class TestPerCommodityArithmetic:
    def test_electricity_cost(self):
        assert electricity_cost(1000, 0.12) == pytest.approx(120.0)

    def test_electricity_cost_default_rate(self):
        assert electricity_cost(1000) == pytest.approx(120.0)

    def test_electricity_carbon(self):
        assert electricity_carbon(1000, 0.000386) == pytest.approx(0.386, rel=1e-3)

    def test_gas_cost(self):
        assert gas_cost(100, 0.80) == pytest.approx(80.0)

    def test_gas_cost_default_rate(self):
        assert gas_cost(100) == pytest.approx(80.0)

    def test_gas_carbon(self):
        # gas_carbon rounds to 4 decimal places: 100 * 0.00005311 = 0.005311 → 0.0053
        assert gas_carbon(100, 0.00005311) == pytest.approx(0.0053, abs=1e-5)

    def test_steam_cost(self):
        assert steam_cost(10, 32.0) == pytest.approx(320.0)

    def test_steam_carbon(self):
        assert steam_carbon(10, 0.04493) == pytest.approx(0.4493, rel=1e-3)


# ─── simple_payback ───────────────────────────────────────────────────────────


class TestSimplePayback:
    def test_normal_case(self):
        assert simple_payback(10000, 2000) == pytest.approx(5.0)

    def test_rounds_to_one_decimal(self):
        assert simple_payback(10000, 3000) == pytest.approx(3.3, abs=0.05)

    def test_zero_savings_returns_none(self):
        assert simple_payback(10000, 0) is None

    def test_negative_savings_returns_none(self):
        assert simple_payback(10000, -500) is None


# ─── ecm_savings_block ────────────────────────────────────────────────────────


class TestEcmSavingsBlock:
    def test_electricity_only_populates_electricity_and_total(self):
        block = ecm_savings_block(kwh_yr=1000, factors=DEFAULT_FACTORS)
        assert "electricity" in block
        assert block["electricity"]["kwh_yr"] == pytest.approx(1000)
        assert block["electricity"]["cost_usd_yr"] == pytest.approx(120.0)
        assert "gas" not in block
        assert "steam" not in block
        assert block["total"]["cost_usd_yr"] == pytest.approx(120.0)

    def test_gas_only(self):
        block = ecm_savings_block(therms_yr=500, factors=DEFAULT_FACTORS)
        assert "gas" in block
        assert block["gas"]["therms_yr"] == pytest.approx(500)
        assert block["gas"]["cost_usd_yr"] == pytest.approx(400.0)
        assert "electricity" not in block
        assert block["total"]["cost_usd_yr"] == pytest.approx(400.0)

    def test_steam_only(self):
        block = ecm_savings_block(mlb_yr=5, factors=DEFAULT_FACTORS)
        assert "steam" in block
        assert block["steam"]["mlb_yr"] == pytest.approx(5)
        assert block["steam"]["cost_usd_yr"] == pytest.approx(160.0)
        assert block["total"]["cost_usd_yr"] == pytest.approx(160.0)

    def test_multi_commodity_totals_sum(self):
        block = ecm_savings_block(kwh_yr=1000, therms_yr=200, factors=DEFAULT_FACTORS)
        expected_total = 120.0 + 160.0  # 1000*0.12 + 200*0.80
        assert block["total"]["cost_usd_yr"] == pytest.approx(expected_total)

    def test_all_null_produces_total_only(self):
        block = ecm_savings_block(kwh_yr=None, therms_yr=None, mlb_yr=None)
        assert "electricity" not in block
        assert "gas" not in block
        assert "steam" not in block
        assert block["total"]["cost_usd_yr"] == pytest.approx(0.0)
        assert block["total"]["co2e_tons_yr"] == pytest.approx(0.0)

    def test_zero_quantity_omitted(self):
        block = ecm_savings_block(kwh_yr=0, therms_yr=500)
        assert "electricity" not in block
        assert "gas" in block

    def test_kwh_yr_range_stored(self):
        block = ecm_savings_block(kwh_yr=1000, kwh_yr_range=(800, 1200))
        assert block["electricity"]["kwh_yr_range"] == [800, 1200]

    def test_no_range_is_none(self):
        block = ecm_savings_block(kwh_yr=1000)
        assert block["electricity"]["kwh_yr_range"] is None

    def test_co2e_included(self):
        block = ecm_savings_block(kwh_yr=1000, factors=DEFAULT_FACTORS)
        assert block["electricity"]["co2e_tons_yr"] > 0
        assert block["total"]["co2e_tons_yr"] > 0


# ─── aggregate_totals ─────────────────────────────────────────────────────────


def _make_ecm(
    *,
    kwh: float | None = None,
    therms: float | None = None,
    mlb: float | None = None,
    capital: float | None = None,
    category: str = "scheduling",
) -> dict:
    """Build a post-expansion ECM (nested savings + implementation blocks)."""
    return {
        "category": category,
        "savings": ecm_savings_block(kwh_yr=kwh, therms_yr=therms, mlb_yr=mlb),
        "implementation": {"capital_cost_usd": capital},
    }


class TestAggregateTotals:
    def test_empty_list(self):
        totals = aggregate_totals([], DEFAULT_FACTORS)
        assert totals["total"]["cost_usd_yr"] == pytest.approx(0.0)
        assert totals["total_capital_usd"] is None
        assert totals["aggregate_payback_years"] is None

    def test_single_electricity_ecm(self):
        totals = aggregate_totals([_make_ecm(kwh=1000)], DEFAULT_FACTORS)
        assert totals["electricity"]["kwh_yr"] == pytest.approx(1000)
        assert totals["total"]["cost_usd_yr"] == pytest.approx(120.0)

    def test_multiple_ecms_sums_correctly(self):
        ecms = [_make_ecm(kwh=1000), _make_ecm(kwh=2000)]
        totals = aggregate_totals(ecms, DEFAULT_FACTORS)
        assert totals["electricity"]["kwh_yr"] == pytest.approx(3000)
        assert totals["total"]["cost_usd_yr"] == pytest.approx(360.0)

    def test_category_counts(self):
        ecms = [
            _make_ecm(kwh=1000, category="scheduling"),
            _make_ecm(kwh=500, category="scheduling"),
            _make_ecm(kwh=800, category="setpoint_reset"),
        ]
        totals = aggregate_totals(ecms, DEFAULT_FACTORS)
        counts = totals["ecm_count_by_category"]
        assert counts["scheduling"] == 2
        assert counts["setpoint_reset"] == 1
        assert counts["controls_sequence"] == 0

    def test_unknown_category_bucketed_as_other(self):
        ecm = _make_ecm(category="made_up_category")
        totals = aggregate_totals([ecm], DEFAULT_FACTORS)
        assert totals["ecm_count_by_category"]["other"] >= 1

    def test_capital_and_payback_computed(self):
        ecm = _make_ecm(kwh=1000, capital=12000)
        totals = aggregate_totals([ecm], DEFAULT_FACTORS)
        assert totals["total_capital_usd"] == pytest.approx(12000.0)
        # payback = 12000 / 120 = 100 years
        assert totals["aggregate_payback_years"] == pytest.approx(100.0)

    def test_no_capital_means_none_payback(self):
        ecm = _make_ecm(kwh=1000, capital=None)
        totals = aggregate_totals([ecm], DEFAULT_FACTORS)
        assert totals["total_capital_usd"] is None
        assert totals["aggregate_payback_years"] is None

    def test_flat_format_ecm_also_works(self):
        """aggregate_totals should accept flat (pre-expansion) ECMs too."""
        flat_ecm = {"category": "scheduling", "kwh_yr": 500, "therms_yr": None}
        totals = aggregate_totals([flat_ecm], DEFAULT_FACTORS)
        assert totals["total"]["cost_usd_yr"] == pytest.approx(60.0)

    def test_multi_commodity_across_ecms(self):
        ecms = [_make_ecm(kwh=1000), _make_ecm(therms=200)]
        totals = aggregate_totals(ecms, DEFAULT_FACTORS)
        assert "electricity" in totals
        assert "gas" in totals
        assert totals["total"]["cost_usd_yr"] == pytest.approx(120.0 + 160.0)
