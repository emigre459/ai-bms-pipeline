"""ECM savings arithmetic helpers.

Schema reference (analysis-output.schema.yaml):
  savings.electricity.cost_usd_yr  = kwh_yr × factors.electricity.rate_usd_kwh
  savings.electricity.co2e_tons_yr = kwh_yr × factors.electricity.co2e_tons_per_kwh
  savings.gas.cost_usd_yr          = therms_yr × factors.gas.rate_usd_therm
  savings.gas.co2e_tons_yr         = therms_yr × factors.gas.co2e_tons_per_therm
  savings.steam.cost_usd_yr        = mlb_yr × factors.steam.rate_usd_mlb
  savings.steam.co2e_tons_yr       = mlb_yr × factors.steam.co2e_tons_per_mlb
  implementation.payback_years     = capital_cost_usd / savings.total.cost_usd_yr
"""
from __future__ import annotations

# ─── Default US commercial energy factors ────────────────────────────────────
# Used when building-specific rates are unknown.

DEFAULT_ELECTRICITY_RATE_USD_KWH: float = 0.12       # EIA 2024 commercial avg
DEFAULT_ELECTRICITY_CO2E_TONS_PER_KWH: float = 0.000386  # eGRID 2023 US avg
DEFAULT_GAS_RATE_USD_THERM: float = 0.80             # EIA 2024 commercial avg
DEFAULT_GAS_CO2E_TONS_PER_THERM: float = 0.00005311  # EPA 40 CFR Part 98
DEFAULT_STEAM_RATE_USD_MLB: float = 32.00            # ~ConEd NYC district steam
DEFAULT_STEAM_CO2E_TONS_PER_MLB: float = 0.04493     # ConEd district steam estimate

DEFAULT_FACTORS: dict = {
    "electricity": {
        "rate_usd_kwh": DEFAULT_ELECTRICITY_RATE_USD_KWH,
        "co2e_tons_per_kwh": DEFAULT_ELECTRICITY_CO2E_TONS_PER_KWH,
    },
    "gas": {
        "rate_usd_therm": DEFAULT_GAS_RATE_USD_THERM,
        "co2e_tons_per_therm": DEFAULT_GAS_CO2E_TONS_PER_THERM,
    },
    "steam": {
        "rate_usd_mlb": DEFAULT_STEAM_RATE_USD_MLB,
        "co2e_tons_per_mlb": DEFAULT_STEAM_CO2E_TONS_PER_MLB,
    },
    "sources": [
        "EIA 2024 US commercial electricity average: $0.12/kWh",
        "eGRID 2023 US national average: 0.000386 tCO2e/kWh",
        "EIA 2024 US commercial natural gas average: $0.80/therm",
        "EPA 40 CFR Part 98: 0.00005311 tCO2e/therm",
        "ConEd NYC district steam estimate: $32.00/Mlb, 0.04493 tCO2e/Mlb",
    ],
}

# ─── Per-commodity helpers ────────────────────────────────────────────────────


def electricity_cost(
    kwh_yr: float,
    rate_usd_kwh: float = DEFAULT_ELECTRICITY_RATE_USD_KWH,
) -> float:
    """Annual electricity cost savings: kwh_yr × rate_usd_kwh."""
    return round(kwh_yr * rate_usd_kwh, 2)


def electricity_carbon(
    kwh_yr: float,
    co2e_tons_per_kwh: float = DEFAULT_ELECTRICITY_CO2E_TONS_PER_KWH,
) -> float:
    """Annual electricity carbon savings: kwh_yr × co2e_tons_per_kwh."""
    return round(kwh_yr * co2e_tons_per_kwh, 4)


def gas_cost(
    therms_yr: float,
    rate_usd_therm: float = DEFAULT_GAS_RATE_USD_THERM,
) -> float:
    """Annual gas cost savings: therms_yr × rate_usd_therm."""
    return round(therms_yr * rate_usd_therm, 2)


def gas_carbon(
    therms_yr: float,
    co2e_tons_per_therm: float = DEFAULT_GAS_CO2E_TONS_PER_THERM,
) -> float:
    """Annual gas carbon savings: therms_yr × co2e_tons_per_therm."""
    return round(therms_yr * co2e_tons_per_therm, 4)


def steam_cost(
    mlb_yr: float,
    rate_usd_mlb: float = DEFAULT_STEAM_RATE_USD_MLB,
) -> float:
    """Annual steam cost savings: mlb_yr × rate_usd_mlb."""
    return round(mlb_yr * rate_usd_mlb, 2)


def steam_carbon(
    mlb_yr: float,
    co2e_tons_per_mlb: float = DEFAULT_STEAM_CO2E_TONS_PER_MLB,
) -> float:
    """Annual steam carbon savings: mlb_yr × co2e_tons_per_mlb."""
    return round(mlb_yr * co2e_tons_per_mlb, 4)


def simple_payback(
    capital_cost_usd: float,
    annual_savings_usd: float,
) -> float | None:
    """Simple payback: capital_cost_usd / annual_savings_usd. Returns None if savings <= 0."""
    if not annual_savings_usd or annual_savings_usd <= 0:
        return None
    return round(capital_cost_usd / annual_savings_usd, 1)


# ─── ECM block builders ───────────────────────────────────────────────────────


def ecm_savings_block(
    *,
    kwh_yr: float | None = None,
    therms_yr: float | None = None,
    mlb_yr: float | None = None,
    kwh_yr_range: tuple[float, float] | None = None,
    therms_yr_range: tuple[float, float] | None = None,
    mlb_yr_range: tuple[float, float] | None = None,
    factors: dict | None = None,
) -> dict:
    """Build a fully-computed savings block for one ECM.

    All cost and carbon fields are derived from the energy quantities and
    the provided factors, so callers only need to supply kWh/therms/Mlb.
    """
    f = factors or DEFAULT_FACTORS
    el_f = f.get("electricity", {})
    gas_f = f.get("gas", {})
    stm_f = f.get("steam", {})

    result: dict = {}
    total_cost = 0.0
    total_co2e = 0.0

    if kwh_yr is not None and kwh_yr > 0:
        cost = electricity_cost(kwh_yr, el_f.get("rate_usd_kwh", DEFAULT_ELECTRICITY_RATE_USD_KWH))
        co2e = electricity_carbon(kwh_yr, el_f.get("co2e_tons_per_kwh", DEFAULT_ELECTRICITY_CO2E_TONS_PER_KWH))
        result["electricity"] = {
            "kwh_yr": round(kwh_yr, 1),
            "kwh_yr_range": list(kwh_yr_range) if kwh_yr_range else None,
            "cost_usd_yr": cost,
            "co2e_tons_yr": co2e,
        }
        total_cost += cost
        total_co2e += co2e

    if therms_yr is not None and therms_yr > 0:
        cost = gas_cost(therms_yr, gas_f.get("rate_usd_therm", DEFAULT_GAS_RATE_USD_THERM))
        co2e = gas_carbon(therms_yr, gas_f.get("co2e_tons_per_therm", DEFAULT_GAS_CO2E_TONS_PER_THERM))
        result["gas"] = {
            "therms_yr": round(therms_yr, 1),
            "therms_yr_range": list(therms_yr_range) if therms_yr_range else None,
            "cost_usd_yr": cost,
            "co2e_tons_yr": co2e,
        }
        total_cost += cost
        total_co2e += co2e

    if mlb_yr is not None and mlb_yr > 0:
        cost = steam_cost(mlb_yr, stm_f.get("rate_usd_mlb", DEFAULT_STEAM_RATE_USD_MLB))
        co2e = steam_carbon(mlb_yr, stm_f.get("co2e_tons_per_mlb", DEFAULT_STEAM_CO2E_TONS_PER_MLB))
        result["steam"] = {
            "mlb_yr": round(mlb_yr, 1),
            "mlb_yr_range": list(mlb_yr_range) if mlb_yr_range else None,
            "cost_usd_yr": cost,
            "co2e_tons_yr": co2e,
        }
        total_cost += cost
        total_co2e += co2e

    result["total"] = {
        "cost_usd_yr": round(total_cost, 2),
        "co2e_tons_yr": round(total_co2e, 4),
    }
    return result


def aggregate_totals(ecms: list[dict], factors: dict | None = None) -> dict:
    """Aggregate per-ECM savings into the analysis totals block."""
    f = factors or DEFAULT_FACTORS
    el_f = f.get("electricity", {})
    gas_f = f.get("gas", {})
    stm_f = f.get("steam", {})

    total_kwh = 0.0
    total_therms = 0.0
    total_mlb = 0.0
    total_capital = 0.0
    has_capital = False

    category_counts: dict[str, int] = {
        "scheduling": 0,
        "setpoint_reset": 0,
        "controls_sequence": 0,
        "recommissioning": 0,
        "equipment_upgrade": 0,
        "other": 0,
    }

    for ecm in ecms:
        savings = ecm.get("savings", {})
        total_kwh += (savings.get("electricity") or {}).get("kwh_yr", 0) or 0
        total_therms += (savings.get("gas") or {}).get("therms_yr", 0) or 0
        total_mlb += (savings.get("steam") or {}).get("mlb_yr", 0) or 0

        capital = (ecm.get("implementation") or {}).get("capital_cost_usd")
        if capital is not None:
            total_capital += capital
            has_capital = True

        cat = ecm.get("category", "other")
        if cat in category_counts:
            category_counts[cat] += 1
        else:
            category_counts["other"] += 1

    el_cost = electricity_cost(total_kwh, el_f.get("rate_usd_kwh", DEFAULT_ELECTRICITY_RATE_USD_KWH))
    el_co2e = electricity_carbon(total_kwh, el_f.get("co2e_tons_per_kwh", DEFAULT_ELECTRICITY_CO2E_TONS_PER_KWH))
    gas_cost_tot = gas_cost(total_therms, gas_f.get("rate_usd_therm", DEFAULT_GAS_RATE_USD_THERM))
    gas_co2e = gas_carbon(total_therms, gas_f.get("co2e_tons_per_therm", DEFAULT_GAS_CO2E_TONS_PER_THERM))
    stm_cost_tot = steam_cost(total_mlb, stm_f.get("rate_usd_mlb", DEFAULT_STEAM_RATE_USD_MLB))
    stm_co2e = steam_carbon(total_mlb, stm_f.get("co2e_tons_per_mlb", DEFAULT_STEAM_CO2E_TONS_PER_MLB))

    total_cost = round(el_cost + gas_cost_tot + stm_cost_tot, 2)
    total_co2e = round(el_co2e + gas_co2e + stm_co2e, 4)

    result: dict = {}

    if total_kwh > 0:
        result["electricity"] = {
            "kwh_yr": round(total_kwh, 1),
            "cost_usd_yr": el_cost,
            "co2e_tons_yr": el_co2e,
            "pct_of_baseline": 0.0,
        }
    if total_therms > 0:
        result["gas"] = {
            "therms_yr": round(total_therms, 1),
            "cost_usd_yr": gas_cost_tot,
            "co2e_tons_yr": gas_co2e,
            "pct_of_baseline": 0.0,
        }
    if total_mlb > 0:
        result["steam"] = {
            "mlb_yr": round(total_mlb, 1),
            "cost_usd_yr": stm_cost_tot,
            "co2e_tons_yr": stm_co2e,
            "pct_of_baseline": 0.0,
        }

    result["total"] = {
        "cost_usd_yr": total_cost,
        "co2e_tons_yr": total_co2e,
        "pct_cost_of_baseline": 0.0,
        "pct_co2e_of_baseline": 0.0,
    }
    result["total_capital_usd"] = round(total_capital, 2) if has_capital else None
    result["aggregate_payback_years"] = (
        simple_payback(total_capital, total_cost)
        if has_capital and total_cost > 0
        else None
    )
    result["ecm_count_by_category"] = category_counts
    return result
