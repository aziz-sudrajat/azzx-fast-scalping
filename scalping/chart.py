"""Render trading chart sebagai file SVG — stdlib saja (tanpa matplotlib).

Menggambar candlestick (hijau/merah), dua garis EMA (fast=kuning,
slow=ungu), marker sinyal B/S (segitiga biru/pink), dan panel volume.
"""

from __future__ import annotations

import html

# Dimensi kanvas
W, H = 900, 600
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 60, 30, 40, 30
# Panel volume menempati porsi bawah area plot
VOLUME_PANEL_RATIO = 0.20

UP_COLOR = "#26a69a"     # hijau  (close >= open)
DOWN_COLOR = "#ef5350"   # merah  (close < open)
EMA_FAST_COLOR = "#ffd60a"  # kuning
EMA_SLOW_COLOR = "#9d4edd"  # ungu
BUY_COLOR = "#3a86ff"    # biru  (marker B)
SELL_COLOR = "#ff5d8f"   # pink  (marker S)
GRID_COLOR = "#2a2a3a"
TEXT_COLOR = "#c9c9d4"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def render_chart(
    candles: list[dict],
    ema_fast: list[float | None],
    ema_slow: list[float | None],
    signals: list[dict],
    path: str,
    title: str = "",
) -> str:
    """Render chart ke file SVG di ``path`` dan return ``path``.

    candles: list dict dengan kunci "open", "high", "low", "close",
             "volume", "open_time".
    signals: list dict {"index": int, "side": "long"|"short",
             "strength": int 1..3} → label B1-B3 / S1-S3.

    Raise ValueError jika data kosong (fail-closed).
    """
    if not candles:
        raise ValueError("render_chart: candles kosong — tidak ada data untuk digambar")

    n = len(candles)

    # Validasi minimal tiap candle
    for i, c in enumerate(candles):
        for k in ("open", "high", "low", "close", "volume"):
            if k not in c or c[k] is None:
                raise ValueError(f"render_chart: candle #{i} kehilangan kunci '{k}'")

    # Koordinat area plot
    plot_x0, plot_x1 = MARGIN_LEFT, W - MARGIN_RIGHT
    plot_y0, plot_y1 = MARGIN_TOP, H - MARGIN_BOTTOM
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    vol_h = plot_h * VOLUME_PANEL_RATIO
    price_y0 = plot_y0                      # atas panel harga
    price_y1 = plot_y1 - vol_h - 8           # bawah panel harga
    vol_y0 = price_y1 + 8                   # atas panel volume
    vol_y1 = plot_y1                        # bawah panel volume

    # Skala harga: gabungkan high/low candle + nilai EMA non-None
    lo = min(c["low"] for c in candles)
    hi = max(c["high"] for c in candles)
    ema_vals = [v for v in list(ema_fast) + list(ema_slow) if v is not None]
    if ema_vals:
        lo = min(lo, min(ema_vals))
        hi = max(hi, max(ema_vals))
    if hi == lo:  # harga datar — beri padding agar tidak divide-by-zero
        hi, lo = hi + 1.0, lo - 1.0
    pad = (hi - lo) * 0.05
    hi, lo = hi + pad, lo - pad

    max_vol = max((c["volume"] for c in candles), default=0.0)
    if max_vol <= 0:
        max_vol = 1.0

    def px(i: int) -> float:
        return plot_x0 + (i + 0.5) * (plot_w / n)

    def py(p: float) -> float:
        return price_y0 + (hi - p) / (hi - lo) * (price_y1 - price_y0)

    def vy(v: float) -> float:
        return vol_y1 - (v / max_vol) * (vol_y0 and (vol_y1 - vol_y0) or 0)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="monospace">'
    )
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#14141c"/>')

    if title:
        parts.append(
            f'<text x="{W/2:.0f}" y="24" text-anchor="middle" font-size="16" '
            f'fill="{TEXT_COLOR}">{_esc(title)}</text>'
        )

    # Grid horizontal harga (5 garis)
    for g in range(6):
        y = price_y0 + g * (price_y1 - price_y0) / 5
        price = hi - g * (hi - lo) / 5
        parts.append(
            f'<line x1="{plot_x0}" y1="{y:.1f}" x2="{plot_x1}" y2="{y:.1f}" '
            f'stroke="{GRID_COLOR}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{plot_x0 - 6}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="{TEXT_COLOR}">{price:.2f}</text>'
        )

    body_w = max(1.0, (plot_w / n) * 0.7)

    # Volume bars
    for i, c in enumerate(candles):
        x = px(i)
        col = UP_COLOR if c["close"] >= c["open"] else DOWN_COLOR
        y_top = vy(c["volume"])
        parts.append(
            f'<rect x="{x - body_w/2:.1f}" y="{y_top:.1f}" '
            f'width="{body_w:.1f}" height="{vol_y1 - y_top:.1f}" '
            f'fill="{col}" fill-opacity="0.45"/>'
        )

    # Candlestick
    for i, c in enumerate(candles):
        x = px(i)
        col = UP_COLOR if c["close"] >= c["open"] else DOWN_COLOR
        parts.append(
            f'<line x1="{x:.1f}" y1="{py(c["high"]):.1f}" x2="{x:.1f}" '
            f'y2="{py(c["low"]):.1f}" stroke="{col}" stroke-width="1.5"/>'
        )
        y_o, y_c = py(c["open"]), py(c["close"])
        y_top, y_bot = min(y_o, y_c), max(y_o, y_c)
        if y_bot - y_top < 1.0:  # doji — pastikan tetap terlihat
            y_bot = y_top + 1.0
        parts.append(
            f'<rect x="{x - body_w/2:.1f}" y="{y_top:.1f}" '
            f'width="{body_w:.1f}" height="{y_bot - y_top:.1f}" fill="{col}"/>'
        )

    # Garis EMA — putus di nilai None
    def polyline(values: list, color: str, width: float) -> None:
        seg: list[str] = []
        for i, v in enumerate(values):
            if v is None:
                if seg:
                    parts.append(
                        f'<polyline points="{" ".join(seg)}" fill="none" '
                        f'stroke="{color}" stroke-width="{width}"/>'
                    )
                    seg = []
                continue
            seg.append(f"{px(i):.1f},{py(v):.1f}")
        if seg:
            parts.append(
                f'<polyline points="{" ".join(seg)}" fill="none" '
                f'stroke="{color}" stroke-width="{width}"/>'
            )

    polyline(list(ema_fast), EMA_FAST_COLOR, 1.8)
    polyline(list(ema_slow), EMA_SLOW_COLOR, 1.8)

    # Marker sinyal: segitiga B (di bawah low) / S (di atas high)
    for s in signals:
        idx = int(s["index"])
        if not (0 <= idx < n):
            continue
        side = str(s.get("side", "")).lower()
        strength = max(1, min(3, int(s.get("strength", 1))))
        x = px(idx)
        tri = 8.0
        if side == "long":
            y_tip = py(candles[idx]["low"]) + tri + 2
            label = f"B{strength}"
            parts.append(
                f'<polygon points="{x:.1f},{y_tip:.1f} '
                f'{x - tri:.1f},{y_tip + tri:.1f} {x + tri:.1f},{y_tip + tri:.1f}" '
                f'fill="{BUY_COLOR}"/>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{y_tip + tri + 12:.1f}" text-anchor="middle" '
                f'font-size="11" font-weight="bold" fill="{BUY_COLOR}">{label}</text>'
            )
        elif side == "short":
            y_tip = py(candles[idx]["high"]) - tri - 2
            label = f"S{strength}"
            parts.append(
                f'<polygon points="{x:.1f},{y_tip:.1f} '
                f'{x - tri:.1f},{y_tip - tri:.1f} {x + tri:.1f},{y_tip - tri:.1f}" '
                f'fill="{SELL_COLOR}"/>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{y_tip - tri - 4:.1f}" text-anchor="middle" '
                f'font-size="11" font-weight="bold" fill="{SELL_COLOR}">{label}</text>'
            )

    # Label sumbu
    parts.append(
        f'<text x="{plot_x0}" y="{H - 8}" font-size="10" fill="{TEXT_COLOR}">'
        f'harga</text>'
    )
    parts.append(
        f'<text x="{plot_x0}" y="{vol_y0 + 10}" font-size="10" fill="{TEXT_COLOR}">'
        f'volume</text>'
    )
    parts.append("</svg>")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    return path
