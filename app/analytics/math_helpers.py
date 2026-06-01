"""
Pure mathematical helpers for performance attribution.

All functions are stateless, synchronous, and dependency-free.
They operate on plain Python scalars and lists — no Pydantic, no Beanie.

Used by every attribution engine to compute standard performance metrics
from sequences of trade P&L values.
"""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Optional


# ── Core performance metrics ──────────────────────────────────────────────────

def win_rate(wins: int, total: int) -> float:
    """Fraction of trades that were profitable [0.0, 1.0]."""
    if total <= 0:
        return 0.0
    return round(wins / total, 6)


def expectancy(avg_win: float, avg_loss: float, win_rt: float) -> float:
    """
    Expected value per trade (₹).

    expectancy = avg_win × win_rate − |avg_loss| × (1 − win_rate)

    Positive expectancy means the strategy has an edge.
    """
    if win_rt < 0.0 or win_rt > 1.0:
        raise ValueError(f"win_rate must be in [0, 1], got {win_rt}")
    return round(avg_win * win_rt - abs(avg_loss) * (1.0 - win_rt), 4)


def profit_factor(total_wins: float, total_losses: float) -> float:
    """
    Ratio of gross profit to gross loss (absolute values).

    profit_factor > 1.0  →  strategy is profitable overall.
    Returns 0.0 when there are no wins; inf when there are no losses.
    """
    abs_losses = abs(total_losses)
    if abs_losses == 0:
        return round(float("inf") if total_wins > 0 else 0.0, 4)
    return round(abs(total_wins) / abs_losses, 4)


def sharpe_ratio(
    daily_pnls: list[float],
    risk_free_annual_pct: float = 0.0,
) -> float:
    """
    Annualised Sharpe ratio from a sequence of daily P&L values (₹).

    Uses a 252-trading-day year.  Returns 0.0 when fewer than 2 data points.

    Note: the risk-free rate is expressed as an annual percentage (e.g. 7.0
    for 7 % p.a.).  The function converts it to a daily ₹-equivalent for the
    series being analysed — which gives the right ratio direction even if
    series is not normalised by capital.  When using raw ₹ P&L rather than
    returns, this is often set to 0.
    """
    n = len(daily_pnls)
    if n < 2:
        return 0.0
    mean_daily = statistics.mean(daily_pnls)
    std_daily = statistics.stdev(daily_pnls)
    if std_daily == 0:
        return 0.0
    # Adjust for risk-free: divide annual rate by 252
    rf_daily = risk_free_annual_pct / 100.0 / 252.0
    # When daily_pnls are ₹ (not %-returns), rf_daily is effectively 0 unless
    # the caller normalises; default 0 is correct.
    excess = mean_daily - rf_daily
    return round((excess / std_daily) * math.sqrt(252), 4)


def max_drawdown(cumulative_pnls: list[float]) -> tuple[float, float]:
    """
    Maximum peak-to-trough drawdown.

    Returns ``(absolute_drawdown_₹, percent_of_peak)``.

    ``cumulative_pnls`` is a chronological series of running-total P&L (₹).
    Starting value is typically 0 (no prior profit) or the initial equity.
    """
    if not cumulative_pnls:
        return 0.0, 0.0
    peak = cumulative_pnls[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for pnl in cumulative_pnls:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd_abs:
            max_dd_abs = dd
            max_dd_pct = (dd / abs(peak) * 100.0) if peak != 0 else 0.0
    return round(max_dd_abs, 4), round(max_dd_pct, 4)


def calmar_ratio(
    annual_return_pct: float, max_drawdown_abs: float, total_capital: float
) -> float:
    """
    Calmar ratio: annual_return_pct / max_drawdown_pct_of_capital.

    Returns 0.0 when max_drawdown or capital is zero.
    """
    if total_capital == 0 or max_drawdown_abs == 0:
        return 0.0
    dd_pct = max_drawdown_abs / total_capital * 100.0
    if dd_pct == 0:
        return 0.0
    return round(annual_return_pct / dd_pct, 4)


def volatility_annual(daily_pnls: list[float]) -> float:
    """
    Annualised standard deviation of daily P&L.

    Returns 0.0 when fewer than 2 data points.
    """
    if len(daily_pnls) < 2:
        return 0.0
    return round(statistics.stdev(daily_pnls) * math.sqrt(252), 4)


# ── Time-series helpers ───────────────────────────────────────────────────────

def daily_pnl_series(
    trade_dates: list[date],
    trade_pnls: list[float],
) -> dict[date, float]:
    """
    Aggregate per-trade P&L into a daily series.

    ``trade_dates`` and ``trade_pnls`` must be the same length.
    Returns a dict keyed by trading date with the summed P&L for that day.
    """
    if len(trade_dates) != len(trade_pnls):
        raise ValueError("trade_dates and trade_pnls must have equal length")
    result: dict[date, float] = {}
    for d, pnl in zip(trade_dates, trade_pnls):
        result[d] = round(result.get(d, 0.0) + pnl, 4)
    return dict(sorted(result.items()))


def cumulative_pnl_series(daily_series: dict[date, float]) -> list[float]:
    """
    Convert a daily P&L dict into a chronological cumulative P&L list.

    Useful for feeding ``max_drawdown()``.
    """
    cumulative = 0.0
    result = []
    for d in sorted(daily_series):
        cumulative += daily_series[d]
        result.append(round(cumulative, 4))
    return result


def rolling_sharpe(
    daily_series: dict[date, float],
    window: int = 20,
) -> dict[date, float]:
    """
    Rolling Sharpe ratio over a sliding ``window``-day window.

    Returns a dict of date → Sharpe for each day that has at least
    ``window`` prior data points.
    """
    sorted_dates = sorted(daily_series)
    pnls = [daily_series[d] for d in sorted_dates]
    result: dict[date, float] = {}
    for i in range(window, len(pnls) + 1):
        window_pnls = pnls[i - window : i]
        result[sorted_dates[i - 1]] = sharpe_ratio(window_pnls)
    return result


# ── Trade-list helpers ────────────────────────────────────────────────────────

def avg_win_avg_loss(pnls: list[float]) -> tuple[float, float]:
    """
    Return ``(avg_win, avg_loss)`` from a list of trade P&Ls.

    avg_loss is expressed as a negative number (e.g. -500.0).
    Returns (0.0, 0.0) when the input is empty.
    """
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_w = round(statistics.mean(wins), 4) if wins else 0.0
    avg_l = round(statistics.mean(losses), 4) if losses else 0.0
    return avg_w, avg_l


def r_multiple(
    entry_price: float,
    exit_price: float,
    stop_loss: float,
    side: str,
) -> Optional[float]:
    """
    Risk-reward multiple expressed as an R-multiple.

    R  =  (exit_price − entry_price) / risk_per_share  for LONG
       =  (entry_price − exit_price) / risk_per_share  for SHORT

    where risk_per_share = |entry_price − stop_loss|.

    Returns None when risk_per_share is zero.
    """
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return None
    if side.upper() in ("LONG", "BUY"):
        return round((exit_price - entry_price) / risk, 4)
    return round((entry_price - exit_price) / risk, 4)


def contribution_pct(amount: float, total: float) -> float:
    """
    Percentage contribution of ``amount`` to ``total``.

    Returns 0.0 when total is zero.
    """
    if total == 0:
        return 0.0
    return round(amount / total * 100.0, 4)


def consistency_score(win_rt: float, trade_count: int) -> float:
    """
    Composite consistency score: win_rate × log2(trade_count + 1).

    Rewards both high win rates AND sufficient sample size.
    Range: [0, ∞) — higher is better.
    """
    if trade_count <= 0:
        return 0.0
    return round(win_rt * math.log2(trade_count + 1), 4)
