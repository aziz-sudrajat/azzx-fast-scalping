# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Regression tests temuan Shiki + Azz (batch fix)."""
import pytest

from scalping.exchanges.base import quantize_down, LocalPositionTracker
from scalping.risk import RiskGuard


def test_quantize_down():
    assert quantize_down(0.01231134, 0.001) == pytest.approx(0.012)
    assert quantize_down(3.33333333, 0.01) == pytest.approx(3.33)
    assert quantize_down(0.0005, 0.001) == 0.0
    assert quantize_down(1.0, 0.25) == 1.0
    with pytest.raises(ValueError):
        quantize_down(1.0, 0)


def test_track_open_averaging():
    """S1/S2: open tambahan searah di-average, bukan overwrite."""
    t = LocalPositionTracker()
    t._track_open("BTCUSDT", "BUY", 0.01, 80000.0)
    t._track_open("BTCUSDT", "BUY", 0.01, 81000.0)
    pos = t._pos["BTCUSDT"]
    assert pos["qty"] == pytest.approx(0.02)
    assert pos["entry"] == pytest.approx(80500.0)


def test_track_close_fallback():
    """T4-Azz: untracked close pakai fallback entry dari exchange."""
    t = LocalPositionTracker()
    res = t._track_close("BTCUSDT", 81000.0,
                         fallback_entry=80000.0,
                         fallback_side="BUY",
                         fallback_qty=0.01)
    assert "untracked" not in res
    # gross = (81000-80000)*0.01 = 10, fee kecil
    assert res["pnl_usd"] > 9.0


def test_track_close_no_fallback():
    t = LocalPositionTracker()
    res = t._track_close("BTCUSDT", 81000.0)
    assert res["untracked"] is True
    assert res["pnl_usd"] == 0.0


def test_risk_unkill():
    """S3-Azz: kill bisa dicabut eksplisit."""
    rg = RiskGuard()
    rg.kill("test")
    ok, _ = rg.check("BTCUSDT", 0)
    assert not ok
    rg.unkill()
    ok, _ = rg.check("BTCUSDT", 0)
    assert ok


def test_backtest_window_no_leak():
    """R2-Azz: candle tanpa time key -> window kosong (fail-closed)."""
    from scalping.backtest import _window_upto
    candles = [{"close": 1}, {"close": 2}]
    assert _window_upto(candles, None) == []
    candles2 = [{"close": 1, "open_time": 100}, {"close": 2, "open_time": 200}]
    assert len(_window_upto(candles2, 150)) == 1


def test_scanner_excludes_stable_base():
    """R6-Azz: USDD/USDJ/XAUT/PAXG/BFUSD terfilter."""
    from scalping.scanner import pick_candidates
    tickers = [
        {"symbol": "USDDUSDT", "quoteVolume": "99999999",
         "priceChangePercent": "0.1", "lastPrice": "1.0"},
        {"symbol": "BTCUSDT", "quoteVolume": "99999999",
         "priceChangePercent": "1.0", "lastPrice": "80000"},
    ]
    out = pick_candidates(tickers, min_quote_vol=1000, limit=10)
    syms = [c["symbol"] for c in out]
    assert "BTCUSDT" in syms
    assert "USDDUSDT" not in syms


def test_parse_args_rejects_unknown():
    """R4-Shiki: flag tak dikenal ditolak."""
    from scalping.__main__ import parse_args
    with pytest.raises(ValueError):
        parse_args(["BTCUSDT", "--marginn", "100"])
