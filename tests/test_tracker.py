# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Test tracker.py: PositionTracker (avg price, DCA, unrealized PnL)."""

import unittest

from scalping.tracker import PositionTracker


class TestPositionTracker(unittest.TestCase):
    def test_avg_price_dua_fill(self):
        t = PositionTracker()
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        t.add_fill("BTCUSDT", "BUY", 0.5, 60300.0, 2000)
        # (60000*1 + 60300*0.5) / 1.5 = 60100
        self.assertAlmostEqual(t.avg_price("BTCUSDT"), 60100.0)

    def test_avg_price_tanpa_posisi_none(self):
        t = PositionTracker()
        self.assertIsNone(t.avg_price("BTCUSDT"))

    def test_dca_level_dan_can_dca(self):
        t = PositionTracker(max_dca_levels=3)
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        self.assertEqual(t.dca_level("BTCUSDT"), 1)
        self.assertTrue(t.can_dca("BTCUSDT"))

        t.add_fill("BTCUSDT", "BUY", 0.5, 59000.0, 2000)
        self.assertEqual(t.dca_level("BTCUSDT"), 2)
        self.assertTrue(t.can_dca("BTCUSDT"))

        t.add_fill("BTCUSDT", "BUY", 0.5, 58000.0, 3000)
        self.assertEqual(t.dca_level("BTCUSDT"), 3)
        self.assertFalse(t.can_dca("BTCUSDT"))  # mentok

    def test_dca_melebihi_max_ditolak(self):
        t = PositionTracker(max_dca_levels=2)
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        t.add_fill("BTCUSDT", "BUY", 1.0, 59000.0, 2000)
        with self.assertRaises(ValueError):
            t.add_fill("BTCUSDT", "BUY", 1.0, 58000.0, 3000)

    def test_fill_berlawanan_arah_ditolak(self):
        t = PositionTracker()
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        with self.assertRaises(ValueError):
            t.add_fill("BTCUSDT", "SELL", 1.0, 61000.0, 2000)

    def test_qty_dca_lebih_besar_dari_awal_ditolak(self):
        """Anti-martingale: qty DCA <= qty entry awal."""
        t = PositionTracker()
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        with self.assertRaises(ValueError):
            t.add_fill("BTCUSDT", "BUY", 2.0, 59000.0, 2000)
        # qty sama dengan awal masih boleh
        t.add_fill("BTCUSDT", "BUY", 1.0, 59000.0, 3000)
        self.assertEqual(t.dca_level("BTCUSDT"), 2)

    def test_unrealized_pnl_long(self):
        t = PositionTracker()
        t.add_fill("BTCUSDT", "BUY", 2.0, 60000.0, 1000)
        # (61000-60000)*2 = 2000
        self.assertAlmostEqual(t.unrealized_pnl("BTCUSDT", 61000.0), 2000.0)
        self.assertAlmostEqual(t.unrealized_pnl("BTCUSDT", 59500.0), -1000.0)

    def test_unrealized_pnl_short(self):
        t = PositionTracker()
        t.add_fill("ETHUSDT", "SELL", 3.0, 3000.0, 1000)
        # short untung saat harga turun: (3000-2950)*3 = 150
        self.assertAlmostEqual(t.unrealized_pnl("ETHUSDT", 2950.0), 150.0)
        self.assertAlmostEqual(t.unrealized_pnl("ETHUSDT", 3100.0), -300.0)

    def test_unrealized_pnl_tanpa_posisi_none(self):
        t = PositionTracker()
        self.assertIsNone(t.unrealized_pnl("BTCUSDT", 60000.0))

    def test_close_hapus_dan_ringkasan(self):
        t = PositionTracker()
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        summary = t.close("BTCUSDT")
        self.assertEqual(summary["symbol"], "BTCUSDT")
        self.assertEqual(summary["side"], "LONG")
        self.assertAlmostEqual(summary["avg_price"], 60000.0)
        self.assertEqual(summary["level"], 1)
        self.assertEqual(t.open_positions(), [])
        self.assertIsNone(t.avg_price("BTCUSDT"))

    def test_close_tanpa_posisi_none(self):
        t = PositionTracker()
        self.assertIsNone(t.close("BTCUSDT"))

    def test_open_positions(self):
        t = PositionTracker()
        t.add_fill("BTCUSDT", "BUY", 1.0, 60000.0, 1000)
        t.add_fill("ETHUSDT", "SELL", 2.0, 3000.0, 2000)
        self.assertEqual(t.open_positions(), ["BTCUSDT", "ETHUSDT"])


if __name__ == "__main__":
    unittest.main()
