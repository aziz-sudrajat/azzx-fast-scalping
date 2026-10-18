# Copyright (c) 2026 aziz-sudrajat <azizsudrajat.stb4@gmail.com>
# https://github.com/aziz-sudrajat
#
# All rights reserved. This file is part of the azzx-fast-scalping project.
# Unauthorized copying, modification, distribution, or false claim of
# authorship is prohibited.

"""Screener coin terbaik untuk scalping (Binance Futures).

Jalankan langsung:  python scan.py [--top N] [--min-vol USD]

Kriteria "terbaik untuk scalping":
  - volume 24h besar (likuiditas, slippage kecil)
  - ATR% sehat (ada gerakan untuk di-capture, tidak dead)
  - momentum jelas (bergerak, bukan sideways mati)
  - volume trend naik (minat pasar masuk)

Output: tabel ranking berwarna + rekomendasi top pick.
"""
from __future__ import annotations

import math
import sys
import time
import urllib.request
import json

from .indicators import atr
from .live_loop import fmt_price, paint

BASE_URL = "https://fapi.binance.com"

_EXCLUDE_QUOTES = ("USDC", "FDUSD", "TUSD", "DAI", "EUR", "BRL")
_EXCLUDE_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
# base stablecoin / komoditas yang lolos filter quote (R6-Azz)
_EXCLUDE_BASE = ("USDD", "USDJ", "XAUT", "PAXG", "BFUSD")


def _http_get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "azzx-scan/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_tickers() -> list[dict]:
    """Semua ticker futures. Fail-closed: error -> SystemExit."""
    try:
        data = _http_get(f"{BASE_URL}/fapi/v1/ticker/24hr")
    except Exception as e:
        raise SystemExit(f"scan gagal ambil ticker: {e}")
    if not isinstance(data, list):
        raise SystemExit("respons ticker tak dikenal")
    return data


def pick_candidates(tickers: list[dict], min_quote_vol: float,
                    limit: int) -> list[dict]:
    """Filter USDT perp yang layak: volume cukup, bukan stable/leveraged."""
    out = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        if sym.endswith(_EXCLUDE_SUFFIX):
            continue
        base = sym[:-4]
        if base in _EXCLUDE_QUOTES or base in _EXCLUDE_BASE:
            continue
        try:
            qv = float(t.get("quoteVolume", "0"))
            pc = float(t.get("priceChangePercent", "0"))
            last = float(t.get("lastPrice", "0"))
        except (TypeError, ValueError):
            continue
        if qv < min_quote_vol or last <= 0:
            continue
        out.append({"symbol": sym, "quote_vol": qv,
                    "change_pct": pc, "price": last})
    out.sort(key=lambda r: r["quote_vol"], reverse=True)
    return out[:limit]


def fetch_klines_15m(symbol: str, limit: int = 100) -> list[dict]:
    url = (f"{BASE_URL}/fapi/v1/klines?symbol={symbol}"
           f"&interval=15m&limit={limit}")
    raw = _http_get(url)
    out = []
    for row in raw:
        out.append({"open_time": row[0], "open": float(row[1]),
                    "high": float(row[2]), "low": float(row[3]),
                    "close": float(row[4]), "volume": float(row[5]),
                    "close_time": row[6]})
    return out[:-1]  # buang candle berjalan


def analyze(symbol: str) -> dict | None:
    """Hitung metrik scalping untuk satu symbol. None bila data kurang."""
    try:
        kl = fetch_klines_15m(symbol)
    except Exception:
        return None
    if len(kl) < 30:
        return None
    closes = [k["close"] for k in kl]
    vols = [k["volume"] for k in kl]

    atr_vals = atr(kl, 14)
    atr_now = atr_vals[-1]
    if atr_now is None or closes[-1] <= 0:
        return None
    atr_pct = atr_now / closes[-1] * 100

    # momentum: % change selama periode klines
    momentum = (closes[-1] - closes[0]) / closes[0] * 100
    # volume trend: rata-rata 10 terakhir vs 10 sebelumnya
    recent_vol = sum(vols[-10:]) / 10
    prev_vol = sum(vols[-20:-10]) / 10 if len(vols) >= 20 else recent_vol
    vol_trend = (recent_vol / prev_vol) if prev_vol > 0 else 1.0

    return {"atr_pct": atr_pct, "momentum": momentum,
            "vol_trend": vol_trend, "price": closes[-1]}


def score(cand: dict, metrics: dict) -> float:
    """Skor 0-100. Bobot: volume 30, volatilitas 30, momentum 25, vol trend 15."""
    # volume: skala log, $5M -> ~40, $100M -> ~75, $1B -> ~95
    vol_score = min(100.0, max(0.0, 20 + 25 * math.log10(cand["quote_vol"] / 1e6 + 1)))
    # volatilitas: sweet spot ATR% 0.3 - 2.0 (15m)
    a = metrics["atr_pct"]
    if a < 0.1:
        volat_score = a * 200  # 0.05% -> 10
    elif a <= 2.0:
        volat_score = 60 + (a - 0.1) / 1.9 * 40
    else:
        volat_score = max(20.0, 100 - (a - 2.0) * 15)  # terlalu liar -> turun
    # momentum: |change| 2-8% ideal
    m = abs(metrics["momentum"]) + abs(cand["change_pct"]) / 4
    mom_score = min(100.0, m * 12)
    # volume trend: >1 = minat masuk
    vt = metrics["vol_trend"]
    trend_score = min(100.0, max(0.0, (vt - 0.5) * 100))
    return (vol_score * 0.30 + volat_score * 0.30
            + mom_score * 0.25 + trend_score * 0.15)


def _fmt_vol(qv: float) -> str:
    if qv >= 1e9:
        return f"${qv/1e9:.1f}B"
    if qv >= 1e6:
        return f"${qv/1e6:.0f}M"
    return f"${qv/1e3:.0f}K"


def scan(top_n: int = 20, min_vol: float = 5_000_000.0,
         analyze_limit: int = 40, quiet: bool = False) -> list[dict]:
    """Scan penuh. Return list ranking (skor desc)."""
    tickers = fetch_tickers()
    cands = pick_candidates(tickers, min_vol, analyze_limit)
    if not quiet:
        print(f"menganalisis {len(cands)} coin teratas by volume...", flush=True)
    results = []
    for i, cand in enumerate(cands):
        m = analyze(cand["symbol"])
        if m is None:
            continue
        s = score(cand, m)
        results.append({**cand, **m, "score": s})
        if not quiet and (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(cands)}...", flush=True)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]


def print_table(results: list[dict]) -> None:
    print()
    print(paint(f"{'#':<3} {'SYMBOL':<14} {'HARGA':<12} {'24H%':>8}  "
                f"{'VOL 24H':>9}  {'ATR%':>6}  {'SKOR':>5}", "bold", "cyan"))
    print(paint("-" * 66, "dim"))
    for i, r in enumerate(results, 1):
        chg = r["change_pct"]
        chg_s = f"{chg:+.1f}%"
        chg_c = "green" if chg >= 0 else "red"
        score_c = "green" if r["score"] >= 70 else "yellow" if r["score"] >= 50 else "dim"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}")
        plain = (f"{medal:<3} {r['symbol']:<14} {fmt_price(r['price']):<12} "
                 f"{chg_s:>8}  {_fmt_vol(r['quote_vol']):>9}  "
                 f"{r['atr_pct']:>5.2f}%  {r['score']:>5.0f}")
        print(paint(plain, score_c))
    print()
    if results:
        best = results[0]
        print(paint(f"⭐ Rekomendasi: {best['symbol']} (skor {best['score']:.0f}) — "
                    f"jalankan: python -m scalping {best['symbol']} "
                    f"--margin 100 --lev 10 --mode paper --margin-mode isolated",
                    "green", "bold"))


def main(argv=None) -> int:
    args = list(argv or sys.argv[1:])
    top_n, min_vol = 20, 5_000_000.0
    it = iter(args)
    for a in it:
        if a == "--top":
            top_n = int(next(it, "20"))
        elif a == "--min-vol":
            min_vol = float(next(it, "5000000"))
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
    t0 = time.time()
    print(paint("🔍 scan coin terbaik untuk scalping (Binance Futures)...",
                "cyan", "bold"), flush=True)
    results = scan(top_n=top_n, min_vol=min_vol)
    print_table(results)
    print(paint(f"selesai dalam {time.time()-t0:.1f}s", "dim"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
