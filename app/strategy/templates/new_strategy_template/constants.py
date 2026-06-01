"""
New Strategy Template — constants.

INSTRUCTIONS:
  1. Replace all MY_STRATEGY_* values with your strategy's identity.
  2. strategy_id must be unique, lowercase, underscore-delimited.
     Examples: "gap_breakout", "opening_momentum", "mean_reversion"
  3. version follows semantic versioning: MAJOR.MINOR.PATCH
"""

STRATEGY_ID = "my_strategy"        # ← CHANGE THIS (unique id)
STRATEGY_NAME = "My Strategy"      # ← CHANGE THIS (human readable)
STRATEGY_VERSION = "1.0.0"
STRATEGY_CATEGORY = "momentum"     # momentum | mean_reversion | arbitrage | statistical
STRATEGY_DESCRIPTION = (
    "Brief description of what this strategy does, its core logic, "
    "entry/exit rules, and expected market conditions."
)
