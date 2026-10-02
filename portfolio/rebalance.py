"""Valuation, drift against targets, and trade suggestions.

Targets are keyed by *sleeve*, not ticker. A sleeve is normally a single ticker,
but it can hold several - which is how a share-class switch works: the old ticker
stays in the portfolio marked `frozen` so it is never traded, while new money for
that sleeve goes to the replacement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Holding
from .prices import fx_rate, get_prices


@dataclass
class Position:
    holding: Holding
    price: float          # in the quote currency
    currency: str
    value: float          # in the base currency
    price_base: float     # one share, in the base currency

    @property
    def ticker(self) -> str:
        return self.holding.ticker

    @property
    def sleeve(self) -> str:
        return self.holding.sleeve_key


@dataclass
class Trade:
    ticker: str           # what to actually trade
    sleeve: str
    delta_value: float    # base currency, + = buy, - = sell
    shares: float         # + = buy, - = sell
    price: float          # one share, in the base currency
    current_pct: float
    target_pct: float


def value_portfolio(
    holdings: list[Holding], base: str = "EUR", *, offline: bool = False
) -> list[Position]:
    symbols = sorted({h.ticker for h in holdings if not h.is_cash})
    quotes = get_prices(symbols, offline=offline)
    fx_cache: dict[str, float] = {base.upper(): 1.0}

    positions = []
    for h in holdings:
        if h.is_cash:
            price, currency = 1.0, base.upper()
        elif h.ticker in quotes:
            price, currency = quotes[h.ticker]
        else:
            print(f"  ! skipping {h.ticker}: no price available")
            continue
        currency = (currency or base).upper()
        if currency not in fx_cache:
            fx_cache[currency] = fx_rate(currency, base, offline=offline)
        price_base = price * fx_cache[currency]
        positions.append(
            Position(h, price, currency, h.quantity * price_base, price_base)
        )
    return positions


def suggest_trades(
    positions: list[Position],
    targets: dict[str, float],
    *,
    contribution: float = 0.0,
    band: float = 0.0,
    min_trade: float = 0.0,
    no_sell: bool = False,
    whole_shares: bool = False,
) -> tuple[list[Trade], float]:
    """Trades moving the portfolio towards `targets`; returns (trades, cash left).

    band          drift in percentage points tolerated before trading
    min_trade     ignore trades smaller than this (base currency)
    no_sell       only deploy `contribution`, biggest shortfalls first
    whole_shares  round orders down to whole shares, then spend the remainder
                  on whatever still has the largest shortfall
    """
    current: dict[str, float] = {}
    tradeable: dict[str, Position] = {}
    for p in positions:
        current[p.sleeve] = current.get(p.sleeve, 0.0) + p.value
        if not p.holding.frozen and p.sleeve not in tradeable:
            tradeable[p.sleeve] = p
    for sleeve in targets:
        current.setdefault(sleeve, 0.0)

    total_now = sum(current.values())
    total_after = total_now + contribution
    if total_after <= 0:
        return [], contribution

    shortfall = {s: targets.get(s, 0.0) * total_after - current[s] for s in current}

    if no_sell:
        budget = max(contribution, 0.0)
        wanted = {}
        for sleeve, delta in sorted(shortfall.items(), key=lambda kv: -kv[1]):
            if delta <= 0:
                continue
            take = min(delta, budget)
            if take > 0:
                wanted[sleeve] = take
                budget -= take
        deltas = wanted
    else:
        deltas = shortfall

    trades: list[Trade] = []
    for sleeve, delta in deltas.items():
        current_pct = 100 * current[sleeve] / total_now if total_now else 0.0
        target_pct = 100 * targets.get(sleeve, 0.0)
        # Measure drift against where the sleeve would land if left alone, which
        # with new cash in play is not its weight today: a sleeve sitting exactly
        # on target still has to buy its share, or the contribution dilutes it.
        untraded_pct = 100 * current[sleeve] / total_after
        if abs(untraded_pct - target_pct) <= band or abs(delta) < min_trade:
            continue
        pos = tradeable.get(sleeve)
        if pos is None:
            print(f"  ! {sleeve} needs {delta:+,.0f} but every holding in it is frozen")
            continue
        trades.append(
            Trade(
                ticker=pos.ticker,
                sleeve=sleeve,
                delta_value=delta,
                shares=delta / pos.price_base if pos.price_base else 0.0,
                price=pos.price_base,
                current_pct=current_pct,
                target_pct=target_pct,
            )
        )

    if whole_shares:
        _round_to_whole_shares(trades)
        # Rounding can empty an order entirely; do not print it as a 0-share trade.
        trades = [t for t in trades if t.shares != 0]

    spent = sum(t.delta_value for t in trades)
    return sorted(trades, key=lambda t: -abs(t.delta_value)), contribution - spent


def _round_to_whole_shares(trades: list[Trade]) -> None:
    """Floor every order to whole shares, then re-spend the freed cash."""
    wanted = {t.ticker: t.delta_value for t in trades}
    for t in trades:
        sign = 1 if t.shares >= 0 else -1
        t.shares = sign * math.floor(abs(t.shares))
        t.delta_value = t.shares * t.price

    leftover = sum(wanted.values()) - sum(t.delta_value for t in trades)
    buys = [t for t in trades if wanted[t.ticker] > 0]
    while buys:
        # Whoever is furthest below their intended order and still affordable.
        gaps = [t for t in buys if t.price <= leftover]
        if not gaps:
            break
        pick = max(gaps, key=lambda t: wanted[t.ticker] - t.delta_value)
        if wanted[pick.ticker] - pick.delta_value <= 0:
            break
        pick.shares += 1
        pick.delta_value += pick.price
        leftover -= pick.price


def by_asset_class(positions: list[Position]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in positions:
        key = p.holding.asset_class or "unclassified"
        out[key] = out.get(key, 0.0) + p.value
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
