"""The portfolio file: instruments, a trade ledger, and target weights.

One JSON file per portfolio. Quantities are never written down - they are the
running total of the trades, so the past stays reconstructable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORTFOLIO = REPO_ROOT / "data" / "portfolio.json"
PRICE_CACHE = REPO_ROOT / "data" / "prices_cache.json"
ENV_VAR = "PORTFOLIO_FILE"


@dataclass
class Holding:
    ticker: str
    name: str = ""
    quantity: float = 0.0
    asset_class: str = ""
    sleeve: str = ""
    frozen: bool = False

    @property
    def is_cash(self) -> bool:
        return self.ticker.upper() == "CASH" or self.asset_class.lower() == "cash"

    @property
    def sleeve_key(self) -> str:
        """Sleeves carry the target weight; a lone ticker is its own sleeve."""
        return self.sleeve or self.ticker

    @classmethod
    def from_dict(cls, d: dict) -> Holding:
        return cls(
            ticker=str(d["ticker"]).strip(),
            name=str(d.get("name") or d["ticker"]).strip(),
            asset_class=str(d.get("asset_class") or "").strip(),
            sleeve=str(d.get("sleeve") or "").strip(),
            frozen=bool(d.get("frozen", False)),
        )


@dataclass
class Fill:
    """One instrument bought or sold. Negative quantity is a sale."""

    ticker: str
    quantity: float
    price: float = 0.0        # per share, in `currency`
    currency: str = ""

    @property
    def cost(self) -> float:
        return self.quantity * self.price

    @classmethod
    def from_dict(cls, d: dict) -> Fill:
        return cls(
            ticker=str(d["ticker"]).strip(),
            quantity=float(d["quantity"]),
            price=float(d.get("price") or 0),
            currency=str(d.get("currency") or "").strip().upper(),
        )


@dataclass
class Trade:
    date: str                 # YYYY-MM-DD
    fills: list[Fill]
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> Trade:
        return cls(
            date=str(d["date"]).strip(),
            fills=[Fill.from_dict(f) for f in d.get("fills", [])],
            note=str(d.get("note") or "").strip(),
        )


@dataclass
class Portfolio:
    name: str
    base_currency: str
    instruments: list[Holding]
    trades: list[Trade]
    targets: dict[str, float]               # per sleeve, any scale
    target_classes: dict[str, float] | None = None
    path: Path | None = None

    def holdings_as_of(self, as_of: str | None = None) -> list[Holding]:
        """Instruments with the quantity implied by every trade up to `as_of`."""
        qty: dict[str, float] = {}
        for trade in self.trades:
            if as_of and trade.date > as_of:
                continue
            for fill in trade.fills:
                qty[fill.ticker] = qty.get(fill.ticker, 0.0) + fill.quantity
        return [replace(h, quantity=qty.get(h.ticker, 0.0)) for h in self.instruments]

    @property
    def holdings(self) -> list[Holding]:
        return self.holdings_as_of(None)

    @property
    def class_of_sleeve(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for h in self.instruments:
            out.setdefault(h.sleeve_key, h.asset_class or "unclassified")
        return out

    @property
    def weights(self) -> dict[str, float]:
        """Per-sleeve targets, normalised to 1.0.

        With `target_classes` set, that is the real allocation: each class is held
        to its share and `targets` only splits that share between the sleeves
        inside it, so the class design cannot drift when one sleeve is edited.
        """
        total = sum(self.targets.values())
        shape = {k: v / total for k, v in self.targets.items()} if total > 0 else {}
        if not self.target_classes:
            return shape

        class_total = sum(self.target_classes.values())
        cls_of = self.class_of_sleeve
        out: dict[str, float] = {}
        for cls, want in self.target_classes.items():
            members = {s: w for s, w in shape.items() if cls_of.get(s) == cls}
            inside = sum(members.values())
            for sleeve, w in members.items():
                share = (w / inside) if inside > 0 else (1 / len(members))
                out[sleeve] = (want / class_total) * share
        return out

    @property
    def class_weights(self) -> dict[str, float]:
        """Target share per asset class, normalised to 1.0."""
        if self.target_classes:
            total = sum(self.target_classes.values())
            return {k: v / total for k, v in self.target_classes.items()}
        cls_of = self.class_of_sleeve
        out: dict[str, float] = {}
        for sleeve, w in self.weights.items():
            cls = cls_of.get(sleeve, "unclassified")
            out[cls] = out.get(cls, 0.0) + w
        return out


def resolve_path(explicit: str | os.PathLike | None = None) -> Path:
    """--portfolio, then $PORTFOLIO_FILE, then data/portfolio.json."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PORTFOLIO).expanduser().resolve()


def load_portfolio(path: str | os.PathLike | None = None) -> Portfolio:
    resolved = resolve_path(path)
    if not resolved.exists():
        raise SystemExit(f"No portfolio at {resolved} (see --portfolio / ${ENV_VAR})")
    raw = json.loads(resolved.read_text(encoding="utf-8"))

    return Portfolio(
        name=raw.get("name") or resolved.stem,
        base_currency=(raw.get("base_currency") or "EUR").upper(),
        instruments=[Holding.from_dict(h) for h in raw["instruments"]],
        trades=sorted(
            (Trade.from_dict(t) for t in raw.get("trades", [])), key=lambda t: t.date
        ),
        targets={str(k): float(v) for k, v in (raw.get("targets") or {}).items()},
        target_classes=raw.get("target_classes"),
        path=resolved,
    )
