MODEL_FAMILIES = [
    {
        "key": "fcff_perpetual",
        "name": "FCFF Perpetual Growth",
        "status": "active_baseline",
        "summary": "Reference model for most non-financial operating companies.",
        "primary_for": "Mature operating companies with usable free cash flow and stable capital structure.",
    },
    {
        "key": "fcfe_perpetual",
        "name": "FCFE Perpetual Growth",
        "status": "roadmap",
        "summary": "Equity-holder focused perpetual model for companies where leverage matters.",
        "primary_for": "Operating companies where equity cash flow is the preferred lens.",
    },
    {
        "key": "ddm",
        "name": "Dividend Discount Model",
        "status": "roadmap",
        "summary": "Best fit for companies with stable, predictable dividend policies.",
        "primary_for": "Dividend-paying businesses with durable payout behavior.",
    },
    {
        "key": "residual_income",
        "name": "Residual Income Model",
        "status": "roadmap",
        "summary": "Fallback for businesses where cash flow is temporarily weak or dividends are absent.",
        "primary_for": "Profitable businesses with weak cash flow conversion or no dividends.",
    },
    {
        "key": "reit_nav_affo_ffo",
        "name": "NAV / AFFO / FFO for REITs",
        "status": "roadmap",
        "summary": "Sector-specific track for real-estate vehicles that do not fit standard operating-company models.",
        "primary_for": "REITs and property-heavy income vehicles.",
    },
]


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_company_profile(stock_row, latest_financial_row):
    sector = ((stock_row or {}).get("sector") or "").strip()
    industry = ((stock_row or {}).get("industry") or "").strip()
    latest_fcf = (latest_financial_row or {}).get("free_cash_flow")
    latest_net_income = (latest_financial_row or {}).get("net_income")

    sector_lower = sector.lower()
    industry_lower = industry.lower()

    is_reit = "reit" in industry_lower or "real estate" in sector_lower
    is_financial = sector_lower == "financial services" or any(
        token in industry_lower for token in ("bank", "insurance", "capital markets", "asset management")
    )

    return {
        "sector": sector,
        "industry": industry,
        "is_reit": is_reit,
        "is_financial": is_financial,
        "has_negative_cash_flow": (_safe_float(latest_fcf) or 0.0) < 0,
        "has_negative_net_income": (_safe_float(latest_net_income) or 0.0) < 0,
    }


def get_screening_rules():
    return [
        {
            "label": "FCFF default",
            "rule": "Use as the baseline for non-financial operating companies with usable free cash flow and a sensible WACC.",
        },
        {
            "label": "DDM candidate",
            "rule": "Only use after dividend-history data is added and payout stability can be verified.",
        },
        {
            "label": "Residual income fallback",
            "rule": "Use when dividends are absent and cash flow is weak, but book value and earnings remain meaningful.",
        },
        {
            "label": "REIT branch",
            "rule": "Route REITs to NAV / AFFO / FFO metrics instead of standard operating-company models.",
        },
        {
            "label": "Financial-sector caution",
            "rule": "Banks and insurers should not default to the FCFF baseline without sector-specific adjustments.",
        },
    ]


def get_model_framework(company_profile):
    profile = company_profile or {}
    is_reit = profile.get("is_reit")
    is_financial = profile.get("is_financial")
    has_negative_cash_flow = profile.get("has_negative_cash_flow")
    has_negative_net_income = profile.get("has_negative_net_income")

    framework = []
    for model in MODEL_FAMILIES:
        item = dict(model)
        key = item["key"]

        if key == "fcff_perpetual":
            if is_reit:
                item["fit"] = "secondary"
                item["reason"] = "REITs are better handled with NAV / AFFO / FFO."
            elif is_financial:
                item["fit"] = "caution"
                item["reason"] = "Financial-sector balance sheets need a separate playbook."
            elif has_negative_cash_flow:
                item["fit"] = "watch"
                item["reason"] = "Negative free cash flow makes the FCFF baseline less reliable."
            else:
                item["fit"] = "primary"
                item["reason"] = "Best current benchmark for a standard operating company."
        elif key == "fcfe_perpetual":
            item["fit"] = "secondary" if not is_reit else "watch"
            item["reason"] = "Next step after the FCFF baseline is locked down."
        elif key == "ddm":
            item["fit"] = "needs_data"
            item["reason"] = "Dividend-history screening is not stored in the app yet."
        elif key == "residual_income":
            item["fit"] = "secondary" if (has_negative_cash_flow or has_negative_net_income) and not is_reit else "watch"
            item["reason"] = (
                "Useful fallback when cash flow is weak but accounting earnings and book value still matter."
                if item["fit"] == "secondary"
                else "Keep on the roadmap until classification and accounting inputs are formalized."
            )
        elif key == "reit_nav_affo_ffo":
            item["fit"] = "primary" if is_reit else "watch"
            item["reason"] = "Primary valuation path for REITs." if is_reit else "Only relevant after REIT detection triggers it."

        framework.append(item)

    return framework
