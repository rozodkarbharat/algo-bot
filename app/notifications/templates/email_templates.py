"""
HTML email template builders.

All functions return a tuple of (subject, plain_text, html_body).
The plain_text fallback is shown by email clients that can't render HTML.

Design rule: templates accept primitive values only — no model imports.
"""

from typing import Optional


_BRAND_COLOR = "#1a73e8"
_SUCCESS_COLOR = "#1e8e3e"
_WARNING_COLOR = "#e37400"
_DANGER_COLOR = "#d32f2f"
_BG_COLOR = "#f8f9fa"
_CARD_COLOR = "#ffffff"


def _base_html(title: str, content: str, color: str = _BRAND_COLOR) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{_BG_COLOR};font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{_BG_COLOR};padding:24px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:{_CARD_COLOR};border-radius:8px;
                      border-top:4px solid {color};box-shadow:0 2px 8px rgba(0,0,0,.08);">
          <tr>
            <td style="padding:24px 32px;">
              <p style="margin:0 0 4px 0;font-size:11px;font-weight:700;
                        letter-spacing:1px;text-transform:uppercase;color:#80868b;">
                TradingBot
              </p>
              <h1 style="margin:0 0 20px 0;font-size:22px;color:#202124;">{title}</h1>
              {content}
              <hr style="border:none;border-top:1px solid #e8eaed;margin:24px 0;">
              <p style="margin:0;font-size:12px;color:#80868b;">
                This is an automated alert from TradingBot. Do not reply.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _row(label: str, value: str, bold: bool = False) -> str:
    weight = "700" if bold else "400"
    return (
        f'<tr><td style="padding:6px 0;font-size:14px;color:#5f6368;width:160px;">'
        f'{label}</td>'
        f'<td style="padding:6px 0;font-size:14px;font-weight:{weight};color:#202124;">'
        f'{value}</td></tr>'
    )


def _table(rows: list[tuple[str, str, bool]]) -> str:
    inner = "".join(_row(label, val, bold) for label, val, bold in rows)
    return f'<table cellpadding="0" cellspacing="0" width="100%">{inner}</table>'


# ── Signal ──────────────────────────────────────────────────────────────────

def signal_generated(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    probability: Optional[float] = None,
    trading_date: str = "",
) -> tuple[str, str, str]:
    subject = f"[TradingBot] Signal: {side} {symbol}"
    risk = abs(entry_price - stop_loss)
    rows = [
        ("Symbol", symbol, True),
        ("Direction", side, True),
        ("Entry Price", f"₹{entry_price:.2f}", True),
        ("Stop Loss", f"₹{stop_loss:.2f}", False),
        ("Risk per Share", f"₹{risk:.2f}", False),
    ]
    if probability is not None:
        rows.append(("Probability", f"{probability * 100:.1f}%", False))
    if trading_date:
        rows.append(("Trading Date", trading_date, False))
    content = _table(rows)  # type: ignore[arg-type]
    plain = (
        f"Signal: {side} {symbol}\n"
        f"Entry: ₹{entry_price:.2f} | SL: ₹{stop_loss:.2f} | Risk: ₹{risk:.2f}"
    )
    return subject, plain, _base_html(f"Signal: {side} {symbol}", content, _BRAND_COLOR)


# ── Trade events ────────────────────────────────────────────────────────────

def trade_entered(
    mode: str,
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    quantity: int,
    capital_used: float,
    trading_date: str = "",
) -> tuple[str, str, str]:
    subject = f"[TradingBot] {mode} Trade Entered: {symbol}"
    rows = [
        ("Mode", mode, False),
        ("Symbol", symbol, True),
        ("Side", side, True),
        ("Entry Price", f"₹{entry_price:.2f}", True),
        ("Stop Loss", f"₹{stop_loss:.2f}", False),
        ("Quantity", str(quantity), False),
        ("Capital Used", f"₹{capital_used:,.0f}", False),
    ]
    if trading_date:
        rows.append(("Date", trading_date, False))
    content = _table(rows)  # type: ignore[arg-type]
    plain = (
        f"{mode} Trade Entered: {side} {symbol}\n"
        f"Entry: ₹{entry_price:.2f} | SL: ₹{stop_loss:.2f} | Qty: {quantity}"
    )
    return subject, plain, _base_html(f"{mode} Trade Entered: {symbol}", content, _SUCCESS_COLOR)


def trade_exited(
    mode: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    pnl: float,
    exit_reason: str,
    trading_date: str = "",
) -> tuple[str, str, str]:
    subject = f"[TradingBot] {mode} Trade Closed: {symbol} ₹{pnl:+.2f}"
    color = _SUCCESS_COLOR if pnl >= 0 else _DANGER_COLOR
    rows = [
        ("Mode", mode, False),
        ("Symbol", symbol, True),
        ("Side", side, False),
        ("Entry Price", f"₹{entry_price:.2f}", False),
        ("Exit Price", f"₹{exit_price:.2f}", False),
        ("Quantity", str(quantity), False),
        ("Net P&L", f"₹{pnl:+.2f}", True),
        ("Exit Reason", exit_reason, False),
    ]
    if trading_date:
        rows.append(("Date", trading_date, False))
    content = _table(rows)  # type: ignore[arg-type]
    plain = (
        f"{mode} Trade Closed: {symbol} | P&L: ₹{pnl:+.2f} | Reason: {exit_reason}"
    )
    return subject, plain, _base_html(f"Trade Closed: {symbol}", content, color)


def stop_loss_hit(
    mode: str,
    symbol: str,
    side: str,
    stop_loss: float,
    pnl: float,
) -> tuple[str, str, str]:
    subject = f"[TradingBot] Stop Loss Hit: {symbol}"
    rows = [
        ("Mode", mode, False),
        ("Symbol", symbol, True),
        ("Side", side, False),
        ("SL Price", f"₹{stop_loss:.2f}", True),
        ("P&L", f"₹{pnl:+.2f}", True),
    ]
    content = _table(rows)  # type: ignore[arg-type]
    plain = f"Stop Loss Hit: {symbol} | SL: ₹{stop_loss:.2f} | P&L: ₹{pnl:+.2f}"
    return subject, plain, _base_html(f"Stop Loss Hit: {symbol}", content, _WARNING_COLOR)


# ── System alerts ───────────────────────────────────────────────────────────

def system_error(component: str, error: str, detail: str = "") -> tuple[str, str, str]:
    subject = f"[TradingBot] CRITICAL: System Error in {component}"
    detail_row = [("Detail", detail[:500], False)] if detail else []
    rows = [("Component", component, True), ("Error", error[:500], False)] + detail_row  # type: ignore[operator]
    content = _table(rows)  # type: ignore[arg-type]
    plain = f"System Error in {component}: {error}"
    return subject, plain, _base_html(f"System Error: {component}", content, _DANGER_COLOR)


def broker_disconnected(broker: str, reason: str) -> tuple[str, str, str]:
    subject = f"[TradingBot] CRITICAL: Broker Disconnected — {broker}"
    rows = [("Broker", broker, True), ("Reason", reason, False)]
    content = _table(rows)  # type: ignore[arg-type]
    plain = f"Broker Disconnected: {broker}. Reason: {reason}"
    return subject, plain, _base_html(f"Broker Disconnected: {broker}", content, _DANGER_COLOR)


def scheduler_failure(job_id: str, error: str) -> tuple[str, str, str]:
    subject = f"[TradingBot] Scheduler Job Failed: {job_id}"
    rows = [("Job ID", job_id, True), ("Error", error[:500], False)]
    content = _table(rows)  # type: ignore[arg-type]
    plain = f"Scheduler Job Failed: {job_id}. Error: {error}"
    return subject, plain, _base_html(f"Scheduler Failure: {job_id}", content, _WARNING_COLOR)


def generic_message(
    title: str, body: str, severity: str = "info"
) -> tuple[str, str, str]:
    color = {
        "info": _BRAND_COLOR,
        "warning": _WARNING_COLOR,
        "critical": _DANGER_COLOR,
    }.get(severity.lower(), _BRAND_COLOR)
    subject = f"[TradingBot] {title}"
    content = f'<p style="font-size:15px;color:#202124;line-height:1.6;">{body}</p>'
    return subject, body, _base_html(title, content, color)


# ── Daily summary ───────────────────────────────────────────────────────────

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
) -> tuple[str, str, str]:
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = realized_pnl + unrealized_pnl
    subject = f"[TradingBot] Daily Summary ({mode}) — {trading_date}"
    color = _SUCCESS_COLOR if total_pnl >= 0 else _DANGER_COLOR

    rows: list[tuple[str, str, bool]] = [
        ("Date", trading_date, False),
        ("Mode", mode, False),
        ("Signals Generated", str(total_signals), False),
        ("Total Trades", str(total_trades), False),
        ("Winners / Losers", f"{winning_trades} / {losing_trades}", False),
        ("Win Rate", f"{win_rate:.1f}%", True),
        ("Realized P&L", f"₹{realized_pnl:+,.2f}", True),
        ("Unrealized P&L", f"₹{unrealized_pnl:+,.2f}", False),
        ("Total P&L", f"₹{total_pnl:+,.2f}", True),
    ]
    if top_stock and top_stock_pnl is not None:
        rows.append(("Best Stock", f"{top_stock} (₹{top_stock_pnl:+.2f})", False))
    if worst_stock and worst_stock_pnl is not None:
        rows.append(("Worst Stock", f"{worst_stock} (₹{worst_stock_pnl:+.2f})", False))

    content = _table(rows)
    plain = (
        f"Daily Summary ({mode}) — {trading_date}\n"
        f"Trades: {total_trades} | Win Rate: {win_rate:.1f}%\n"
        f"P&L: ₹{total_pnl:+,.2f} (Realized: ₹{realized_pnl:+,.2f})"
    )
    return subject, plain, _base_html(f"Daily Summary — {trading_date}", content, color)


# ── Incident alert ─────────────────────────────────────────────────────────────

def incident_alert(
    incident_id: str,
    component: str,
    severity: str,
    title: str,
    description: str,
    status: str,
) -> tuple[str, str, str]:
    """Format an incident lifecycle notification email."""
    color_map = {"info": _BRAND_COLOR, "warning": _WARNING_COLOR, "critical": _DANGER_COLOR}
    color = color_map.get(severity.lower(), _WARNING_COLOR)
    subject = f"[{severity.upper()}] Incident: {title}"

    status_color = {"open": "#d32f2f", "acknowledged": "#e37400", "investigating": "#e37400",
                    "resolved": "#1e8e3e"}.get(status.lower(), "#5f6368")
    rows = [
        ("ID", incident_id, False),
        ("Component", component, True),
        ("Severity", severity.upper(), True),
        ("Description", description, False),
        ("Status", f'<span style="color:{status_color};font-weight:700;">{status.upper()}</span>', False),
    ]
    content = _table(rows)
    plain = f"Incident [{severity.upper()}]: {title}\nComponent: {component}\n{description}\nStatus: {status.upper()}\nID: {incident_id}"
    return subject, plain, _base_html(title, content, color)


# ── EOD exit ──────────────────────────────────────────────────────────────────

def eod_exit(
    mode: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    pnl: float,
    trading_date: str = "",
) -> tuple[str, str, str]:
    pnl_color = _SUCCESS_COLOR if pnl >= 0 else _DANGER_COLOR
    subject = f"EOD Exit: {symbol} | P&L ₹{pnl:+.2f}"
    rows = [
        ("Mode", mode, False),
        ("Symbol", symbol, True),
        ("Side", side.upper(), False),
        ("Entry", f"₹{entry_price:.2f}", False),
        ("Exit", f"₹{exit_price:.2f}", False),
        ("Quantity", str(quantity), False),
        ("P&L", f'<span style="color:{pnl_color};font-weight:700;">₹{pnl:+.2f}</span>', True),
        ("Reason", "EOD Force Exit", False),
    ]
    if trading_date:
        rows.insert(0, ("Date", trading_date, False))
    content = _table(rows)
    plain = f"EOD Exit: {symbol} | {side} | Entry ₹{entry_price:.2f} | Exit ₹{exit_price:.2f} | P&L ₹{pnl:+.2f}"
    return subject, plain, _base_html(f"EOD Exit: {symbol}", content)


# ── Reconciliation mismatch ───────────────────────────────────────────────────

def reconciliation_mismatch(
    broker: str,
    mismatch_count: int,
    description: str,
) -> tuple[str, str, str]:
    subject = f"Reconciliation Mismatch — {broker} ({mismatch_count} discrepancies)"
    rows = [
        ("Broker", broker, True),
        ("Mismatches", str(mismatch_count), True),
        ("Detail", description, False),
        ("Action", "Verify positions manually", True),
    ]
    content = _table(rows)
    plain = f"Reconciliation Mismatch — {broker}\n{mismatch_count} discrepancies\n{description}"
    return subject, plain, _base_html(f"Reconciliation Mismatch — {broker}", content, _WARNING_COLOR)


# ── Database unavailable ──────────────────────────────────────────────────────

def database_unavailable(error: str) -> tuple[str, str, str]:
    subject = "CRITICAL: Database Unavailable"
    content = _table([
        ("Status", '<span style="color:#d32f2f;font-weight:700;">UNAVAILABLE</span>', True),
        ("Error", error[:300], False),
        ("Impact", "All trading operations suspended", True),
    ])
    plain = f"CRITICAL: MongoDB is unavailable.\n{error}"
    return subject, plain, _base_html("Database Unavailable", content, _DANGER_COLOR)
