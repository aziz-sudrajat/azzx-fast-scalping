# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Regression: market_close tidak boleh menimpa entry (PnL harus benar)."""
import pytest

from scalping.exchanges.bybit import BybitExchangeBroker
from scalping.exchanges.okx import OKXExchangeBroker
from scalping.exchanges.bitget import BitgetExchangeBroker

_CASES = [
    (BybitExchangeBroker, {"api_key": "k", "api_secret": "s"}),
    (OKXExchangeBroker, {"api_key": "k", "api_secret": "s", "passphrase": "p"}),
    (BitgetExchangeBroker, {"api_key": "k", "api_secret": "s", "passphrase": "p"}),
]


@pytest.mark.parametrize("cls,kw", _CASES)
def test_market_close_pnl_long_benar(cls, kw, monkeypatch):
    """Open LONG @100, close @110 -> PnL ~ +9.895, bukan ~0 (bug tracking)."""
    b = cls(**kw)

    def fake_post(path, payload):
        if cls.__name__ == "BitgetExchangeBroker":
            return {"code": "00000", "data": {"orderId": "1"}}
        if cls.__name__ == "OKXExchangeBroker":
            return {"code": "0", "data": [{"ordId": "1"}]}
        return {"retCode": 0, "result": {"orderId": "1"}}

    monkeypatch.setattr(b, "_post", fake_post)
    b.market_open("BTCUSDT", "BUY", 1.0, 100.0)
    monkeypatch.setattr(b, "get_position",
                        lambda symbol: {"symbol": "BTCUSDT", "amt": 1.0,
                                        "entry_price": 100.0, "side": "BUY"})
    res = b.market_close("BTCUSDT", 110.0)
    pnl = res["pnl_usd"]
    # gross +10, fee (100+110)*1*0.0005=0.105 -> 9.895
    assert abs(pnl - 9.895) < 0.01, f"PnL salah: {pnl}"


@pytest.mark.parametrize("cls,kw", _CASES)
def test_market_close_pnl_short_benar(cls, kw, monkeypatch):
    """Open SHORT @100, close @90 -> PnL ~ +9.905, bukan ~0."""
    b = cls(**kw)

    def fake_post(path, payload):
        if cls.__name__ == "BitgetExchangeBroker":
            return {"code": "00000", "data": {"orderId": "1"}}
        if cls.__name__ == "OKXExchangeBroker":
            return {"code": "0", "data": [{"ordId": "1"}]}
        return {"retCode": 0, "result": {"orderId": "1"}}

    monkeypatch.setattr(b, "_post", fake_post)
    b.market_open("BTCUSDT", "SELL", 2.0, 100.0)
    monkeypatch.setattr(b, "get_position",
                        lambda symbol: {"symbol": "BTCUSDT", "amt": 2.0,
                                        "entry_price": 100.0, "side": "SELL"})
    res = b.market_close("BTCUSDT", 90.0)
    pnl = res["pnl_usd"]
    # gross +20, fee (100+90)*2*0.0005=0.19 -> 19.81
    assert abs(pnl - 19.81) < 0.01, f"PnL salah: {pnl}"
