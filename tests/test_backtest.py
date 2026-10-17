"""Test scalping/backtest.py — memakai modul asli (tanpa stub)."""
import os
import sys
import random
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scalping import config  # noqa: E402
from scalping.backtest import run_backtest  # noqa: E402
from scalping.signal import ScalpEngine  # noqa: E402


def build_market():
    """600 bar 1m: turun -> naik -> pullback -> naik tajam (deterministik).

    k5m/k15m diagregasi dari 1m dengan field 'time' agar alignment
    multi-timeframe di backtest teruji.
    """
    rnd = random.Random(11)
    k1m = []
    p = 110.0
    for i in range(600):
        if i < 400:
            p -= 0.02 + rnd.uniform(-0.02, 0.02)
        elif i < 500:
            p += 0.05 + rnd.uniform(-0.03, 0.03)
        elif i < 540:
            p -= 0.06 + rnd.uniform(-0.02, 0.02)
        else:
            p += 0.07 + rnd.uniform(-0.03, 0.03)
        o = k1m[-1]["close"] if k1m else p
        hi = max(o, p) + abs(rnd.gauss(0, 0.05))
        lo = min(o, p) - abs(rnd.gauss(0, 0.05))
        v = 350.0 if rnd.random() < 0.06 else 100 + rnd.uniform(-15, 15)
        k1m.append({"time": i, "open": o, "high": hi, "low": lo,
                    "close": p, "volume": v})

    def agg(kl, step):
        out = []
        for i in range(0, len(kl), step):
            grp = kl[i:i + step]
            out.append({"time": grp[0]["time"], "open": grp[0]["open"],
                        "high": max(c["high"] for c in grp),
                        "low": min(c["low"] for c in grp),
                        "close": grp[-1]["close"],
                        "volume": sum(c["volume"] for c in grp)})
        return out

    k5m, k15m = agg(k1m, 5), agg(k1m, 15)

    # Cari bar cross bullish yang valid, lalu paksa volume spike di bar itu
    # (pada data aktual) agar backtest punya minimal 1 trade.
    eng = ScalpEngine(config)
    for j in range(545, 600):
        w1 = [dict(c) for c in k1m[:j + 1]]
        w1[-1]["volume"] = 400.0
        t = w1[-1]["time"]
        w5 = [c for c in k5m if c["time"] <= t]
        w15 = [c for c in k15m if c["time"] <= t]
        if eng.evaluate(w1, w5, w15) is not None:
            k1m[j]["volume"] = 400.0
            break
    return k1m, k5m, k15m


def build_flat():
    flat1 = [{"time": i, "open": 100.0, "high": 100.02, "low": 99.98,
              "close": 100.0, "volume": 100.0} for i in range(130)]
    flat5 = [{"time": i * 5, "open": 100.0, "high": 100.02, "low": 99.98,
              "close": 100.0, "volume": 500.0} for i in range(30)]
    flat15 = [{"time": i * 15, "open": 100.0, "high": 100.02, "low": 99.98,
               "close": 100.0, "volume": 1500.0} for i in range(40)]
    return flat1, flat5, flat15


class BacktestTest(unittest.TestCase):
    def test_trades_happen_and_metrics_consistent(self):
        k1m, k5m, k15m = build_market()
        res = run_backtest(k1m, k5m, k15m, config)
        self.assertGreaterEqual(res["n_trades"], 1)
        self.assertEqual(res["n_trades"], len(res["trades"]))

        pnls = [t["pnl_net"] for t in res["trades"]]
        for t in res["trades"]:
            self.assertIn(t["reason"], ("tp", "sl", "time_stop", "eod"))
            self.assertIn(t["side"], ("long", "short"))
            # fee+slippage selalu mengurangi PnL: pnl_net < gross (eff, excl fee)
            direction = 1 if t["side"] == "long" else -1
            gross_eff = (t["exit_price"] - t["entry_price"]) * direction
            self.assertLess(t["pnl_net"], gross_eff,
                            "fee harus membuat pnl_net < gross")
            self.assertAlmostEqual(t["pnl_net"], gross_eff - t["fee_total"])
            self.assertGreater(t["fee_total"], 0)
            # slippage merugikan entry: harga entry eff lebih buruk dari close
            raw_entry = k1m[t["entry_index"]]["close"]
            if t["side"] == "long":
                self.assertGreater(t["entry_price"], raw_entry)
            else:
                self.assertLess(t["entry_price"], raw_entry)

        wins = sum(1 for p in pnls if p > 0)
        self.assertAlmostEqual(res["winrate"], wins / len(pnls))
        self.assertAlmostEqual(res["total_pnl"], sum(pnls))
        self.assertAlmostEqual(res["expectancy"], sum(pnls) / len(pnls))
        gp = sum(p for p in pnls if p > 0)
        gl = -sum(p for p in pnls if p < 0)
        if gl > 0:
            self.assertAlmostEqual(res["profit_factor"], gp / gl)
        self.assertGreaterEqual(res["max_drawdown"], 0.0)
        self.assertTrue(0.0 <= res["winrate"] <= 1.0)

    def test_no_trades_metrics_are_none_safe(self):
        k1m, k5m, k15m = build_flat()
        res = run_backtest(k1m, k5m, k15m, config)
        self.assertEqual(res["n_trades"], 0)
        self.assertEqual(res["trades"], [])
        self.assertIsNone(res["winrate"], "winrate harus None saat 0 trade")
        self.assertIsNone(res["profit_factor"], "profit_factor harus None saat 0 trade")
        self.assertEqual(res["total_pnl"], 0.0)
        self.assertEqual(res["expectancy"], 0.0)
        self.assertEqual(res["max_drawdown"], 0.0)

    def test_single_position_per_side_no_pyramiding(self):
        k1m, k5m, k15m = build_market()
        res = run_backtest(k1m, k5m, k15m, config)
        # Tidak boleh ada dua posisi SATU arah yang overlap dalam waktu.
        for side in ("long", "short"):
            spans = sorted((t["entry_index"], t["exit_index"])
                           for t in res["trades"] if t["side"] == side)
            for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
                self.assertGreaterEqual(b0, a1,
                                        "pyramiding terdeteksi pada sisi %s" % side)


if __name__ == "__main__":
    unittest.main()
