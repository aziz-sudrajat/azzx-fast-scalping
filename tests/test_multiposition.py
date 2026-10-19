# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Test multi-position: 0=unlimited, entry terpisah per TP/SL, animasi log."""

import json
import threading
import time
import urllib.parse
import urllib.request
from types import SimpleNamespace
from unittest import mock

import pytest

from scalping.broker import PaperBroker
from scalping.risk import RiskGuard
from scalping.signal_loop import SignalTrader


# ------------------------------------------------------------------ fakes
class FakeData:
    def __init__(self, price=100.0, fail_klines=False):
        self.price = price
        self.fail_klines = fail_klines

    def get_ticker_price(self, symbol):
        return self.price

    def get_klines(self, symbol, interval, limit=200):
        if self.fail_klines:
            raise RuntimeError("jaringan putus")
        return [{"open": 100.0, "high": 101.0, "low": 99.0,
                 "close": 100.0, "volume": 10.0} for _ in range(limit)]


def fake_cfg():
    return SimpleNamespace(TP_ATR_MULT=1.5, SL_ATR_MULT=1.0, MODE="paper")


def fake_sig(price=100.0, side="long"):
    return SimpleNamespace(price=price, side=side, atr=2.0, strength=2,
                           reason="test", time_stop_min=15)


def make_trader(**kw):
    kw.setdefault("max_positions", 0)
    risk = RiskGuard(max_daily_loss_usd=100.0,
                     max_positions=kw.pop("max_positions"),
                     cooldown_sec=300)
    return SignalTrader(PaperBroker(1000.0), FakeData(), fake_cfg(), risk,
                        mode="paper", leverage=10.0)


# ------------------------------------------------------------------ risk
class TestRiskUnlimited:
    def test_nol_tanpa_batas(self):
        g = RiskGuard(max_positions=0)
        assert g.check("BTCUSDT", 50)[0] is True

    def test_negatif_ditolak(self):
        with pytest.raises(ValueError):
            RiskGuard(max_positions=-1)

    def test_batas_dua_menolak_ketiga(self):
        g = RiskGuard(max_positions=2)
        assert g.check("BTCUSDT", 1)[0] is True
        ok, reason = g.check("BTCUSDT", 2)
        assert ok is False and "max posisi" in reason


# ------------------------------------------------------------------ broker
class TestPaperBrokerCloseQty:
    def test_partial_close(self):
        b = PaperBroker(1000.0)
        b.market_open("BTCUSDT", "BUY", 2.0, 100.0)
        r = b.close_qty("BTCUSDT", 0.5, 120.0)
        assert r["pnl_usd"] == pytest.approx(10.0)
        assert r["remaining_qty"] == pytest.approx(1.5)
        assert b.balance() == pytest.approx(1010.0)

    def test_full_menghapus_posisi(self):
        b = PaperBroker(1000.0)
        b.market_open("BTCUSDT", "SELL", 1.0, 100.0)
        b.close_qty("BTCUSDT", 1.0, 90.0)
        assert b.get_position("BTCUSDT") is None

    def test_over_qty_ditolak(self):
        b = PaperBroker(1000.0)
        b.market_open("BTCUSDT", "BUY", 1.0, 100.0)
        with pytest.raises(ValueError):
            b.close_qty("BTCUSDT", 2.0, 100.0)

    def test_tanpa_posisi_ditolak(self):
        b = PaperBroker(1000.0)
        with pytest.raises(RuntimeError):
            b.close_qty("BTCUSDT", 1.0, 100.0)


# ------------------------------------------------------------------ trader
class TestSignalTraderMulti:
    def test_dua_entry_jadi_posisi_terpisah(self):
        t = make_trader()
        t.data.price = 100.0
        tr1 = t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 1.0)
        t.data.price = 102.0
        tr2 = t._open_trade("BTCUSDT", fake_sig(102.0, "long"), 1.0)
        assert tr1 and tr2
        assert len(t.open_trades) == 2
        assert tr1["id"] != tr2["id"]
        assert tr1["entry"] != tr2["entry"]
        assert tr1["tp"] != tr2["tp"] and tr1["sl"] != tr2["sl"]

    def test_risk_guard_membatasi_entry(self):
        t = make_trader(max_positions=1)
        t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 1.0)
        tr, reason = t._maybe_open("BTCUSDT", 1.0)
        assert tr is None
        assert reason and "max posisi" in reason
        assert len(t.open_trades) == 1

    def test_tutup_satu_lainnya_tetap(self):
        t = make_trader()
        tr1 = t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 1.0)
        tr2 = t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 1.0)
        assert t._close_trade(tr1, 110.0, "TP") is True
        assert len(t.open_trades) == 1
        assert t.open_trades[0]["id"] == tr2["id"]
        assert t.trades_done == 1
        # broker paper ikut berkurang
        pos = t.broker.get_position("BTCUSDT")
        assert pos["qty"] == pytest.approx(1.0)

    def test_sinyal_lawan_arah_ditolak_one_way(self):
        t = make_trader()
        t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 1.0)
        t.engine.evaluate = lambda *a: fake_sig(100.0, "short")
        tr, reason = t._maybe_open("BTCUSDT", 1.0)
        assert tr is None and reason is None
        assert len(t.open_trades) == 1

    def test_preflight_margin_menolak_bila_saldo_kurang(self):
        t = make_trader()  # balance 1000, lev 10
        # butuh margin 100*100/10 = 1000 > ... pakai qty besar
        tr = t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 500.0)
        assert tr is None  # 500*100/10 = 5000 > 1000
        tr2 = t._open_trade("BTCUSDT", fake_sig(100.0, "long"), 1.0)
        assert tr2 is not None

    def test_run_max_trades_tunggu_posisi_tutup(self):
        # stop saat posisi terbuka -> posisi ditutup satu per satu (tak orphan)
        t = make_trader()
        opened = []

        def fake_maybe_open(symbol, qty):
            if not opened:
                tr = t._open_trade(symbol, fake_sig(100.0, "long"), qty)
                opened.append(tr)
                return tr, None
            return None, None

        t._maybe_open = fake_maybe_open
        th = threading.Thread(
            target=t.run, args=("BTCUSDT", 1.0),
            kwargs={"max_trades": 0, "poll_sec": 0.02, "monitor_sec": 0.02},
            daemon=True)
        th.start()
        time.sleep(0.3)
        assert len(t.open_trades) == 1
        t.stop()  # manual-stop harus menutup posisi yang terbuka
        th.join(timeout=5)
        assert not th.is_alive()
        assert t.open_trades == []
        assert t.trades_done == 1


# ------------------------------------------------------------------ webui
@pytest.fixture(scope="module")
def server():
    import scalping.web_ui as wu
    srv = wu.make_server("127.0.0.1", 0, start_scanner=False)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _url(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


def _get(server, path):
    with urllib.request.urlopen(_url(server) + path, timeout=10) as r:
        return r.status, r.read()


def _post(server, path):
    req = urllib.request.Request(_url(server) + path, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


class TestWebUiMulti:
    def test_html_memuat_marker_animasi_dan_maxpos(self, server):
        _, body = _get(server, "/")
        text = body.decode("utf-8")
        for marker in ("@keyframes trow-in", "trow-new", "@keyframes shimmer",
                       "flash-up", "flash-dn", "@keyframes upnl-up",
                       'id="maxpos"', "seenTrades", "prevUpnl",
                       "max pos:", "maxpositions"):
            assert marker in text, f"marker hilang: {marker}"

    def test_state_punya_max_positions(self, server):
        _, body = _get(server, "/api/state")
        d = json.loads(body)
        assert "max_positions" in d
        assert d["max_positions"] == 0  # default tanpa batas

    def test_start_meneruskan_maxpos_ke_engine(self, server):
        import scalping.web_ui as wu
        fake_data = FakeData(price=50000.0, fail_klines=True)

        def fake_broker(mode, exchange):
            return PaperBroker(1000.0)

        def fake_data_fn(mode, exchange):
            return fake_data

        try:
            with mock.patch.object(wu, "_build_broker", fake_broker), \
                 mock.patch.object(wu, "_build_data", fake_data_fn):
                status, body = _post(
                    server,
                    "/api/start?symbol=BTCUSDT&margin=100&lev=10&mode=paper"
                    "&maxpositions=5")
                d = json.loads(body)
                assert d["ok"] is True, d
                time.sleep(0.4)
                _, sb = _get(server, "/api/state")
                sd = json.loads(sb)
                assert sd["running"] is True
                assert sd["max_positions"] == 5
                tr = wu._state["trader"]
                assert tr is not None
                assert tr.risk.max_positions == 5
        finally:
            _post(server, "/api/stop")
            time.sleep(0.3)

    def test_start_maxpos_negatif_ditolak(self, server):
        import scalping.web_ui as wu

        def fake_broker(mode, exchange):
            return PaperBroker(1000.0)

        def fake_data_fn(mode, exchange):
            return FakeData()

        with mock.patch.object(wu, "_build_broker", fake_broker), \
             mock.patch.object(wu, "_build_data", fake_data_fn):
            _, body = _post(server,
                             "/api/start?symbol=BTCUSDT&maxpositions=-2")
            d = json.loads(body)
            assert d["ok"] is False
            assert "maxpositions" in d["msg"]
