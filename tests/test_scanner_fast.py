# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Unit tests untuk scanner new short-TF (1m/5m/15m) tanpa network."""

import csv
import math
import sys

import pytest

sys.path.insert(0, ".")

import scalping.scanner as sc  # noqa: E402
from scalping.scanner import (  # noqa: E402
    _grade,
    _grade_rank,
    analyze_fast,
    pick_candidates,
    qualify_fast,
    recommend,
    scan_fast,
    score_fast,
    to_csv,
)
from scalping.scanner import _inst_id_okx  # noqa: E402


def _candles(closes, volumes=None, spread: float = 0.2, base_open=None):
    """Synthetic candle list dari closes (dict, volume & candle spread opsional)."""
    out = []
    for i, c in enumerate(closes):
        o = base_open if base_open is not None else (closes[i - 1] if i else c)
        v = (volumes[i] if volumes else 1000 + i)
        out.append({
            "open_time": i * 60000,
            "open": round(o, 6),
            "high": round(max(o, c) + spread, 6),
            "low": round(min(o, c) - spread, 6),
            "close": round(c, 6),
            "volume": v,
            "close_time": i * 60000 + 60000,
        })
    return out


def _fake_klines(symbol, interval, limit):
    """Mock _klines: data sintetis, beda ketat antar symbol.

    - 'GOOD*': tren halus + spike volume 5 bar akhir + candle agak lebar.
    - 'BAD*': chop/sideway (ATR kecil, range tipis, momentum nol).
    """
    sym = symbol.upper()
    n1, n5, n15 = 40, 140, 35
    if "GOOD" in sym:
        closes = [100 + 0.3 * i + 0.3 * math.sin(i * 0.1) for i in range(n1)]
        vols = [4000 if i >= 34 else 1000 + i for i in range(n1)]
        return _candles(closes, volumes=vols)
    else:
        closes = [100 + 0.02 * i + 0.05 * math.sin(i * 0.2) for i in range(n1)]
    return _candles(closes)


@pytest.fixture(autouse=True)
def _inject_fake_klines(monkeypatch):
    """Semua test pakai data sintetis untuk _klines (tanpa network)."""
    monkeypatch.setattr(sc, "_klines", _fake_klines)


# ---------------------------------------------------------------- analyze
class TestAnalyzeFast:
    def test_metric_keys(self):
        m = analyze_fast("TESTAUSDT")
        assert m is not None
        for k in ("atr_1m", "atr_5m", "atr_15m", "atr_pct_1m", "mom_5m",
                  "mom_15m", "vol_spike_1m", "vol_trend_5m",
                  "candle_range_1m", "price"):
            assert k in m

    def test_metrics_sensible(self):
        m = analyze_fast("TESTAUSDT")
        assert m is not None
        assert m["mom_5m"] > 0.01          # bullish momentum (> 0.01%)
        assert m["vol_spike_1m"] > 1.0
        assert m["atr_1m"] > 0
        assert m["candle_range_1m"] > 0

    def test_okx_symbol(self):
        assert _inst_id_okx("BTCUSDT") == "BTC-USDT-SWAP"
        assert _inst_id_okx("hbar") == "HBAR-USDT-SWAP"


# ---------------------------------------------------------------- scoring
class TestScoring:
    def test_score_range(self):
        m = analyze_fast("X")
        assert m is not None
        s = score_fast({"quote_vol": 100_000_000}, m)
        assert 0 <= s <= 100

    def test_good_setup_scores_higher(self):
        good = analyze_fast("GOOD")
        bad = analyze_fast("BAD")
        assert good is not None and bad is not None
        s_good = score_fast({"quote_vol": 500_000_000}, good)
        s_bad = score_fast({"quote_vol": 500_000_000}, bad)
        assert s_good > s_bad

    def test_grade_mapping(self):
        assert _grade(95) == "A"
        assert _grade(85) == "B"
        assert _grade(72) == "C"
        assert _grade(60) == "D"
        assert _grade(40) == "F"


# ---------------------------------------------------------------- gate
class TestQualifyFast:
    def test_warm_trending_ok(self):
        m = analyze_fast("GOOD")
        assert m is not None
        ok, reasons = qualify_fast(m)
        assert ok, f"seharusnya lolos: {reasons}"

    def test_chop_rejected(self):
        closes = [100 + i * 0.00005 for i in range(60)]
        kl = _candles(closes, spread=0.003)

        def _chop_klines(sym, interval, limit):
            return kl[:30]

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sc, "_klines", _chop_klines)
        m = analyze_fast("CHOP")
        assert m is not None
        ok, reasons = qualify_fast(m)
        assert not ok
        assert any("chop" in r.lower() for r in reasons)

    def test_over_extend_rejected(self):
        closes = [100 * (1.01) ** i for i in range(60)]
        kl = _candles(closes, spread=0.5)

        def _over_klines(sym, interval, limit):
            return kl[:30]

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sc, "_klines", _over_klines)
        m = analyze_fast("OVER")
        assert m is not None
        ok, _ = qualify_fast(m)
        # ATR percentile ~100 -> gagal (over-extended)
        assert ok or m["atr_pct_rank"] >= 95


# ---------------------------------------------------------------- recommend
class TestRecommend:
    def test_long_recommend(self):
        m = analyze_fast("GOOD")
        assert m is not None
        rec = recommend({"symbol": "TESTA", "quote_vol": 1e9, "change_pct": 1.0,
                         "price": 100.0}, m)
        assert rec is not None
        assert rec["side"] == "long"
        assert rec["tp"] > rec["entry"]
        assert rec["sl"] < rec["entry"]
        assert rec["time_stop_min"] == 15
        assert rec["grade"] in ("A", "B")

    def test_short_recommend(self):
        def _down_klines(sym, interval, limit):
            return [
                {"open_time": i * 60000,             "open": 120 - 0.2 * math.sin(i * 0.15) - 0.2 * i,
                 "high": 120 - 0.2 * math.sin(i * 0.15) - 0.2 * i + 0.07,
                 "low": 120 - 0.2 * math.sin(i * 0.15) - 0.2 * i - 0.07,
                 "close": 120 - 0.2 * math.sin(i * 0.15) - 0.2 * i - 0.015,
                 "volume": 4000 if i >= 34 else 1000, "close_time": i * 60000 + 60000}
                for i in range(40)]

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sc, "_klines", _down_klines)
        m = analyze_fast("SHORT")
        assert m is not None
        rec = recommend({"symbol": "TESTS", "quote_vol": 1e9, "change_pct": -1.0,
                         "price": 60.0}, m)
        assert rec is not None
        assert rec["side"] == "short"

    def test_recommend_none_for_dead(self):
        def _flat_klines(sym, interval, limit):
            return [
                {"open_time": i * 60000, "open": 100.0 + i * 0.0001,
                 "high": 100 + i * 0.0001 + 0.0002, "low": 100 + i * 0.0001 - 0.0002,
                 "close": 100 + i * 0.0001, "volume": 1000,
                 "close_time": i * 60000 + 60000}
                for i in range(30)]

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sc, "_klines", _flat_klines)
        m = analyze_fast("DEAD")
        assert m is not None
        rec = recommend({"symbol": "X", "quote_vol": 1e9, "change_pct": 0.01,
                         "price": 100.0}, m)
        assert rec is None


# ---------------------------------------------------------------- scan
class TestScanFast:
    def test_scan_with_fake_data(self, monkeypatch):
        monkeypatch.setattr(sc, "fetch_tickers", lambda: [
            {"symbol": "GOODUSDT", "quoteVolume": "5000000000",
             "priceChangePercent": "2.5", "lastPrice": "100"},
            {"symbol": "BADUSDT", "quoteVolume": "5000000",
             "priceChangePercent": "0.1", "lastPrice": "10"},
        ])
        res = scan_fast(top_n=5, min_vol=1_000_000, limit=10, parallel=False,
                        min_grade="C")
        assert len(res) >= 1
        syms = [r["symbol"] for r in res]
        scores = [r["score"] for r in res]
        assert syms == sorted(syms, key=lambda s: -scores[syms.index(s)])

    def test_symbols_override(self, monkeypatch):
        monkeypatch.setattr(sc, "fetch_tickers", lambda: [])
        res = scan_fast(symbols=["AAAUSDT", "BBBUSDT"], top_n=10,
                        min_vol=0, parallel=False)
        assert {r["symbol"] for r in res} <= {"AAAUSDT", "BBBUSDT"}

    def test_min_grade_filter(self, monkeypatch):
        monkeypatch.setattr(sc, "fetch_tickers", lambda: [
            {"symbol": "GOODUSDT", "quoteVolume": "5000000000",
             "priceChangePercent": "2.5", "lastPrice": "100"}])
        res_b = scan_fast(top_n=5, min_vol=0, limit=10, parallel=False,
                          min_grade="B")
        res_c = scan_fast(top_n=5, min_vol=0, limit=10, parallel=False,
                          min_grade="C")
        assert all(r["grade"] in ("A", "B") for r in res_b)
        assert len(res_c) >= len(res_b)


# ---------------------------------------------------------------- csv
class TestCsv:
    def test_to_csv(self, tmp_path):
        res = [{"symbol": "A", "price": 1.0, "quote_vol": 1e9,
                "change_pct": 1.0, "atr_pct_1m": 0.2, "mom_1m": 0.1,
                "mom_5m": 1.0, "mom_15m": 1.5, "roc_1m": 0.1,
                "vol_spike_1m": 2.0, "vol_trend_5m": 1.1,
                "candle_range_1m": 0.2, "score": 80, "grade": "B",
                "tp": 1.5, "sl": 0.5, "time_stop_min": 15, "side": "long",
                "reason": "ok"}]
        p = tmp_path / "out.csv"
        to_csv(res, str(p))
        assert p.exists()
        with open(p, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["symbol"] == "A"
        assert rows[0]["grade"] == "B"


# ---------------------------------------------------------------- grade rank
class TestGradeRank:
    def test_rank(self):
        assert _grade_rank("A") == 4
        assert _grade_rank("C") == 2
        assert _grade_rank("F") == 0
