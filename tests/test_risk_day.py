# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Regression: RiskGuard auto-reset saat ganti hari."""
from scalping.risk import RiskGuard


def test_auto_reset_ganti_hari(monkeypatch):
    rg = RiskGuard(max_daily_loss_usd=100.0)
    rg.record_trade(-150.0)  # loss besar -> limit tercapai
    ok, _ = rg.check("BTCUSDT", 0)
    assert not ok  # ditolak

    # simulasi ganti hari
    monkeypatch.setattr(rg, "_today_wib", staticmethod(lambda: "2099-01-02"))
    # paksa _day ke kemarin
    rg._day = "2099-01-01"
    ok2, _ = rg.check("BTCUSDT", 0)
    assert ok2  # reset otomatis -> boleh lagi
    assert rg.daily_pnl() == 0.0
