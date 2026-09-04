FCFF_REFERENCE_TERMINAL_GROWTH = 0.025
FCFF_REFERENCE_PROJECTION_YEARS = 4


def _to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_two_stage_fcff_per_share(
    latest_fcff,
    growth_rate_pct,
    discount_rate,
    shares_outstanding,
    total_debt=0.0,
    fx_rate=1.0,
    projection_years=FCFF_REFERENCE_PROJECTION_YEARS,
    terminal_growth_rate=FCFF_REFERENCE_TERMINAL_GROWTH,
):
    latest_fcff = _to_float(latest_fcff)
    growth_rate_pct = _to_float(growth_rate_pct)
    discount_rate = _to_float(discount_rate)
    shares_outstanding = _to_float(shares_outstanding)
    total_debt = _to_float(total_debt) or 0.0
    fx_rate = _to_float(fx_rate) or 1.0

    if latest_fcff is None or growth_rate_pct is None or discount_rate is None:
        return None
    if shares_outstanding is None or shares_outstanding <= 0 or fx_rate <= 0:
        return None
    if discount_rate <= 0:
        return None
    if discount_rate <= terminal_growth_rate:
        return None

    fcff = latest_fcff
    growth_rate = growth_rate_pct / 100.0
    present_value = 0.0

    for year in range(1, projection_years + 1):
        fcff = fcff * (1 + growth_rate)
        present_value += fcff / ((1 + discount_rate) ** year)

    terminal_fcff = fcff * (1 + terminal_growth_rate)
    terminal_value = terminal_fcff / (discount_rate - terminal_growth_rate)
    enterprise_value = present_value + (terminal_value / ((1 + discount_rate) ** projection_years))
    equity_value = enterprise_value - total_debt

    return (equity_value * fx_rate) / shares_outstanding


def build_fcff_reference_benchmark(
    latest_fcff,
    total_debt,
    shares_outstanding,
    fx_rate,
    discount_rate,
    scenario_growth_rates,
    legacy_values=None,
):
    legacy_values = legacy_values or {}
    benchmark = {
        "available": False,
        "projection_years": FCFF_REFERENCE_PROJECTION_YEARS,
        "terminal_growth_rate_pct": FCFF_REFERENCE_TERMINAL_GROWTH * 100,
        "latest_fcff": _to_float(latest_fcff),
        "total_debt": _to_float(total_debt) or 0.0,
        "shares_outstanding": _to_float(shares_outstanding),
        "fx_rate": _to_float(fx_rate) or 1.0,
        "discount_rate_pct": (_to_float(discount_rate) or 0.0) * 100,
        "scenarios": {},
        "assumptions": [
            "Projects the latest stored free cash flow directly for 4 years.",
            "Uses the selected discount rate and a 2.5% perpetual terminal growth rate.",
            "Subtracts latest stored debt from enterprise value before converting to per-share value.",
        ],
        "limitations": [
            "Stored data does not currently include a cash-and-equivalents bridge, so the equity bridge is debt-only.",
            "This benchmark reuses the app's scenario growth inputs, which may still be too aggressive for mature perpetual growth.",
        ],
    }

    for scenario, growth_rate in (scenario_growth_rates or {}).items():
        fcff_value = calculate_two_stage_fcff_per_share(
            latest_fcff=latest_fcff,
            growth_rate_pct=growth_rate,
            discount_rate=discount_rate,
            shares_outstanding=shares_outstanding,
            total_debt=total_debt,
            fx_rate=fx_rate,
        )
        legacy_value = _to_float((legacy_values or {}).get(scenario))
        benchmark["scenarios"][scenario] = {
            "growth_rate_pct": _to_float(growth_rate),
            "fcff_value": fcff_value,
            "legacy_value": legacy_value,
        }

    benchmark["available"] = any(
        data.get("fcff_value") is not None for data in benchmark["scenarios"].values()
    )
    return benchmark


def get_methodology_gap_notes():
    return [
        "Current app model starts from revenue, margin, and FCFE-style ratios rather than projecting free cash flow directly.",
        "The FCFF benchmark starts from the latest stored free cash flow and values enterprise cash generation before debt.",
        "Both views currently share the same scenario-growth inputs, so the remaining gap mostly reflects the cash-flow construction.",
    ]
