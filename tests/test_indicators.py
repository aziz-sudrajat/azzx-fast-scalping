"""Test indikator: hitungan manual yang bisa diverifikasi dengan tangan.

Setiap nilai ekspektasi dihitung manual di komentar/docstring, bukan dari
implementasi yang dites (menghindari test yang hanya mengulang kode).
"""

import unittest

from scalping.indicators import atr, atr_percentile, cvd, ema, macd, rsi, vwap


def _candle(open_, high, low, close, volume=100.0):
    return {
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


class TestEMA(unittest.TestCase):
    def test_seed_sma_dan_rekursi(self):
        # period=2, k=2/3. seed=SMA(10,12)=11 @idx1.
        # idx2: 14*2/3 + 11*1/3 = 13. idx3: 16*2/3 + 13*1/3 = 15.
        got = ema([10.0, 12.0, 14.0, 16.0], 2)
        self.assertIsNone(got[0])
        for g, e in zip(got[1:], [11.0, 13.0, 15.0]):
            self.assertAlmostEqual(g, e, places=9)

    def test_data_kurang_dari_period(self):
        self.assertEqual(ema([1.0, 2.0], 5), [None, None])

    def test_kosong(self):
        self.assertEqual(ema([], 9), [])

    def test_period_satu_mengikuti_harga(self):
        self.assertEqual(ema([3.0, 5.0, 4.0], 1), [3.0, 5.0, 4.0])

    def test_period_tidak_valid(self):
        for bad in (0, -3):
            with self.assertRaises(ValueError, msg=f"period={bad}"):
                ema([1.0, 2.0], bad)


class TestMACD(unittest.TestCase):
    def test_tanda_tren(self):
        naik = [float(i) for i in range(1, 61)]
        turun = [float(61 - i) for i in range(1, 61)]
        m_naik, s_naik, h_naik = macd(naik)
        m_turun, _, _ = macd(turun)
        self.assertEqual(len(m_naik), 60)
        self.assertGreater(m_naik[-1], 0.0)   # tren naik -> MACD positif
        self.assertLess(m_turun[-1], 0.0)     # tren turun -> MACD negatif
        # slow=26 -> macd_line terdefinisi mulai indeks 25
        self.assertTrue(all(v is None for v in m_naik[:25]))
        self.assertIsNotNone(m_naik[25])
        # signal=9 -> signal_line terdefinisi mulai indeks 25+9-1=33
        self.assertTrue(all(v is None for v in s_naik[:33]))
        self.assertIsNotNone(s_naik[33])
        # histogram = macd - signal di titik terdefinisi
        self.assertAlmostEqual(h_naik[33], m_naik[33] - s_naik[33], places=9)
        self.assertTrue(all(v is None for v in h_naik[:33]))

    def test_data_kurang(self):
        m, s, h = macd([1.0, 2.0, 3.0])
        self.assertEqual(m, [None, None, None])
        self.assertEqual(s, [None, None, None])
        self.assertEqual(h, [None, None, None])

    def test_fast_tidak_boleh_lebih_besar_dari_slow(self):
        with self.assertRaises(ValueError):
            macd([float(i) for i in range(40)], fast=26, slow=12)


class TestRSI(unittest.TestCase):
    def test_wilder_manual(self):
        # values=[10,11,12,11,10], period=2
        # perubahan: +1,+1,-1,-1. seed @idx2: avg_gain=1, avg_loss=0 -> 100.
        # idx3: avg_gain=(1*1+0)/2=0.5, avg_loss=(0*1+1)/2=0.5 -> rs=1 -> 50.
        # idx4: avg_gain=(0.5+0)/2=0.25, avg_loss=(0.5+1)/2=0.75 -> rs=1/3 -> 25.
        got = rsi([10.0, 11.0, 12.0, 11.0, 10.0], 2)
        self.assertEqual(got[:2], [None, None])
        for g, e in zip(got[2:], [100.0, 50.0, 25.0]):
            self.assertAlmostEqual(g, e, places=9)

    def test_semua_flat(self):
        # tanpa gain maupun loss -> 50 (netral), bukan 100
        got = rsi([5.0] * 20, 14)
        self.assertIsNone(got[13])
        self.assertAlmostEqual(got[14], 50.0, places=9)

    def test_naik_terus_mendekati_100(self):
        got = rsi([float(i) for i in range(30)], 14)
        self.assertGreater(got[-1], 99.0)

    def test_data_kurang(self):
        self.assertEqual(rsi([1.0, 2.0], 14), [None, None])


class TestATR(unittest.TestCase):
    def test_wilder_manual(self):
        # TR: [3, 3, 4] (dihitung di docstring atr).
        # seed=SMA(3,3)=3 @idx1; idx2: (3*1+4)/2 = 3.5.
        candles = [
            _candle(10, 12, 9, 11),
            _candle(11, 13, 10, 12),
            _candle(12, 11, 8, 9),
        ]
        got = atr(candles, 2)
        self.assertIsNone(got[0])
        self.assertAlmostEqual(got[1], 3.0, places=9)
        self.assertAlmostEqual(got[2], 3.5, places=9)

    def test_gap_memperbesar_true_range(self):
        # gap up: prev close 10, candle berikut high 20 low 15
        # TR = max(5, |20-10|, |15-10|) = 10
        candles = [_candle(9, 11, 9, 10), _candle(16, 20, 15, 18)]
        got = atr(candles, 1)
        # period=1 -> ATR = TR langsung
        self.assertAlmostEqual(got[0], 2.0, places=9)
        self.assertAlmostEqual(got[1], 10.0, places=9)

    def test_data_kurang(self):
        self.assertEqual(atr([_candle(1, 2, 0.5, 1.5)], 14), [None])


class TestVWAP(unittest.TestCase):
    def test_satu_candle(self):
        # typical=(12+8+10)/3=10
        got = vwap([_candle(10, 12, 8, 10, volume=100.0)])
        self.assertAlmostEqual(got[0], 10.0, places=9)

    def test_kumulatif(self):
        # c1: tp=9, pv=900 ; c2: tp=11, pv=3300 -> (900+3300)/400 = 10.5
        candles = [
            _candle(9, 12, 6, 9, volume=100.0),
            _candle(10, 14, 8, 11, volume=300.0),
        ]
        got = vwap(candles)
        self.assertAlmostEqual(got[0], 9.0, places=9)
        self.assertAlmostEqual(got[1], 10.5, places=9)

    def test_volume_nol(self):
        got = vwap([_candle(1, 2, 0.5, 1.5, volume=0.0)])
        self.assertIsNone(got[0])


class TestCVD(unittest.TestCase):
    def test_tick_rule(self):
        # naik +100, turun -50, flat (close==open) +30 -> [100, 50, 80]
        candles = [
            _candle(10, 12, 9, 11, volume=100.0),
            _candle(11, 12, 8, 9, volume=50.0),
            _candle(9, 10, 8, 9, volume=30.0),
        ]
        self.assertEqual(cvd(candles), [100.0, 50.0, 80.0])

    def test_kosong(self):
        self.assertEqual(cvd([]), [])


class TestATRPercentile(unittest.TestCase):
    def test_maksimum(self):
        self.assertAlmostEqual(atr_percentile([1.0, 2.0, 3.0, 4.0, 5.0]), 100.0)

    def test_minimum(self):
        self.assertAlmostEqual(atr_percentile([5.0, 4.0, 3.0, 2.0, 1.0]), 20.0)

    def test_tengah(self):
        # terakhir=2.0 di [3,1,2]: nilai <= 2.0 ada 2 dari 3 -> 66.67
        self.assertAlmostEqual(
            atr_percentile([3.0, 1.0, 2.0]), 100.0 * 2 / 3, places=9
        )

    def test_none_diabaikan(self):
        self.assertAlmostEqual(
            atr_percentile([None, 2.0, None, 4.0]), 100.0, places=9
        )

    def test_lookback(self):
        # hanya 3 terakhir [3,4,5] -> 100
        self.assertAlmostEqual(
            atr_percentile([1.0, 2.0, 3.0, 4.0, 5.0], lookback=3), 100.0
        )

    def test_tanpa_data_valid(self):
        self.assertIsNone(atr_percentile([]))
        self.assertIsNone(atr_percentile([None, None]))


if __name__ == "__main__":
    unittest.main()
