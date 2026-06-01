"""
Opening Range Historical Validation — strategy identity and timing constants.

All times are stated in IST and their UTC equivalents (IST = UTC+05:30).
"""

# ── Identity ──────────────────────────────────────────────────────────────────

STRATEGY_ID = "opening_range_historical_validation"
STRATEGY_NAME = "Opening Range Historical Validation"
STRATEGY_VERSION = "1.0.0"
STRATEGY_CATEGORY = "intraday"
STRATEGY_DESCRIPTION = (
    "Detects stocks that show a two-sided breakout on Day D — where price both "
    "breaks above the initial high-side breakout level (CH1_High) AND below the "
    "initial low-side breakout level (CL1_Low) on the same day.  Then validates "
    "the setup against the last 30 historical occurrences of the same pattern "
    "(Phase 2).  On Day D+1, trades whichever side of the opening range breaks "
    "first (Phase 3), subject to a 1% range filter and 12:00 IST time cutoff."
)

# ── Opening Range ─────────────────────────────────────────────────────────────

# 9:15 IST = 03:45 UTC — open time of the first 15-min candle
ORB_OPEN_UTC_HOUR = 3
ORB_OPEN_UTC_MINUTE = 45

# 9:30 IST = 04:00 UTC — close time of the first 15-min candle / breakout window start
ORB_CLOSE_UTC_HOUR = 4
ORB_CLOSE_UTC_MINUTE = 0

# ── Phase 3 time filter ───────────────────────────────────────────────────────

# 12:00 IST = 06:30 UTC — latest candle OPEN time for entry
MAX_ENTRY_UTC_HOUR = 6
MAX_ENTRY_UTC_MINUTE = 30

# ── EOD exit ─────────────────────────────────────────────────────────────────

# 3:15 IST = 09:45 UTC — forced exit time
EOD_EXIT_UTC_HOUR = 9
EOD_EXIT_UTC_MINUTE = 45

# ── Phase 2 validation defaults ───────────────────────────────────────────────

DEFAULT_LOOKBACK_OCCURRENCES = 30       # number of prior setups to validate against
MIN_OCCURRENCES_REQUIRED = 5            # fewer than this → not tradable
QUALIFICATION_MIN_WINS = 21             # absolute wins required (of 30)
QUALIFICATION_MIN_WIN_RATE = 0.70       # win-rate threshold (70%)

# ── Phase 3 filters ───────────────────────────────────────────────────────────

DEFAULT_MAX_ORB_RANGE_PCT = 1.0        # D+1 first-candle range must be ≤ 1%

# ── Capital / cost defaults ───────────────────────────────────────────────────

DEFAULT_CAPITAL_PER_TRADE = 100_000.0
DEFAULT_SLIPPAGE_PCT = 0.05
DEFAULT_BROKERAGE_PER_SIDE = 20.0
