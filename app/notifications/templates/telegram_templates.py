"""
Telegram message template builders.

All functions return a UTF-8 string formatted with Telegram MarkdownV2.
Special characters that must be escaped in MarkdownV2 are handled by
_esc().

Design rule: templates accept primitive values only — no model imports.
This keeps the template layer independent of the rest of the app so it
can be unit-tested without a database.
"""

from typing import Any, Optional


# Characters that must be escaped in Telegram MarkdownV2
_ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"


def _esc(text: str) -> str:
    """Escape special MarkdownV2 characters in a literal string."""
    for ch in _ESCAPE_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


def _severity_emoji(severity: str) -> str:
    return {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(severity.lower(), "📢")


def _side_emoji(side: str) -> str:
    return "🟢" if side.upper() in ("LONG", "BUY") else "🔴"


# ── Signal templates ──────────────────────────────────────────────────────────

def signal_generated(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    probability: Optional[float] = None,
    orb_range_pct: Optional[float] = None,
) -> str:
    emoji = _side_emoji(side)
    prob_line = (
        f"📊 Probability: *{_esc(f'{probability * 100:.1f}%')}*\n"
        if probability is not None
        else ""
    )
    orb_line = (
        f"📏 ORB Range: *{_esc(f'{orb_range_pct:.2f}%')}*\n"
        if orb_range_pct is not None
        else ""
    )
    risk = abs(entry_price - stop_loss)
    return (
        f"{emoji} *SIGNAL: {_esc(side)} {_esc(symbol)}*\n\n"
        f"💰 Entry: *₹{_esc(f'{entry_price:.2f}')}*\n"
        f"🛑 Stop Loss: *₹{_esc(f'{stop_loss:.2f}')}*\n"
        f"⚡ Risk: *₹{_esc(f'{risk:.2f}')}*\n"
        f"{prob_line}"
        f"{orb_line}"
    )


# ── Trade entry templates ─────────────────────────────────────────────────────

def trade_entered(
    mode: str,  # "Paper" | "Live"
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    quantity: int,
    capital_used: float,
) -> str:
    emoji = _side_emoji(side)
    return (
        f"{emoji} *{_esc(mode)} Trade Entered: {_esc(symbol)}*\n\n"
        f"📋 Side: *{_esc(side)}*\n"
        f"💰 Entry: *₹{_esc(f'{entry_price:.2f}')}*\n"
        f"📦 Qty: *{_esc(str(quantity))}*\n"
        f"🛑 Stop Loss: *₹{_esc(f'{stop_loss:.2f}')}*\n"
        f"💼 Capital: *₹{_esc(f'{capital_used:,.0f}')}*\n"
    )


# ── Trade exit templates ──────────────────────────────────────────────────────

def trade_exited(
    mode: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    pnl: float,
    exit_reason: str,
) -> str:
    pnl_emoji = "✅" if pnl >= 0 else "❌"
    return (
        f"{pnl_emoji} *{_esc(mode)} Trade Closed: {_esc(symbol)}*\n\n"
        f"📋 Side: *{_esc(side)}*\n"
        f"💰 Entry: *₹{_esc(f'{entry_price:.2f}')}*\n"
        f"🏁 Exit: *₹{_esc(f'{exit_price:.2f}')}*\n"
        f"📦 Qty: *{_esc(str(quantity))}*\n"
        f"💵 Net P&L: *₹{_esc(f'{pnl:+.2f}')}*\n"
        f"🏷️ Reason: *{_esc(exit_reason)}*\n"
    )


def stop_loss_hit(
    mode: str,
    symbol: str,
    side: str,
    stop_loss: float,
    pnl: float,
) -> str:
    return (
        f"🛑 *Stop Loss Hit: {_esc(symbol)}*\n\n"
        f"📋 Mode: *{_esc(mode)}*\n"
        f"📋 Side: *{_esc(side)}*\n"
        f"💰 SL Price: *₹{_esc(f'{stop_loss:.2f}')}*\n"
        f"💵 P&L: *₹{_esc(f'{pnl:+.2f}')}*\n"
    )


# ── System alert templates ─────────────────────────────────────────────────────

def broker_disconnected(broker: str, reason: str) -> str:
    return (
        f"🔌 *Broker Disconnected: {_esc(broker)}*\n\n"
        f"❗ Reason: {_esc(reason)}\n\n"
        f"⚠️ All pending orders may be at risk\\. Check positions immediately\\."
    )


def websocket_disconnected(feed: str, reason: str) -> str:
    return (
        f"📡 *WebSocket Disconnected: {_esc(feed)}*\n\n"
        f"❗ Reason: {_esc(reason)}\n"
        f"⚠️ Live data feed interrupted\\."
    )


def scheduler_failure(job_id: str, error: str) -> str:
    return (
        f"⏰ *Scheduler Job Failed: {_esc(job_id)}*\n\n"
        f"❌ Error: {_esc(error[:500])}\n"
    )


def system_error(component: str, error: str, detail: str = "") -> str:
    detail_line = f"\n📎 Detail: {_esc(detail[:300])}" if detail else ""
    return (
        f"🚨 *System Error: {_esc(component)}*\n\n"
        f"❌ {_esc(error[:500])}"
        f"{detail_line}\n"
    )


def generic_message(title: str, body: str, severity: str = "info") -> str:
    emoji = _severity_emoji(severity)
    return f"{emoji} *{_esc(title)}*\n\n{_esc(body)}"


# ── Daily summary template ─────────────────────────────────────────────────────

def daily_summary(
    trading_date: str,
    total_signals: int,
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
    realized_pnl: float,
    unrealized_pnl: float,
    top_stock: Optional[str],
    top_stock_pnl: Optional[float],
    worst_stock: Optional[str],
    worst_stock_pnl: Optional[float],
    mode: str = "Paper",
) -> str:
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = realized_pnl + unrealized_pnl
    pnl_emoji = "✅" if total_pnl >= 0 else "❌"

    top_line = (
        f"🏆 Best: *{_esc(top_stock)}* ₹{_esc(f'{top_stock_pnl:+.2f}')}\n"
        if top_stock
        else ""
    )
    worst_line = (
        f"💀 Worst: *{_esc(worst_stock)}* ₹{_esc(f'{worst_stock_pnl:+.2f}')}\n"
        if worst_stock
        else ""
    )

    return (
        f"📊 *Daily Summary \\({_esc(mode)}\\) — {_esc(trading_date)}*\n\n"
        f"📡 Signals Generated: *{total_signals}*\n"
        f"📈 Total Trades: *{total_trades}*\n"
        f"✅ Winners: *{winning_trades}*  ❌ Losers: *{losing_trades}*\n"
        f"🎯 Win Rate: *{_esc(f'{win_rate:.1f}%')}*\n\n"
        f"{pnl_emoji} Realized P&L: *₹{_esc(f'{realized_pnl:+,.2f}')}*\n"
        f"📉 Unrealized P&L: *₹{_esc(f'{unrealized_pnl:+,.2f}')}*\n"
        f"💰 Total P&L: *₹{_esc(f'{total_pnl:+,.2f}')}*\n\n"
        f"{top_line}"
        f"{worst_line}"
    )


# ── Incident alert ─────────────────────────────────────────────────────────────

def incident_alert(
    incident_id: str,
    component: str,
    severity: str,
    title: str,
    description: str,
    status: str,
) -> str:
    """Format an incident lifecycle notification for Telegram."""
    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
        severity.lower(), "📢"
    )
    status_emoji = {
        "open": "🔴",
        "acknowledged": "🟡",
        "investigating": "🟡",
        "resolved": "🟢",
    }.get(status.lower(), "⚪")

    return (
        f"{severity_emoji} *INCIDENT \\[{_esc(severity.upper())}\\]*\n\n"
        f"🏷 *{_esc(title)}*\n\n"
        f"🔧 Component: `{_esc(component)}`\n"
        f"📋 Description: {_esc(description)}\n"
        f"{status_emoji} Status: *{_esc(status.upper())}*\n"
        f"🆔 ID: `{_esc(incident_id)}`\n"
    )


# ── EOD exit ──────────────────────────────────────────────────────────────────

def eod_exit(
    mode: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    pnl: float,
) -> str:
    """Format an end-of-day forced exit notification."""
    emoji = "✅" if pnl >= 0 else "❌"
    return (
        f"🔔 *{_esc(mode)} EOD Exit: {_esc(symbol)}*\n\n"
        f"{'🟢' if side.upper() in ('LONG','BUY') else '🔴'} Side: *{_esc(side)}*\n"
        f"💰 Entry: *₹{_esc(f'{entry_price:.2f}')}*\n"
        f"🚪 Exit: *₹{_esc(f'{exit_price:.2f}')}*\n"
        f"📦 Qty: *{quantity}*\n"
        f"{emoji} P&L: *₹{_esc(f'{pnl:+.2f}')}*\n"
        f"⏱ Reason: *EOD Force Exit*\n"
    )


# ── Reconciliation mismatch ───────────────────────────────────────────────────

def reconciliation_mismatch(
    broker: str,
    mismatch_count: int,
    description: str,
) -> str:
    return (
        f"⚠️ *Reconciliation Mismatch*\n\n"
        f"🏦 Broker: *{_esc(broker)}*\n"
        f"🔢 Mismatches: *{mismatch_count}*\n"
        f"📋 Detail: {_esc(description)}\n"
        f"⚡ Action required: verify positions manually\\.\n"
    )


# ── Database unavailable ──────────────────────────────────────────────────────

def database_unavailable(error: str) -> str:
    return (
        f"🚨 *DATABASE UNAVAILABLE*\n\n"
        f"💾 MongoDB is unreachable\\.\n"
        f"❌ Error: `{_esc(error[:200])}`\n"
        f"⚡ All trading operations suspended until DB recovers\\.\n"
    )

