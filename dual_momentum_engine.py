"""Dual-momentum ETF switcher and backtester for the Portfolio Builder.

Signals are evaluated on weekly Friday closes using adjusted prices. At each
signal date the strategy selects the stronger of Junior BeES and Gold BeES by
an equal-weighted 6M/12M total-return score, then holds it only when its score
exceeds Liquid BeES' score; otherwise it holds Liquid BeES.
"""
from __future__ import annotations
import math
from typing import Any
import pandas as pd

TICKERS = {"JUNIORBEES.NS": "JuniorBeES", "GOLDBEES.NS": "GoldBeES", "LIQUIDBEES.NS": "LiquidBeES"}


def _num(v):
    try:
        x = float(v)
        return round(x, 6) if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _cagr(values):
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0: return None
    years = (len(values) - 1) / 52.0
    return ((values[-1] / values[0]) ** (1 / years) - 1) * 100 if years > 0 else None


def _max_drawdown(values):
    peak, worst = -float("inf"), 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0: worst = min(worst, (v / peak - 1) * 100)
    return worst


def run_dual_momentum(prices: pd.DataFrame) -> dict[str, Any]:
    """Run the weekly engine on a DataFrame with ticker columns and adjusted closes."""
    prices = prices.copy().sort_index().ffill().dropna(how="all")
    missing = [t for t in TICKERS if t not in prices.columns]
    if missing: raise ValueError("Missing adjusted close columns: " + ", ".join(missing))
    weekly = prices[list(TICKERS)].resample("W-FRI").last().ffill()
    # 126/252 trading-day windows are approximated as 26/52 weekly observations.
    r6 = weekly / weekly.shift(26) - 1
    r12 = weekly / weekly.shift(52) - 1
    scores = (r6 + r12) / 2
    first = scores.dropna().index.min()
    if pd.isna(first): raise ValueError("At least 52 weeks of adjusted prices are required.")
    weekly, scores = weekly.loc[first:], scores.loc[first:]
    rows, active_prev = [], None
    strat_growth, bench_growth = [1.0], [1.0]
    strategy_returns, junior_returns = [], []
    active_history = []
    for i, date in enumerate(weekly.index):
        s = scores.loc[date]
        j, g, c = (_num(s.get(t) * 100) for t in TICKERS)
        if j is None or g is None or c is None: continue
        leader = "JUNIORBEES.NS" if j >= g else "GOLDBEES.NS"
        chosen = leader if _num(s[leader] * 100) > c else "LIQUIDBEES.NS"
        active = TICKERS[chosen]
        switched = active_prev is not None and active != active_prev
        rows.append({"date": date.strftime("%Y-%m-%d"), "previous_asset": active_prev,
                     "active_asset": active, "switched": switched, "junior_score": j,
                     "gold_score": g, "cash_score": c})
        active_history.append(active)
        if i > 0:
            prior = weekly.index[i - 1]
            prev_active = active_history[-2]
            prev_ticker = next(t for t, label in TICKERS.items() if label == prev_active)
            ret = float(weekly.loc[date, prev_ticker] / weekly.loc[prior, prev_ticker] - 1)
            jr = float(weekly.loc[date, "JUNIORBEES.NS"] / weekly.loc[prior, "JUNIORBEES.NS"] - 1)
            strategy_returns.append(ret); junior_returns.append(jr)
            strat_growth.append(strat_growth[-1] * (1 + ret)); bench_growth.append(bench_growth[-1] * (1 + jr))
        active_prev = active
    if not rows: raise ValueError("No valid weekly momentum signals were produced.")
    switches = [r for r in rows if r["switched"]]
    last_date = pd.Timestamp(rows[-1]["date"])
    past_year = [r for r in switches if pd.Timestamp(r["date"]) >= last_date - pd.Timedelta(days=365)]
    sd = pd.Series(strategy_returns, dtype=float)
    sharpe = (sd.mean() / sd.std(ddof=1) * math.sqrt(52)) if len(sd) > 1 and sd.std(ddof=1) else None
    return {
        "as_of_date": rows[-1]["date"], "active_holding": rows[-1]["active_asset"],
        "scores": {"junior_score": rows[-1]["junior_score"], "gold_score": rows[-1]["gold_score"], "cash_score": rows[-1]["cash_score"]},
        "switches": switches, "past_year_switches": [{"date": r["date"], "from": r["previous_asset"], "to": r["active_asset"],
            "junior_score": r["junior_score"], "gold_score": r["gold_score"]} for r in past_year],
        "history": rows,
        "metrics": {"cagr": _cagr(strat_growth), "benchmark_cagr": _cagr(bench_growth),
            "trailing_year_return": round((math.prod(1 + x for x in strategy_returns[-52:]) - 1) * 100, 4) if strategy_returns else None,
            "benchmark_trailing_year_return": round((math.prod(1 + x for x in junior_returns[-52:]) - 1) * 100, 4) if junior_returns else None,
            "max_drawdown": _max_drawdown(strat_growth), "sharpe_ratio": _num(sharpe),
            "total_switch_count": len(switches), "trades_per_year": round(len(switches) / max((len(rows)-1)/52, 1/52), 3)},
    }


def get_portfolio_momentum_state(period: str = "5y") -> dict[str, Any]:
    """Download adjusted closes from Yahoo Finance and return current state + backtest."""
    import yfinance as yf
    raw = yf.download(list(TICKERS), period=period, auto_adjust=True, progress=False, group_by="column", threads=False)
    if raw is None or raw.empty: raise RuntimeError("Yahoo Finance returned no ETF price history.")
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0): prices = raw["Close"].copy()
        elif "Close" in raw.columns.get_level_values(-1): prices = raw.xs("Close", axis=1, level=-1).copy()
        else: prices = raw.copy()
    else: prices = raw.copy()
    prices = prices.rename(columns={c: str(c) for c in prices.columns})
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    return run_dual_momentum(prices)
