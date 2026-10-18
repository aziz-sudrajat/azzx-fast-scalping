# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""CLI simpel azzx-fast-scalping.

Contoh (gaya bebas, urutan fleksibel):
    python -m scalping HBARUSDT --margin 100 --lev 10 --mode paper --margin-mode isolated
    python -m scalping --margin 100 --lev 10 isolated paper HBARUSDT
    python -m scalping BTCUSDT -e bybit --margin 50 --lev 20 testnet

Exchange: -e binance|okx|bybit|bitget (default: binance).

Artinya: modal margin 100 USDT, leverage 10x -> posisi 1000 USDT,
qty dihitung otomatis dari harga live. Margin mode: isolated | cross.
Mode: paper | testnet | live (live butuh --i-understand).

API key via env (tidak pernah di argumen):
    testnet: BINANCE_TESTNET_KEY / BINANCE_TESTNET_SECRET
    live:    BINANCE_LIVE_KEY / BINANCE_LIVE_SECRET
"""
from __future__ import annotations

import sys

from . import config as cfg_mod
from .risk import RiskGuard
from .live_loop import (AlternatingTrader, _build_broker, _build_data,
                        _now_wib, fmt_price, paint)
from .exchanges import SUPPORTED, get_exchange


def parse_args(args: list[str]) -> dict:
    """Parser fleksibel: dukung flag (--margin 100) dan positional
    (isolated, paper, HBARUSDT) seperti contoh user."""
    out = {"symbol": "BTCUSDT", "margin": 100.0, "lev": 10,
           "mode": None, "margin_mode": "isolated", "exchange": "binance",
           "max_trades": 0, "i_understand": False}
    pos = []
    it = iter(args)
    for a in it:
        if a == "--margin":
            out["margin"] = float(next(it, "100"))
        elif a in ("--lev", "--leverage"):
            out["lev"] = int(next(it, "10"))
        elif a == "--mode":
            out["mode"] = next(it, None)
        elif a in ("--margin-mode", "--marginmode"):
            out["margin_mode"] = next(it, "isolated").lower()
        elif a == "--max-trades":
            out["max_trades"] = int(next(it, "0"))
        elif a in ("-e", "--exchange"):
            out["exchange"] = next(it, "binance").lower()
        elif a == "--i-understand":
            out["i_understand"] = True
        elif a.startswith("-"):
            continue  # flag tak dikenal -> abaikan
        else:
            pos.append(a)
    # positional: kenali isolated/cross, paper/testnet/live, sisanya symbol
    for p in pos:
        pl = p.lower()
        if pl in ("isolated", "cross", "crossed"):
            out["margin_mode"] = "isolated" if pl == "isolated" else "cross"
        elif pl in ("paper", "testnet", "live"):
            out["mode"] = pl
        else:
            out["symbol"] = p.upper()
    if out["margin"] <= 0:
        raise SystemExit("--margin harus > 0")
    if not 1 <= out["lev"] <= 125:
        raise SystemExit("--lev harus 1-125")
    if out["margin_mode"] not in ("isolated", "cross"):
        raise SystemExit("margin mode harus isolated/cross")
    try:
        get_exchange(out["exchange"])
    except ValueError as e:
        raise SystemExit(str(e))
    return out


def main(argv=None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))

    cfg = cfg_mod.load()
    if args["mode"]:
        cfg["MODE"] = args["mode"]
    mode = cfg["MODE"]
    if mode == "live" and not args["i_understand"]:
        raise SystemExit("mode live butuh --i-understand.")

    symbol = args["symbol"]
    exchange = args["exchange"]
    data = _build_data(mode, exchange)
    broker = _build_broker(mode, exchange)

    # --- pre-flight: validasi credential (gagal cepat sebelum order) ---
    if mode in ("testnet", "live"):
        try:
            bal = broker.get_balance()
            print(f"[{_now_wib()}] [{mode}] koneksi {exchange} OK, "
                  f"saldo wallet: {bal:.2f} USDT", flush=True)
        except Exception as e:
            raise SystemExit(f"[{mode}] credential {exchange} GAGAL: {e}\n"
                             f"cek API key/secret/passphrase di environment.")
        if mode == "live":
            print(paint("!" * 60, "red", "bold"), flush=True)
            print(paint("  !! MODE LIVE — UANG ASLI !!", "red", "bold"),
                  flush=True)
            print(paint(f"  {exchange} {symbol} | margin {args['margin']} USDT "
                        f"x{args['lev']} = {args['margin']*args['lev']:.2f} USDT notional",
                        "red", "bold"), flush=True)
            print(paint("!" * 60, "red", "bold"), flush=True)

    # --- set leverage & margin type (testnet/live via API; paper simulasi) ---
    if mode in ("testnet", "live"):
        try:
            r = broker.set_leverage(symbol, args["lev"])
            print(f"[{_now_wib()}] leverage {symbol} -> {args['lev']}x "
                  f"(respon: {r.get('leverage', '?')}x)", flush=True)
        except Exception as e:
            print(f"[{_now_wib()}] set leverage gagal: {e}", flush=True)
        try:
            mt = "ISOLATED" if args["margin_mode"] == "isolated" else "CROSSED"
            broker.set_margin_type(symbol, mt)
            print(f"[{_now_wib()}] margin mode {symbol} -> {mt}", flush=True)
        except Exception as e:
            print(f"[{_now_wib()}] set margin-type gagal (mungkin ada posisi "
                  f"terbuka): {e}", flush=True)
    else:
        broker.set_leverage(symbol, args["lev"])
        broker.set_margin_type(symbol, args["margin_mode"])
        print(f"[{_now_wib()}] [paper] leverage {args['lev']}x, "
              f"margin {args['margin_mode']}", flush=True)

    # --- qty dari margin x leverage ---
    price = None
    try:
        kl = data.get_klines(symbol, "1m", limit=2)
        if kl:
            price = float(kl[-1]["close"])
    except Exception as e:
        raise SystemExit(f"harga {symbol} gagal diambil (fail-closed): {e}")
    notional = args["margin"] * args["lev"]
    qty = notional / price
    print(f"[{_now_wib()}] {symbol} @ {fmt_price(price)} | margin {args['margin']} USDT "
          f"x{args['lev']} = notional {notional:.2f} USDT -> qty {qty:.6f} "
          f"| mode={mode} {args['margin_mode']}", flush=True)

    risk = RiskGuard()
    trader = AlternatingTrader(broker, data, cfg, risk)
    try:
        trader.run(symbol, qty, max_trades=args["max_trades"])
    except KeyboardInterrupt:
        print("\ndihentikan user.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
