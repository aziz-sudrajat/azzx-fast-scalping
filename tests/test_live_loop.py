# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Tests untuk live_loop.AlternatingTrader (mock broker+data, tanpa network)."""
from scalping.live_loop import AlternatingTrader, _fmt_pnl, _now_wib
from scalping.broker import PaperBroker
from scalping.risk import RiskGuard


def _klines(n=80, start=80000.0, step=10.0):
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"open_time": i, "open": c - 5, "high": c + 20,
                    "low": c - 20, "close": c, "volume": 100.0,
                    "close_time": i})
    return out
# ATR klines ~40  ->  TP = entry + 60, SL = entry - 40


class FakeData:
    def get_klines(self, symbol, interval, limit=200):
        return list(_klines()[-limit:])


class FakeCfg:
    MODE = "paper"
    ATR_PERIOD = 14
    TP_ATR_MULT = 1.5
    SL_ATR_MULT = 1.0
    TIME_STOP_MIN = 60


class PriceFeed:
    """Patch _live_price dengan urutan harga yang ditentukan."""

    def __init__(self, prices):
        self.prices = prices
        self.i = 0
        self._orig = AlternatingTrader._live_price

    def __enter__(self):
        outer = self

        def fake_live(slf, symbol):
            idx = min(outer.i, len(outer.prices) - 1)
            outer.i += 1
            return outer.prices[idx]

        AlternatingTrader._live_price = fake_live
        return self

    def __exit__(self, *a):
        AlternatingTrader._live_price = self._orig


def _run(prices, qty=0.01, max_trades=1):
    events = []
    with PriceFeed(prices):
        t = AlternatingTrader(PaperBroker(), FakeData(), FakeCfg(),
                              RiskGuard(), on_event=events.append)
        t.run("BTCUSDT", qty=qty, max_trades=max_trades, poll_sec=0.01)
    return t, [e for e in events if e.get("type") == "trade_close"]


def test_fmt_helpers():
    assert _fmt_pnl(12.5) == "+12.50"
    assert _fmt_pnl(-3.2) == "-3.20"
    assert len(_now_wib()) == 19


def test_long_hits_tp():
    entry = 80000.0
    # naik +200/poll: TP=80060 kena di poll pertama -> PnL = 60*0.01 - fee > 0
    prices = [entry] + [entry + 200 * (i + 1) for i in range(10)]
    t, closes = _run(prices)
    assert len(closes) == 1
    assert closes[0]["side"] == "LONG"
    assert closes[0]["why"] == "TP"
    assert closes[0]["pnl"] > 0


def test_alternating_long_short():
    entry = 80000.0
    # trade 1 LONG: langsung naik -> TP
    # trade 2 SHORT: entry ~80200, langsung turun -> TP
    p1 = [entry, entry + 200]
    p2base = entry + 200
    p2 = [p2base, p2base - 200]
    t, closes = _run(p1 + p2, max_trades=2)
    assert [c["side"] for c in closes] == ["LONG", "SHORT"]
    assert t.trades_done == 2
    assert all(c["why"] == "TP" for c in closes)


def test_sl_hit():
    entry = 80000.0
    # LONG tapi harga anjlok -> SL = entry - 40 kena
    prices = [entry, entry - 200]
    t, closes = _run(prices)
    assert len(closes) == 1
    assert closes[0]["why"] == "SL"
    assert closes[0]["pnl"] < 0


def test_risk_guard_stops():
    entry = 80000.0
    prices = [entry, entry + 200]
    with PriceFeed(prices):
        t = AlternatingTrader(PaperBroker(), FakeData(), FakeCfg(),
                              RiskGuard())
        t.risk.kill("test kill")
        n = t.run("BTCUSDT", qty=0.01, max_trades=5, poll_sec=0.01)
        assert n == 0
