"""Historical valuation: replay the ledger against historical prices.

Nothing needs to be recorded as it happens. Quantities come from the trades and
prices come from the market's own history, so any past date can be valued after
the fact - including dates before the tool was ever run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .model import Portfolio
from .prices import to_major

# Yahoo back-adjusts closes for splits and dividends. For "what was this worth on
# the day", the raw close is what pairs with the share count in the ledger.
AUTO_ADJUST = False


@dataclass
class Point:
    on: str              # YYYY-MM-DD
    value: float         # market value in the base currency
    invested: float      # cumulative cost of every fill up to `on`, same currency

    @property
    def gain(self) -> float:
        return self.value - self.invested


def month_ends(start: str, end: str) -> list[str]:
    """Last day of each month between `start` and `end`, plus `end` itself."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    out: list[date] = []
    cursor = first
    while cursor <= last:
        nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = nxt - timedelta(days=1)
        if first <= month_end <= last:
            out.append(month_end)
        cursor = nxt
    if not out or out[-1] != last:
        out.append(last)
    return [d.isoformat() for d in out]


def _closes(symbol: str, start: str, end: str) -> dict[str, float]:
    """Daily closes for one symbol, keyed by YYYY-MM-DD, in the major unit."""
    import yfinance as yf

    # A week of lead-in so the first sample still finds a prior close.
    pad = (date.fromisoformat(start) - timedelta(days=10)).isoformat()
    stop = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    try:
        ticker = yf.Ticker(symbol)
        frame = ticker.history(start=pad, end=stop, auto_adjust=AUTO_ADJUST)
    except Exception as exc:
        print(f"  ! {symbol}: {exc}")
        return {}
    if frame.empty:
        print(f"  ! {symbol}: no price history")
        return {}

    # LSE closes come back in pence while the trade is booked in pounds; without
    # this the position is valued a hundred times over.
    try:
        quoted = (getattr(ticker, "fast_info", {}) or {}).get("currency") or ""
    except Exception:
        quoted = ""
    return {
        idx.date().isoformat(): to_major(float(close), quoted)[0]
        for idx, close in frame["Close"].dropna().items()
    }


def _on_or_before(series: dict[str, float], when: str) -> float | None:
    """Markets close at weekends; take the most recent close up to `when`."""
    candidates = [d for d in series if d <= when]
    return series[max(candidates)] if candidates else None


def build(pf: Portfolio, base: str, dates: list[str]) -> list[Point]:
    """Value the portfolio on each date in `dates`."""
    if not pf.trades:
        raise SystemExit("A trade ledger is needed to value past dates.")

    tickers = sorted({h.ticker for h in pf.instruments if not h.is_cash})
    # The window has to reach back to the first trade, not just the first sample,
    # or fills before it find no FX rate and vanish from the invested total.
    window_start = min([*dates, *(t.date for t in pf.trades)])
    window_end = max(dates)

    print(f"Fetching history for {len(tickers)} instrument(s)...")
    closes = {t: _closes(t, window_start, window_end) for t in tickers}

    # Every currency the instruments or the fills are denominated in.
    currencies: set[str] = set()
    for trade in pf.trades:
        for fill in trade.fills:
            if fill.currency:
                currencies.add(fill.currency.upper())
    quote_ccy = _quote_currencies(pf, tickers)
    currencies |= set(quote_ccy.values())
    currencies.discard(base.upper())

    fx: dict[str, dict[str, float]] = {}
    for ccy in sorted(currencies):
        series = _closes(f"{ccy}{base.upper()}=X", window_start, window_end)
        if not series:
            inverse = _closes(f"{base.upper()}{ccy}=X", window_start, window_end)
            series = {d: 1 / v for d, v in inverse.items() if v}
        fx[ccy] = series

    def rate(ccy: str, when: str) -> float | None:
        if not ccy or ccy == base.upper():
            return 1.0
        return _on_or_before(fx.get(ccy, {}), when)

    points: list[Point] = []
    for when in dates:
        value = 0.0
        missing = []
        for holding in pf.holdings_as_of(when):
            if not holding.quantity:
                continue
            if holding.is_cash:
                value += holding.quantity
                continue
            price = _on_or_before(closes.get(holding.ticker, {}), when)
            fx_rate = rate(quote_ccy.get(holding.ticker, base), when)
            if price is None or fx_rate is None:
                missing.append(holding.ticker)
                continue
            value += holding.quantity * price * fx_rate

        invested = 0.0
        for trade in pf.trades:
            if trade.date > when:
                continue
            for fill in trade.fills:
                fx_rate = rate(fill.currency.upper(), trade.date)
                if fx_rate is None:
                    continue
                # Converted at the rate on the day it was paid, not today's.
                invested += fill.cost * fx_rate

        if missing:
            print(f"  ! {when}: no price for {', '.join(sorted(set(missing)))}")
        points.append(Point(on=when, value=value, invested=invested))
    return points


def _quote_currencies(pf: Portfolio, tickers: list[str]) -> dict[str, str]:
    """Each instrument's trading currency, taken from the fills that bought it."""
    out: dict[str, str] = {}
    for trade in pf.trades or []:
        for fill in trade.fills:
            if fill.currency:
                out.setdefault(fill.ticker, fill.currency.upper())
    return {t: out.get(t, pf.base_currency) for t in tickers}
