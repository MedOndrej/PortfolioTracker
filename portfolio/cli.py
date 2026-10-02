"""Command line interface: portfolio value | rebalance | history | trades."""

from __future__ import annotations

import argparse
from datetime import date

from . import valuation
from .model import ENV_VAR, load_portfolio, resolve_path
from .prices import fx_rate
from .rebalance import by_asset_class, suggest_trades, value_portfolio


def _money(x: float) -> str:
    return f"{x:>14,.2f}"


def _open(args):
    """Load the portfolio and settle the base currency for this run."""
    pf = load_portfolio(args.portfolio)
    base = (args.base or pf.base_currency).upper()
    return pf, base


def _holdings(pf, args):
    """Holdings as of --as-of, warning that prices are still today's."""
    as_of = getattr(args, "as_of", None)
    if as_of and pf.trades:
        print(f"\n! quantities as of {as_of}, but priced at today's market. "
              f"This is not a historical valuation.")
    return pf.holdings_as_of(as_of)


def cmd_value(args) -> None:
    pf, base = _open(args)
    if not pf.holdings:
        raise SystemExit(f"{pf.path} has no holdings.")
    positions = value_portfolio(_holdings(pf, args), base, offline=args.offline)
    targets = pf.weights
    total = sum(p.value for p in positions)

    sleeve_value: dict[str, float] = {}
    sleeve_count: dict[str, int] = {}
    for p in positions:
        sleeve_value[p.sleeve] = sleeve_value.get(p.sleeve, 0.0) + p.value
        sleeve_count[p.sleeve] = sleeve_count.get(p.sleeve, 0) + 1

    print(f"\n{pf.name} - value in {base} as of {date.today()}\n")
    print(f"{'TICKER':<12}{'QTY':>12}{'PRICE':>12} {'CUR':<5}"
          f"{'VALUE':>15}{'WEIGHT':>9}{'TARGET':>9}")
    print("-" * 76)
    for p in sorted(positions, key=lambda p: -p.value):
        weight = 100 * p.value / total if total else 0.0
        # The target belongs to the sleeve, so only show it on a single-ticker sleeve.
        tgt = targets.get(p.sleeve)
        if tgt is None:
            tgt_s = f"{'-':>9}"
        elif sleeve_count[p.sleeve] > 1:
            tgt_s = f"{'(sleeve)':>9}"
        else:
            tgt_s = f"{100 * tgt:>8.1f}%"
        label = p.ticker + (" *" if p.holding.frozen else "")
        print(
            f"{label:<12}{p.holding.quantity:>12,.4f}{p.price:>12,.2f} "
            f"{p.currency:<5}{_money(p.value)}{weight:>8.1f}%{tgt_s}"
        )
    print("-" * 76)
    print(f"{'TOTAL':<12}{'':>12}{'':>12} {'':<5}{_money(total)}\n")

    multi = {s: v for s, v in sleeve_value.items() if sleeve_count[s] > 1}
    if multi:
        print("Sleeves")
        for sleeve, value in sorted(multi.items(), key=lambda kv: -kv[1]):
            tgt = 100 * targets.get(sleeve, 0.0)
            share = 100 * value / total if total else 0.0
            print(f"  {sleeve:<20}{_money(value)}{share:>8.1f}%{tgt:>8.1f}% target")
        print()
    if any(p.holding.frozen for p in positions):
        print("  * frozen: counts towards its sleeve, never traded\n")

    classes = by_asset_class(positions)
    if len(classes) > 1:
        class_targets = pf.class_weights
        print("By asset class")
        for name, value in classes.items():
            now = 100 * value / total if total else 0.0
            want = 100 * class_targets.get(name, 0.0)
            drift = f"{now - want:>+7.1f}" if want else f"{'':>7}"
            print(f"  {name:<12}{_money(value)}{now:>8.1f}%{want:>8.1f}% target{drift}")
        print()


def cmd_rebalance(args) -> None:
    pf, base = _open(args)
    if not pf.targets:
        raise SystemExit(f"{pf.path} has no targets - nothing to rebalance towards.")
    positions = value_portfolio(_holdings(pf, args), base, offline=args.offline)
    total = sum(p.value for p in positions)

    contribution = args.contribution
    src_ccy = (args.contribution_currency or base).upper()
    if contribution and src_ccy != base:
        rate = fx_rate(src_ccy, base, offline=args.offline)
        contribution *= rate
        print(f"\nContribution {args.contribution:,.2f} {src_ccy} "
              f"@ {rate:.5f} = {contribution:,.2f} {base}")

    trades, unspent = suggest_trades(
        positions,
        pf.weights,
        contribution=contribution,
        band=args.band,
        min_trade=args.min_trade,
        no_sell=args.no_sell,
        whole_shares=args.whole_shares,
    )

    print(f"\n{pf.name}: {total:,.2f} {base}", end="")
    if contribution:
        print(f" + {contribution:,.2f} new cash = {total + contribution:,.2f}", end="")
    print(f"\nTolerance band +/-{args.band:.1f} pp, min trade {args.min_trade:,.0f} {base}")
    if args.no_sell:
        print("Mode: buy-only (new cash is routed to the largest shortfalls)")
    print()

    if not trades:
        print("Nothing to do - every sleeve is inside its tolerance band.\n")
        return

    shares_fmt = ",.0f" if args.whole_shares else ",.3f"
    print(f"{'TICKER':<12}{'ACTION':<7}{'VALUE':>14}{'SHARES':>12}{'NOW':>8}{'TARGET':>8}  SLEEVE")
    print("-" * 74)
    for t in trades:
        action = "BUY" if t.delta_value > 0 else "SELL"
        sleeve = "" if t.sleeve == t.ticker else t.sleeve
        print(
            f"{t.ticker:<12}{action:<7}{abs(t.delta_value):>14,.2f}"
            f"{abs(t.shares):>12{shares_fmt}}{t.current_pct:>7.1f}%{t.target_pct:>7.1f}%  {sleeve}"
        )
    print("-" * 74)
    buys = sum(t.delta_value for t in trades if t.delta_value > 0)
    sells = -sum(t.delta_value for t in trades if t.delta_value < 0)
    print(f"Buys {buys:,.2f}   Sells {sells:,.2f}   Net {buys - sells:,.2f} {base}")
    if contribution:
        print(f"Cash left over: {unspent:,.2f} {base}")
    print()


def cmd_history(args) -> None:
    pf, base = _open(args)
    if not pf.trades:
        raise SystemExit(
            f"{pf.path} has no trade ledger, so past dates cannot be reconstructed."
        )

    first = min(t.date for t in pf.trades)
    start = args.start or first
    end = args.end or date.today().isoformat()
    if start < first:
        start = first
    dates = valuation.month_ends(start, end)

    points = valuation.build(pf, base, dates)
    if not points:
        raise SystemExit("Nothing to value.")

    print(f"\n{pf.name} - valued in {base} from the trade ledger\n")
    print(f"{'DATE':<12}{'VALUE':>16}{'INVESTED':>16}{'GAIN':>15}{'RETURN':>9}")
    print("-" * 68)
    for pt in points:
        ret = 100 * pt.gain / pt.invested if pt.invested else 0.0
        print(f"{pt.on:<12}{pt.value:>16,.0f}{pt.invested:>16,.0f}"
              f"{pt.gain:>+15,.0f}{ret:>+8.1f}%")
    print("-" * 68)

    last = points[-1]
    print(f"Invested {last.invested:,.0f} {base}, now worth {last.value:,.0f} "
          f"({last.gain:+,.0f}, {100 * last.gain / last.invested if last.invested else 0:+.1f}%)")
    print("Money paid in is converted at the rate on the day it was paid.\n")


def cmd_trades(args) -> None:
    pf, _ = _open(args)
    running: dict[str, float] = {}
    for trade in sorted(pf.trades, key=lambda t: t.date):
        header = f"{trade.date}" + (f"  {trade.note}" if trade.note else "")
        print(f"\n{header}")
        print(f"  {'TICKER':<12}{'QTY':>12}{'PRICE':>12} {'CUR':<5}{'COST':>14}{'HELD':>12}")
        print("  " + "-" * 67)
        # Costs are per-currency: summing EUR and USD lines would be meaningless.
        cost_by_ccy: dict[str, float] = {}
        for fill in trade.fills:
            running[fill.ticker] = running.get(fill.ticker, 0.0) + fill.quantity
            cost_by_ccy[fill.currency] = cost_by_ccy.get(fill.currency, 0.0) + fill.cost
            action = "" if fill.quantity >= 0 else "-"
            print(f"  {fill.ticker:<12}{action + format(abs(fill.quantity), ',.0f'):>12}"
                  f"{fill.price:>12,.4f} {fill.currency:<5}{fill.cost:>14,.2f}"
                  f"{running[fill.ticker]:>12,.0f}")
        print("  " + "-" * 67)
        totals = "   ".join(f"{v:,.2f} {c}" for c, v in sorted(cost_by_ccy.items()))
        print(f"  {len(trade.fills)} fill(s), cost {totals}")
    print(f"\n{len(pf.trades)} trade(s). Holdings are the running total above.\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="portfolio", description=__doc__)
    parser.add_argument("-p", "--portfolio", default=None, metavar="PATH",
                        help=f"portfolio JSON file (default: ${ENV_VAR}, else {resolve_path()})")
    parser.add_argument("--base", default=None,
                        help="report in this currency instead of the portfolio's own")
    parser.add_argument("--offline", action="store_true",
                        help="never hit the network; use cached prices only")
    parser.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                        help="reconstruct quantities from trades up to this date "
                             "(prices are still today's)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("value", help="show current value and weights").set_defaults(func=cmd_value)

    reb = sub.add_parser("rebalance", help="suggest trades to hit target weights")
    reb.add_argument("-c", "--contribution", type=float, default=0.0,
                     help="new cash to invest alongside the rebalance")
    reb.add_argument("--contribution-currency", default=None, metavar="CUR",
                     help="currency of --contribution if it is not the base currency")
    reb.add_argument("-b", "--band", type=float, default=0.0,
                     help="tolerated drift in percentage points (default 0)")
    reb.add_argument("-m", "--min-trade", type=float, default=0.0,
                     help="skip trades smaller than this")
    reb.add_argument("--no-sell", action="store_true",
                     help="only buy: deploy the contribution into the biggest shortfalls")
    reb.add_argument("--whole-shares", action="store_true",
                     help="round orders down to whole shares and re-spend the remainder")
    reb.set_defaults(func=cmd_rebalance)

    hist = sub.add_parser("history", help="value the portfolio over time from the ledger")
    hist.add_argument("--start", default=None, metavar="YYYY-MM-DD",
                      help="first date to value (default: the first trade)")
    hist.add_argument("--end", default=None, metavar="YYYY-MM-DD",
                      help="last date to value (default: today)")
    hist.set_defaults(func=cmd_history)
    led = sub.add_parser("trades", help="show the trade ledger and running holdings")
    led.set_defaults(func=cmd_trades)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
