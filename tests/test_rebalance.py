"""Rebalance maths and ledger replay. No network: positions are built directly."""

from portfolio.model import Fill, Holding, Portfolio, Trade
from portfolio.rebalance import Position, suggest_trades


def pos(ticker, qty, price, *, sleeve="", frozen=False, asset_class="equity"):
    h = Holding(ticker=ticker, name=ticker, quantity=qty, asset_class=asset_class,
                sleeve=sleeve, frozen=frozen)
    return Position(h, price, "EUR", qty * price, price)


def test_contribution_is_fully_allocated_and_lands_on_target():
    # Buy-only can only reach the target if no sleeve is above it afterwards.
    positions = [pos("A", 100, 10), pos("B", 100, 10)]  # 1000 / 1000, 50/50
    targets = {"A": 0.4, "B": 0.6}
    trades, unspent = suggest_trades(positions, targets, contribution=1000, no_sell=True)

    assert unspent == 0
    after = {"A": 1000.0, "B": 1000.0}
    for t in trades:
        after[t.ticker] += t.delta_value
    total = sum(after.values())
    assert total == 3000
    assert round(after["A"] / total, 9) == 0.4
    assert round(after["B"] / total, 9) == 0.6


def test_no_sell_never_sells_even_when_overweight():
    positions = [pos("A", 100, 10), pos("B", 10, 10)]  # 1000 / 100
    targets = {"A": 0.5, "B": 0.5}
    trades, _ = suggest_trades(positions, targets, contribution=100, no_sell=True)

    assert all(t.delta_value > 0 for t in trades)
    assert [t.ticker for t in trades] == ["B"]


def test_without_no_sell_an_overweight_position_is_sold():
    positions = [pos("A", 100, 10), pos("B", 10, 10)]
    targets = {"A": 0.5, "B": 0.5}
    trades, _ = suggest_trades(positions, targets)

    by_ticker = {t.ticker: t for t in trades}
    assert by_ticker["A"].delta_value < 0
    assert by_ticker["B"].delta_value > 0
    assert round(sum(t.delta_value for t in trades), 6) == 0


def test_whole_shares_never_overspends_the_contribution():
    positions = [pos("A", 10, 97.3), pos("B", 10, 51.7)]
    targets = {"A": 0.5, "B": 0.5}
    trades, unspent = suggest_trades(
        positions, targets, contribution=1000, no_sell=True, whole_shares=True
    )

    assert all(float(t.shares).is_integer() for t in trades)
    assert sum(t.delta_value for t in trades) <= 1000
    assert unspent >= 0
    assert round(sum(t.delta_value for t in trades) + unspent, 6) == 1000


def test_frozen_holding_counts_to_its_sleeve_but_is_never_traded():
    positions = [
        pos("OLD", 100, 10, sleeve="eu", frozen=True),
        pos("NEW", 0, 10, sleeve="eu"),
        pos("OTHER", 100, 10),
    ]
    targets = {"eu": 0.5, "OTHER": 0.5}
    trades, _ = suggest_trades(positions, targets, contribution=1000, no_sell=True)

    assert "OLD" not in [t.ticker for t in trades]
    eu = [t for t in trades if t.sleeve == "eu"]
    assert eu and eu[0].ticker == "NEW"


def test_band_and_min_trade_suppress_small_moves():
    positions = [pos("A", 100, 10), pos("B", 98, 10)]  # 50.5% / 49.5%
    targets = {"A": 0.5, "B": 0.5}

    assert suggest_trades(positions, targets, band=2.0)[0] == []
    assert suggest_trades(positions, targets, min_trade=1000)[0] == []
    assert suggest_trades(positions, targets)[0] != []


def test_class_targets_override_sleeve_weights():
    pf = Portfolio(
        name="x",
        base_currency="EUR",
        instruments=[
            Holding(ticker="E1", quantity=1, asset_class="equity"),
            Holding(ticker="E2", quantity=1, asset_class="equity"),
            Holding(ticker="B1", quantity=1, asset_class="bond"),
        ],
        trades=[],
        targets={"E1": 30, "E2": 10, "B1": 60},
        target_classes={"equity": 50, "bond": 50},
    )
    w = pf.weights

    # Classes land exactly on target...
    assert round(w["E1"] + w["E2"], 9) == 0.5
    assert round(w["B1"], 9) == 0.5
    # ...and `targets` only sets the split inside a class (30:10 -> 3:1).
    assert round(w["E1"] / w["E2"], 9) == 3.0
    assert round(sum(w.values()), 9) == 1.0


def test_class_weights_fall_back_to_sleeve_targets():
    pf = Portfolio(
        name="x",
        base_currency="EUR",
        instruments=[
            Holding(ticker="E1", quantity=1, asset_class="equity"),
            Holding(ticker="B1", quantity=1, asset_class="bond"),
        ],
        trades=[],
        targets={"E1": 70, "B1": 30},
    )
    assert pf.class_weights == {"equity": 0.7, "bond": 0.3}


def test_sleeve_already_on_target_still_gets_its_share_of_new_cash():
    # With a contribution, "on target today" is not "needs no trade": doing
    # nothing would dilute the sleeve as the rest of the portfolio grows.
    positions = [pos("A", 100, 10), pos("B", 100, 10)]
    targets = {"A": 0.5, "B": 0.5}
    trades, unspent = suggest_trades(
        positions, targets, contribution=1000, band=1.0, no_sell=True
    )

    assert {t.ticker for t in trades} == {"A", "B"}
    assert unspent == 0


def test_band_still_suppresses_trades_when_there_is_no_contribution():
    positions = [pos("A", 100, 10), pos("B", 100, 10)]
    targets = {"A": 0.5, "B": 0.5}
    assert suggest_trades(positions, targets, band=1.0)[0] == []


def test_whole_shares_drops_orders_that_round_to_nothing():
    # B is only a hair off target, so its order floors to zero shares.
    positions = [pos("A", 1000, 10), pos("B", 100, 10)]
    targets = {"A": 0.9091, "B": 0.0909}
    trades, _ = suggest_trades(positions, targets, whole_shares=True)

    assert all(t.shares != 0 for t in trades)


def _ledger(**kw):
    from portfolio.model import Fill

    return Portfolio(
        name="x",
        base_currency="EUR",
        instruments=[
            Holding(ticker="A", asset_class="equity"),
            Holding(ticker="B", asset_class="bond"),
        ],
        targets={"A": 50, "B": 50},
        trades=[
            Trade(date="2025-01-10", fills=[
                Fill("A", 100, 10.0, "EUR"), Fill("B", 50, 20.0, "EUR")]),
            Trade(date="2025-06-01", fills=[
                Fill("A", 25, 12.0, "EUR"), Fill("B", -10, 22.0, "EUR")]),
        ],
        **kw,
    )


def test_quantities_are_the_running_total_of_the_ledger():
    qty = {h.ticker: h.quantity for h in _ledger().holdings}
    assert qty == {"A": 125, "B": 40}


def test_as_of_replays_only_trades_up_to_that_date():
    pf = _ledger()
    before = {h.ticker: h.quantity for h in pf.holdings_as_of("2025-01-09")}
    at_first = {h.ticker: h.quantity for h in pf.holdings_as_of("2025-01-10")}
    between = {h.ticker: h.quantity for h in pf.holdings_as_of("2025-03-01")}

    assert before == {"A": 0, "B": 0}
    assert at_first == {"A": 100, "B": 50}   # inclusive of the trade date
    assert between == {"A": 100, "B": 50}


def test_fill_cost_signs_sales():
    assert Fill("A", 10, 5.0, "EUR").cost == 50.0
    assert Fill("A", -10, 5.0, "EUR").cost == -50.0


def test_pence_quotes_are_converted_to_pounds():
    # LSE quotes in pence; a trade booked in GBP would otherwise be valued 100x.
    from portfolio.prices import to_major

    assert to_major(17190.0, "GBp") == (171.90, "GBP")
    assert to_major(171.34, "EUR") == (171.34, "EUR")
