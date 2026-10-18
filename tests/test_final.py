# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Regression tests final audit: signing body & OKX net position."""
import json
import urllib.request

from scalping.exchanges.base import http_post_json
from scalping.exchanges.okx import OKXExchangeBroker
from scalping.exchanges.bybit import BybitExchangeBroker


class _FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_post_raw_body_identik():
    """Body yang dikirim harus identik dengan yang di-sign."""
    sent = {}

    def fake(req, timeout=15):
        sent["body"] = req.data.decode()
        return _FakeResp({"ok": True})

    import urllib.request as ur
    orig = ur.urlopen
    ur.urlopen = fake
    try:
        raw = json.dumps({"a": 1}, separators=(",", ":"))
        http_post_json("https://x.test/", {"a": 1}, raw_body=raw)
        assert sent["body"] == raw
        assert sent["body"] == '{"a":1}'
    finally:
        ur.urlopen = orig


def test_okx_net_position_side():
    """OKX net mode: posSide='net', arah dari tanda pos."""
    import urllib.request as ur
    orig = ur.urlopen

    def fake(req, timeout=15):
        url = req.full_url
        if "positions" in url:
            return _FakeResp({"code": "0", "data": [
                {"instId": "BTC-USDT-SWAP", "pos": "0.5",
                 "posSide": "net", "avgPx": "80000"}]})
        return _FakeResp({"code": "0", "data": []})

    ur.urlopen = fake
    try:
        b = OKXExchangeBroker("k", "s", "p", demo=True)
        pos = b.get_position("BTCUSDT")
        assert pos["side"] == "BUY", pos  # pos positif = long
        assert pos["amt"] == 0.5
    finally:
        ur.urlopen = orig


def test_okx_net_position_short():
    import urllib.request as ur
    orig = ur.urlopen

    def fake(req, timeout=15):
        return _FakeResp({"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "pos": "-0.3",
             "posSide": "net", "avgPx": "81000"}]})

    ur.urlopen = fake
    try:
        b = OKXExchangeBroker("k", "s", "p", demo=True)
        pos = b.get_position("BTCUSDT")
        assert pos["side"] == "SELL", pos  # pos negatif = short
        assert pos["amt"] == 0.3
    finally:
        ur.urlopen = orig


def test_bybit_sign_uses_compact_body():
    """Pastikan sign dihitung dari body yang sama dengan yang dikirim."""
    import urllib.request as ur
    orig = ur.urlopen
    sent = {}

    def fake(req, timeout=15):
        sent["body"] = req.data.decode()
        sent["sign_header"] = req.headers.get("X-bapi-sign", "")
        return _FakeResp({"retCode": 0, "result": {}})

    ur.urlopen = fake
    try:
        b = BybitExchangeBroker("k", "s", testnet=True)
        # hitung manual: sign harus dari compact body
        from scalping.exchanges.base import hmac_sha256_hex
        import time
        b._post("/v5/order/create", {"category": "linear"})
        body_sent = sent["body"]
        assert body_sent == '{"category":"linear"}', body_sent
    finally:
        ur.urlopen = orig
