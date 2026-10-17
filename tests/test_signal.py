"""Test scalping/signal.py — memakai modul asli scalping.indicators &
scalping.config (tanpa stub, tanpa polusi sys.modules).
"""
import os
import sys
import types
import random
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scalping import config, indicators  # noqa: E402
from scalping.signal import ScalpEngine, Signal  # noqa: E402


def strict_cfg(**over):
    """Salinan config asli sebagai namespace + override (untuk uji filter)."""
    d = {k: getattr(config, k) for k in dir(config)
         if k.isupper() and not k.startswith("_")}
    d.update(over)
    return types.SimpleNamespace(**d)


def candles_from_closes(closes, seed=1, vol=100.0):
    rnd = random.Random(seed)
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(o, c) + abs(rnd.gauss(0, 0.04))
        lo = min(o, c) - abs(rnd.gauss(0, 0.04))
        out.append({"open": o, "high": hi, "low": lo, "close": c,
                    "volume": vol + rnd.uniform(-15, 15)})
    return out


def firing_setup(direction="long"):
    """Data sintetik trending + window tepat saat sinyal muncul.

    Kembalikan (w1, k5m, k15m, idx, engine_cfg) atau None. Volume spike hanya
    dipasang pada bar kandidat (salinan), agar baseline volume tak terkontaminasi.
    """
    if direction == "long":
        rnd = random.Random(7)
        closes, p = [], 100.0
        for _ in range(110):
            p -= 0.035 + rnd.uniform(-0.02, 0.02)
            closes.append(p)
        for i in range(60):
            p += 0.05 + 0.004 * i + rnd.uniform(-0.03, 0.03)
            closes.append(p)
        k1m = candles_from_closes(closes, seed=7)
        k5m = candles_from_closes([98 + i * 0.1 for i in range(30)], seed=8)
        k15m = candles_from_closes([96 + i * 0.12 for i in range(40)], seed=9)
    else:
        rnd = random.Random(21)
        closes, p = [], 96.0
        for _ in range(110):
            p += 0.035 + rnd.uniform(-0.02, 0.02)
            closes.append(p)
        for i in range(60):
            p -= 0.05 + 0.004 * i + rnd.uniform(-0.03, 0.03)
            closes.append(p)
        k1m = candles_from_closes(closes, seed=21)
        k5m = candles_from_closes([102 - i * 0.1 for i in range(30)], seed=22)
        k15m = candles_from_closes([104 - i * 0.12 for i in range(40)], seed=23)
    eng = ScalpEngine(config)
    for j in range(105, len(k1m)):
        w1 = [dict(c) for c in k1m[:j + 1]]
        w1[-1]["volume"] = 400.0  # spike hanya di bar kandidat
        sig = eng.evaluate(w1, k5m, k15m)
        if sig is not None and sig.side == direction:
            return w1, k5m, k15m, j
    return None


class SignalEngineTest(unittest.TestCase):
    def test_long_signal_valid(self):
        got = firing_setup("long")
        self.assertIsNotNone(got, "sinyal long valid tidak muncul dari data trending")
        w1, k5m, k15m, j = got
        sig = ScalpEngine(config).evaluate(w1, k5m, k15m)
        self.assertIsInstance(sig, Signal)
        self.assertEqual(sig.side, "long")
        self.assertAlmostEqual(sig.price, w1[-1]["close"])
        self.assertAlmostEqual(sig.tp, sig.price + 1.5 * sig.atr)
        self.assertAlmostEqual(sig.sl, sig.price - 1.0 * sig.atr)
        self.assertGreater(sig.tp, sig.price)
        self.assertLess(sig.sl, sig.price)
        self.assertIn(sig.strength, (1, 2, 3))
        self.assertEqual(sig.time_stop_min, config.TIME_STOP_MIN)
        self.assertTrue(sig.reason)

    def test_short_signal_valid(self):
        got = firing_setup("short")
        self.assertIsNotNone(got, "sinyal short valid tidak muncul dari data trending")
        w1, k5m, k15m, j = got
        sig = ScalpEngine(config).evaluate(w1, k5m, k15m)
        self.assertIsInstance(sig, Signal)
        self.assertEqual(sig.side, "short")
        self.assertAlmostEqual(sig.tp, sig.price - 1.5 * sig.atr)
        self.assertAlmostEqual(sig.sl, sig.price + 1.0 * sig.atr)
        self.assertLess(sig.tp, sig.price)
        self.assertGreater(sig.sl, sig.price)
        self.assertIn(sig.strength, (1, 2, 3))

    def test_none_on_flat_chop(self):
        flat = [{"open": 100.0, "high": 100.02, "low": 99.98,
                 "close": 100.0, "volume": 100.0} for _ in range(130)]
        sig = ScalpEngine(config).evaluate(flat, flat[:30], flat[:40])
        self.assertIsNone(sig, "data flat/chop harus menghasilkan None")

    def test_none_on_insufficient_data(self):
        eng = ScalpEngine(config)
        k = candles_from_closes([100 + i * 0.01 for i in range(20)], seed=5)
        self.assertIsNone(eng.evaluate(k, k, k))
        self.assertIsNone(eng.evaluate([], [], []))
        self.assertIsNone(eng.evaluate(None, None, None))

    def test_atr_filter_blocks_low_percentile(self):
        # Ambil window yang pasti memicu sinyal, ukur rank ATR aktual,
        # lalu buktikan: threshold di atas rank -> blokir; tepat di rank -> lolos.
        got = firing_setup("long")
        self.assertIsNotNone(got)
        w1, k5m, k15m, j = got
        rank = indicators.atr_percentile(indicators.atr(w1, config.ATR_PERIOD), 100)
        self.assertIsNotNone(rank)
        self.assertIsNone(
            ScalpEngine(strict_cfg(ATR_MIN_PERCENTILE=rank + 1)).evaluate(w1, k5m, k15m),
            "filter ATR harus memblokir saat rank di bawah ambang",
        )
        self.assertIsNotNone(
            ScalpEngine(strict_cfg(ATR_MIN_PERCENTILE=rank)).evaluate(w1, k5m, k15m),
            "sinyal harus lolos saat rank tepat di ambang",
        )

    def test_spread_filter_blocks(self):
        got = firing_setup("long")
        self.assertIsNotNone(got)
        w1, k5m, k15m, j = got
        eng = ScalpEngine(config)
        self.assertIsNone(eng.evaluate(w1, k5m, k15m, spread_bps=100.0),
                          "spread di atas MAX_SPREAD_BPS harus memblokir")
        self.assertIsNotNone(eng.evaluate(w1, k5m, k15m, spread_bps=2.0),
                             "spread kecil tidak boleh memblokir")
        self.assertIsNotNone(eng.evaluate(w1, k5m, k15m),
                             "tanpa info spread tidak boleh memblokir")


if __name__ == "__main__":
    unittest.main()
