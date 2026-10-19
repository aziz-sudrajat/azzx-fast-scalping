# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Test Monkey Desk dashboard (scalping.web_ui) — stdlib only."""

import json
import threading
import urllib.parse
import urllib.request

import pytest

import scalping.web_ui as wu


@pytest.fixture(scope="module")
def server():
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


def test_index_has_monkey_desk_marker(server):
    status, body = _get(server, "/")
    assert status == 200
    text = body.decode("utf-8")
    assert "MONKEY DESK" in text
    assert "SAYA PAHAM" in text  # guard live ada di HTML


def test_api_state_keys(server):
    status, body = _get(server, "/api/state")
    assert status == 200
    d = json.loads(body)
    for key in ("mode", "symbol", "exchange", "strategy", "running",
                "balance", "session_pnl", "session_pnl_pct", "trades",
                "wins", "win_rate", "open_positions", "positions",
                "price", "klines", "equity", "desks", "updated"):
        assert key in d, f"key hilang: {key}"
    desks = d["desks"]
    for name in ("scanner", "engine", "executor", "risk"):
        assert name in desks, f"desk hilang: {name}"
        assert "status" in desks[name]
    assert d["mode"] == "paper"  # default
    assert d["trades"] == 0
    assert d["positions"] == []
    assert d["equity"] == []


def test_api_trades_positions_equity_log(server):
    for path, key in (("/api/trades", "trades"),
                      ("/api/positions", "positions"),
                      ("/api/equity", "equity"),
                      ("/api/log", None)):
        status, body = _get(server, path)
        assert status == 200, path
        d = json.loads(body)
        if key is None:
            assert isinstance(d, list)
        else:
            assert isinstance(d[key], list)


def test_live_guard_rejects_without_confirm(server):
    status, body = _post(server, "/api/start?mode=live&symbol=BTCUSDT"
                                 "&margin=100&lev=10&confirm=salah")
    assert status == 200
    d = json.loads(body)
    assert d["ok"] is False
    assert "SAYA PAHAM" in d["msg"]


def test_kill_switch_ok(server):
    status, body = _post(server, "/api/kill")
    assert status == 200
    d = json.loads(body)
    assert d["ok"] is True


def test_emit_updates_tiles():
    # unit: _emit memetakan event ke desk tiles tanpa server
    wu._emit({"type": "trade_open", "side": "LONG", "entry": 80000.0,
              "tp": 80100.0, "sl": 79900.0, "strength": 2,
              "reason": "test", "time": "t0"})
    assert wu._state["engine_tile"]["status"] == "open"
    assert wu._state["engine_tile"]["last_signal"]["side"] == "LONG"
    assert wu._state["exec_tile"]["status"] == "running"
    wu._emit({"type": "trade_close", "side": "LONG", "entry": 80000.0,
              "tp": 80100.0, "pnl": 5.0, "why": "TP", "time": "t1",
              "total_pnl": 5.0, "trades": 1, "wins": 1})
    assert len(wu._state["closed"]) == 1
    assert wu._state["closed"][0]["pnl"] == 5.0
    assert wu._state["equity"][-1]["eq"] == 5.0
    assert len(wu._state["log"]) == 1
    # bersihkan agar tidak bocor ke test lain
    wu._state["closed"] = []
    wu._state["equity"] = []
    wu._state["log"] = []
    wu._state["engine_tile"] = wu._tile("idle")
    wu._state["exec_tile"] = wu._tile("idle", mode="paper")


def test_robots_present_in_html(server):
    status, body = _get(server, "/")
    assert status == 200
    text = body.decode("utf-8")
    for rid in ("bot-scanner", "bot-engine", "bot-exec", "bot-risk"):
        assert 'id="%s"' % rid in text, "robot hilang: %s" % rid
        assert 'id="wrap-%s"' % rid.replace("bot-", "") in text, \
            "wrapper hilang: %s" % rid
    assert text.count('class="bot bot-') == 4
    for cls in (".bot-idle{", ".bot-walk{", ".bot-work{", ".bot-jump{",
                ".bot-alert{", ".bot-sleep{"):
        assert cls in text, "class animasi hilang: %s" % cls
    for kf in ("@keyframes bot-walk", "@keyframes bot-nod",
               "@keyframes bot-jump", "@keyframes bot-sleep",
               "@keyframes bot-shake", "@keyframes radar-spin",
               "@keyframes zzz", "@keyframes bot-blink"):
        assert kf in text, "keyframes hilang: %s" % kf
    assert "tileAge" in text and "setBot" in text


def test_tiles_have_epoch(server):
    status, body = _get(server, "/api/state")
    assert status == 200
    d = json.loads(body)
    for name in ("scanner", "engine", "executor", "risk"):
        assert "epoch" in d["desks"][name], "epoch hilang: %s" % name
        assert isinstance(d["desks"][name]["epoch"], (int, float))
