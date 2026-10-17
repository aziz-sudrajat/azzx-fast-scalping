"""Test render chart SVG — data sintetik, stdlib saja (unittest)."""

import os
import random
import tempfile
import unittest

from scalping.chart import render_chart


def _synthetic_candles(n: int = 60, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    candles = []
    price = 67000.0
    for i in range(n):
        o = price
        drift = rng.uniform(-80, 80)
        c = o + drift
        h = max(o, c) + rng.uniform(0, 60)
        l = min(o, c) - rng.uniform(0, 60)
        v = rng.uniform(50, 500)
        candles.append({
            "open": o, "high": h, "low": l, "close": c,
            "volume": v, "open_time": i * 60_000,
        })
        price = c
    return candles


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    k = 2 / (period + 1)
    e = None
    for i, v in enumerate(values):
        e = v if e is None else v * k + e * (1 - k)
        if i >= period - 1:
            out[i] = e
    return out


class TestRenderChart(unittest.TestCase):
    def test_render_60_candles_2_signals(self):
        candles = _synthetic_candles(60)
        closes = [c["close"] for c in candles]
        signals = [
            {"index": 15, "side": "long", "strength": 2},
            {"index": 45, "side": "short", "strength": 3},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "chart.svg")
            ret = render_chart(candles, _ema(closes, 9), _ema(closes, 21),
                               signals, path, title="BTCUSDT 1m test")
            self.assertEqual(ret, path)
            self.assertTrue(os.path.isfile(path), "file SVG harus ada")
            with open(path, encoding="utf-8") as f:
                svg = f.read()
            self.assertIn("<svg", svg)
            self.assertIn("</svg>", svg)
            # Marker sinyal B2 (long strength 2) dan S3 (short strength 3)
            self.assertIn(">B2<", svg)
            self.assertIn(">S3<", svg)
            # Candlestick + EMA + volume ikut digambar
            self.assertIn("<rect", svg)
            self.assertIn("<polyline", svg)

    def test_empty_candles_raises(self):
        with self.assertRaises(ValueError):
            render_chart([], [], [], [], "/tmp/should-not-exist.svg")


if __name__ == "__main__":
    unittest.main()
