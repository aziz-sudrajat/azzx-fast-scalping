# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Test risk.py: RiskGuard (daily loss, max posisi, cooldown, kill-switch)."""

import time
import unittest

from scalping.risk import RiskGuard


class TestRiskGuard(unittest.TestCase):
    def test_check_awal_lolos(self):
        g = RiskGuard(max_daily_loss_usd=100.0, max_positions=3, cooldown_sec=300)
        ok, reason = g.check("BTCUSDT", open_count=0)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_daily_loss_block(self):
        g = RiskGuard(max_daily_loss_usd=100.0, cooldown_sec=0)  # tanpa cooldown: uji aturan loss harian saja
        g.record_trade(-60.0)
        ok, _ = g.check("BTCUSDT", 0)
        self.assertTrue(ok)  # belum sampai batas
        g.record_trade(-50.0)  # total -110 <= -100
        ok, reason = g.check("BTCUSDT", 0)
        self.assertFalse(ok)
        self.assertIn("daily loss", reason)

    def test_daily_loss_tepat_batas_block(self):
        g = RiskGuard(max_daily_loss_usd=100.0, cooldown_sec=0)
        g.record_trade(-100.0)
        ok, _ = g.check("BTCUSDT", 0)
        self.assertFalse(ok)

    def test_max_posisi_block(self):
        g = RiskGuard(max_positions=3)
        ok, _ = g.check("BTCUSDT", 2)
        self.assertTrue(ok)
        ok, reason = g.check("BTCUSDT", 3)
        self.assertFalse(ok)
        self.assertIn("max posisi", reason)

    def test_cooldown_setelah_loss(self):
        g = RiskGuard(cooldown_sec=300)
        g.record_trade(-10.0)  # loss -> cooldown mulai
        ok, reason = g.check("BTCUSDT", 0)
        self.assertFalse(ok)
        self.assertIn("cooldown", reason)

        # trade profit tidak memicu cooldown
        g2 = RiskGuard(cooldown_sec=300)
        g2.record_trade(25.0)
        ok, _ = g2.check("BTCUSDT", 0)
        self.assertTrue(ok)

    def test_cooldown_kadaluarsa(self):
        g = RiskGuard(cooldown_sec=60)
        g.record_trade(-10.0, ts=time.time() - 120)  # loss 120 detik lalu
        ok, _ = g.check("BTCUSDT", 0)
        self.assertTrue(ok)

    def test_kill_switch(self):
        g = RiskGuard()
        self.assertFalse(g.is_killed())
        g.kill("darurat manual")
        self.assertTrue(g.is_killed())
        ok, reason = g.check("BTCUSDT", 0)
        self.assertFalse(ok)
        self.assertIn("kill-switch", reason)

    def test_kill_lebih_tinggi_dari_semua(self):
        """Kill-switch menolak walau kondisi lain aman."""
        g = RiskGuard()
        g.kill()
        ok, _ = g.check("BTCUSDT", 0)
        self.assertFalse(ok)

    def test_reset_day(self):
        g = RiskGuard(max_daily_loss_usd=100.0, cooldown_sec=300)
        g.record_trade(-150.0)
        ok, _ = g.check("BTCUSDT", 0)
        self.assertFalse(ok)
        g.reset_day()
        ok, _ = g.check("BTCUSDT", 0)
        self.assertTrue(ok)
        self.assertEqual(g.daily_pnl(), 0.0)

    def test_reset_day_tidak_buka_kill(self):
        """Fail-safe: reset harian tidak mencabut kill manual."""
        g = RiskGuard()
        g.kill("darurat")
        g.reset_day()
        self.assertTrue(g.is_killed())

    def test_parameter_invalid_ditolak(self):
        with self.assertRaises(ValueError):
            RiskGuard(max_daily_loss_usd=0)
        with self.assertRaises(ValueError):
            RiskGuard(max_positions=0)


if __name__ == "__main__":
    unittest.main()
