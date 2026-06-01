"""One-Side ORB strategy identity constants."""

STRATEGY_ID = "one_side_orb"
STRATEGY_NAME = "One-Side ORB"
STRATEGY_VERSION = "1.0.0"
STRATEGY_CATEGORY = "momentum"
STRATEGY_DESCRIPTION = (
    "Trades Opening Range Breakouts on stocks that showed a confirmed one-side "
    "directional day (OSD). Uses historical continuation probability to filter "
    "candidates: only stocks above the probability threshold are traded. "
    "Entry on any candle close breaking the first 15-minute candle range. "
    "Stop loss is the opposite side of the opening range. EOD exit at 3:15 PM IST."
)
