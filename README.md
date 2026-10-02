# Portfolio

Personal CLI for tracking a portfolio and working out what to buy to stay near its
target allocation. One JSON file per portfolio; nothing personal is in this repo.

```bash
uv sync
cp examples/example-portfolio.json portfolios/me/portfolio.json   # then edit it
export PORTFOLIO_FILE=portfolios/me/portfolio.json
```

The file comes from `-p/--portfolio`, then `$PORTFOLIO_FILE`, then `data/portfolio.json`.

## Commands

```bash
uv run portfolio value                       # value, weights vs target
uv run portfolio rebalance -b 1              # trades, ignoring drift under 1pp
uv run portfolio rebalance -c 800000 --contribution-currency CZK --no-sell --whole-shares
uv run portfolio history                     # value and return over time
uv run portfolio trades                      # the ledger, with running holdings
uv run portfolio --as-of 2026-04-13 value    # what was held on a given day
```

- `--no-sell` deploys new cash only, into the biggest shortfalls.
- `--whole-shares` rounds to integer shares and reports the cash left over.
- `--offline` uses cached prices only.

## The file

```json
{
  "name": "Example",
  "base_currency": "EUR",
  "instruments": [
    { "ticker": "IWDA.AS", "asset_class": "equity" },
    { "ticker": "VUSA.L", "asset_class": "equity", "sleeve": "sp500", "frozen": true },
    { "ticker": "CSPX.L", "asset_class": "equity", "sleeve": "sp500" },
    { "ticker": "AGGH.L", "asset_class": "bond" }
  ],
  "target_classes": { "equity": 60, "bond": 40 },
  "targets": { "IWDA.AS": 45, "sp500": 15, "AGGH.L": 40 },
  "trades": [
    { "date": "2025-01-15", "note": "Initial setup", "fills": [
      { "ticker": "IWDA.AS", "quantity": 100, "price": 92.14, "currency": "EUR" }
    ] }
  ]
}
```

**Quantities are never written down.** They are the running total of the fills, so
after a purchase you append what you did rather than editing a number, and any past
date can be reconstructed. A negative `quantity` is a sale.

- `ticker` — Yahoo symbol (`IWDA.AS`, `VUSA.L`, `BTC-USD`). `CASH` is held at 1.0.
  LSE prices quoted in pence are converted automatically.
- `target_classes` — the real allocation; each class is held to its share.
- `targets` — per sleeve. These only split a class's share *between* its sleeves,
  so the class design can't drift from an edit to one sleeve.
- `sleeve` — several tickers sharing one target. `frozen` counts towards the sleeve
  but is never traded, which switches share class without selling.

## History

`history` values the portfolio by replaying the ledger against historical prices, so
nothing has to be recorded as it happens — including dates before you started using
this. Money paid in is converted at the rate on the day it was paid, so the gain
column is return, not contributions.

Yahoo's back-adjusted closes are deliberately not used; the raw close is what pairs
with the share count in the ledger. That makes a share split the one thing to fix by
hand, by restating the affected fills.

## Personal data

One directory per owner under `portfolios/`. **All of `portfolios/` is gitignored**,
as is anything in `data/`. Check with `git check-ignore -v <path>`.

```bash
uv run pytest
uv run ruff check .
```

Nothing here places orders, and it ignores tax and transaction costs.
