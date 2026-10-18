# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Regression tests untuk bug temuan audit pre-push 2026-10-18."""
import pytest

import scalping.broker as bmod
from scalping.broker import BinanceBroker


def test_place_market_qty_no_scientific():
    """BUG3: repr(1e-05) = '1e-05' ditolak Binance. Harus desimal polos."""
    captured = {}
    orig = bmod.BinanceBroker._request

    def fake_req(self, method, path, params, signed=True):
        captured.update(params)
        return {"orderId": 1}

    bmod.BinanceBroker._request = fake_req
    try:
        inst = BinanceBroker("k", "s", testnet=True)
        inst.place_market("BTCUSDT", "BUY", 0.00001)
        assert captured["quantity"] == "0.00001", captured["quantity"]
        assert "e" not in captured["quantity"].lower()
        inst.place_market("BTCUSDT", "BUY", 1.5)
        assert captured["quantity"] == "1.5"
    finally:
        bmod.BinanceBroker._request = orig


def test_main_price_none_fail_closed(monkeypatch):
    """BUG2: klines kosong -> SystemExit yang jelas, bukan TypeError."""
    import scalping.__main__ as m

    class FakeData:
        def get_klines(self, *a, **k):
            return []

    class FakeBroker:
        def set_leverage(self, *a, **k):
            return {}

        def set_margin_type(self, *a, **k):
            return {}

    monkeypatch.setattr(m, "_build_data", lambda mode, ex: FakeData())
    monkeypatch.setattr(m, "_build_broker", lambda mode, ex: FakeBroker())
    with pytest.raises(SystemExit) as ei:
        m.main(["BTCUSDT", "--mode", "paper"])
    assert "fail-closed" in str(ei.value).lower() or "kosong" in str(ei.value)


def test_live_loop_main_exchange_flag():
    """BUG1: live_loop.main dukung -e."""
    import inspect
    import scalping.live_loop as ll
    src = inspect.getsource(ll.main)
    assert '"-e"' in src or "'-e'" in src
    assert "_build_broker(mode, exchange)" in src


def test_webui_emit_fmt_price():
    """BUG4: webui log pakai fmt_price, bukan :.2f."""
    import inspect
    import scalping.webui as w
    src = inspect.getsource(w._emit)
    assert "fmt_price" in src
