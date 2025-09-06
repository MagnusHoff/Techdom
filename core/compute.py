# compute.py
import math


def monthly_payment(principal, annual_rate_pct, n_years):
    if principal <= 0:
        return 0.0
    r = (annual_rate_pct / 100.0) / 12.0
    n = int(n_years * 12)
    return (
        principal / n if r == 0 else principal * r * (1 + r) ** n / ((1 + r) ** n - 1)
    )


def compute_metrics(
    price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs
):
    loan = max(price - equity, 0)
    m_payment = monthly_payment(loan, interest, term_years)
    maint = rent * (maint_pct / 100.0)
    vacancy = rent * (vacancy_pct / 100.0)
    total_monthly_costs = m_payment + hoa + maint + vacancy + other_costs
    cashflow = rent - total_monthly_costs
    noi_month = rent - (hoa + maint + vacancy + other_costs)
    noi_year = noi_month * 12
    invested_equity = equity if equity > 0 else 1
    annual_rate = interest / 100.0
    approx_interest_year = loan * annual_rate
    principal_reduction_year = max(m_payment * 12 - approx_interest_year, 0)
    total_equity_return_pct = (
        ((cashflow * 12) + principal_reduction_year) / invested_equity * 100.0
    )
    factor = 1.0 - (maint_pct / 100.0) - (vacancy_pct / 100.0)
    break_even = (
        (m_payment + hoa + other_costs) / factor if factor > 0 else float("inf")
    )
    return {
        "loan": loan,
        "m_payment": m_payment,
        "maint": maint,
        "vacancy": vacancy,
        "total_costs": total_monthly_costs,
        "cashflow": cashflow,
        "noi_year": noi_year,
        "break_even": break_even,
        "principal_reduction_year": principal_reduction_year,
        "total_equity_return_pct": total_equity_return_pct,
        "legacy_net_yield_pct": (noi_year / invested_equity) * 100.0,
    }
