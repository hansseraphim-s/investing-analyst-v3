"""iav3 — a backtest-first, risk-disciplined equity trading agent.

Design principles:
  * Deterministic strategy logic in the hot path (testable, fast, free).
  * Every strategy is validated on historical data before any capital is used.
  * Risk checks are pure functions with full unit-test coverage.
  * Paper trading is the default; live trading requires explicit opt-in.
  * No performance is promised. Backtest metrics describe the past, not the future.
"""

__version__ = "2.0.0"
