# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Tests get_balance (pre-flight credential check), mock HTTP."""
import json
import urllib.request

from scalping.exchanges.binance import BinanceExchangeBroker
from scalping.exchanges.okx import OKXExchangeBroker
from scalping.exchanges.bybit import BybitExchangeBroker
from scalping.exchanges.bitget import BitgetExchangeBroker


class _FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock(payload):
    def fake(req, timeout=15):
        return _FakeResp(payload)
    return fake


def test_binance_balance(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _mock({"totalWalletBalance": "1234.56"}))
    b = BinanceExchangeBroker("k", "s", testnet=True)
    assert b.get_balance() == 1234.56


def test_okx_balance(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _mock({"code": "0", "data": [{"totalEq": "789.0"}]}))
    b = OKXExchangeBroker("k", "s", "p", demo=True)
    assert b.get_balance() == 789.0


def test_bybit_balance(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _mock(
        {"retCode": 0,
         "result": {"list": [{"totalWalletBalance": "456.78"}]}}))
    b = BybitExchangeBroker("k", "s", testnet=True)
    assert b.get_balance() == 456.78


def test_bitget_balance(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _mock({"code": "00000",
                               "data": [{"available": "321.0"}]}))
    b = BitgetExchangeBroker("k", "s", "p")
    assert b.get_balance() == 321.0


def test_broker_missing_key():
    import pytest
    with pytest.raises(ValueError):
        BinanceExchangeBroker("", "", testnet=True)
    with pytest.raises(ValueError):
        OKXExchangeBroker("k", "", "p")
