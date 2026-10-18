# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Tests untuk scanner: filter, scoring, tanpa network (mock)."""
from scalping.scanner import pick_candidates, score, _fmt_vol


def _tickers():
    return [
        {"symbol": "BTCUSDT", "quoteVolume": "8000000000",
         "priceChangePercent": "2.5", "lastPrice": "81000"},
        {"symbol": "USDCUSDT", "quoteVolume": "9000000000",
         "priceChangePercent": "0.01", "lastPrice": "1"},
        {"symbol": "BTCUPUSDT", "quoteVolume": "50000000",
         "priceChangePercent": "5.0", "lastPrice": "10"},
        {"symbol": "DOGEUSDT", "quoteVolume": "5000000",
         "priceChangePercent": "-3.0", "lastPrice": "0.12"},
        {"symbol": "TINYUSDT", "quoteVolume": "100000",
         "priceChangePercent": "50.0", "lastPrice": "0.001"},
        {"symbol": "ETHBTC", "quoteVolume": "999999999",
         "priceChangePercent": "1.0", "lastPrice": "0.03"},
    ]


def test_pick_candidates_filter():
    c = pick_candidates(_tickers(), 1_000_000, 10)
    syms = [x["symbol"] for x in c]
    assert "BTCUSDT" in syms
    assert "DOGEUSDT" in syms
    assert "USDCUSDT" not in syms      # stablecoin
    assert "BTCUPUSDT" not in syms     # leveraged token
    assert "TINYUSDT" not in syms      # volume kecil
    assert "ETHBTC" not in syms        # bukan USDT


def test_pick_sorted_by_volume():
    c = pick_candidates(_tickers(), 1_000_000, 10)
    assert c[0]["symbol"] == "BTCUSDT"


def test_score_range():
    cand = {"symbol": "X", "quote_vol": 100_000_000,
            "change_pct": 3.0, "price": 1.0}
    m = {"atr_pct": 0.8, "momentum": 2.0, "vol_trend": 1.3, "price": 1.0}
    s = score(cand, m)
    assert 0 <= s <= 100
    assert s > 50  # kandidat bagus -> skor lumayan


def test_score_dead_coin_rendah():
    cand = {"symbol": "Y", "quote_vol": 6_000_000,
            "change_pct": 0.1, "price": 1.0}
    m = {"atr_pct": 0.02, "momentum": 0.05, "vol_trend": 0.6, "price": 1.0}
    s = score(cand, m)
    assert s < 50  # dead coin -> skor rendah


def test_fmt_vol():
    assert _fmt_vol(8_100_000_000) == "$8.1B"
    assert _fmt_vol(250_000_000) == "$250M"
    assert _fmt_vol(50_000) == "$50K"
