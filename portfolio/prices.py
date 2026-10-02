"""Current price and FX lookup, via yfinance with a 12h cache."""

from __future__ import annotations

import json
import time

from .model import PRICE_CACHE

CACHE_TTL_SECONDS = 12 * 3600


def _load_cache() -> dict:
    if PRICE_CACHE.exists():
        try:
            return json.loads(PRICE_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PRICE_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


# Some exchanges quote in a minor unit: LSE pence, JSE cents, TASE agorot.
# Yahoo reports those as 'GBp' etc., so a price has to be divided down before it
# can be compared with, or converted from, the currency a trade was booked in.
MINOR_UNITS = {"GBp": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


def to_major(price: float, currency: str) -> tuple[float, str]:
    """A price and its currency, in the major unit."""
    if currency in MINOR_UNITS:
        major, divisor = MINOR_UNITS[currency]
        return price / divisor, major
    return price, (currency or "").upper()


def _fetch(symbols: list[str]) -> dict[str, tuple[float, str]]:
    """Last close + native currency per symbol, via yfinance."""
    import yfinance as yf

    out: dict[str, tuple[float, str]] = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = getattr(t, "fast_info", {}) or {}
            price = info.get("last_price") or info.get("lastPrice")
            currency = info.get("currency")
            if price is None:
                hist = t.history(period="5d")
                if not hist.empty:
                    price = float(hist["Close"].dropna().iloc[-1])
            if price is None:
                print(f"  ! no price for {sym}")
                continue
            price, currency = to_major(float(price), currency or "USD")
            out[sym] = (price, currency)
        except Exception as exc:  # network, bad ticker, yfinance internals
            print(f"  ! {sym}: {exc}")
    return out


def get_prices(
    symbols: list[str], *, offline: bool = False, max_age: int = CACHE_TTL_SECONDS
) -> dict[str, tuple[float, str]]:
    """{symbol: (price, currency)}. A stale cache beats nothing."""
    cache = _load_cache()
    now = time.time()

    resolved: dict[str, tuple[float, str]] = {}
    stale: list[str] = []
    for sym in symbols:
        if sym.upper() == "CASH":
            resolved[sym] = (1.0, "")
            continue
        entry = cache.get(sym)
        if entry and now - entry["ts"] < max_age:
            resolved[sym] = (entry["price"], entry["currency"])
        else:
            stale.append(sym)

    if stale and not offline:
        print(f"Fetching {len(stale)} price(s)...")
        for sym, (price, currency) in _fetch(stale).items():
            resolved[sym] = (price, currency)
            cache[sym] = {"price": price, "currency": currency, "ts": now}
        _save_cache(cache)

    for sym in stale:
        if sym not in resolved and sym in cache:
            entry = cache[sym]
            age_h = (now - entry["ts"]) / 3600
            print(f"  using cached {sym} ({age_h:.0f}h old)")
            resolved[sym] = (entry["price"], entry["currency"])
    return resolved


def fx_rate(src: str, dst: str, *, offline: bool = False) -> float:
    """Multiplier to convert an amount in `src` into `dst`."""
    src, dst = src.upper(), dst.upper()
    if not src or src == dst:
        return 1.0
    pair = f"{src}{dst}=X"
    rates = get_prices([pair], offline=offline, max_age=CACHE_TTL_SECONDS)
    if pair in rates:
        return rates[pair][0]
    inverse = f"{dst}{src}=X"
    rates = get_prices([inverse], offline=offline, max_age=CACHE_TTL_SECONDS)
    if inverse in rates and rates[inverse][0]:
        return 1.0 / rates[inverse][0]
    raise SystemExit(f"No FX rate for {src}->{dst}")
