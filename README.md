# azzx-fast-scalping ⚡

Tools scalping cepat untuk **futures** (Binance, OKX, Bybit, Bitget).
Scan coin terbaik → eksekusi otomatis long/short bergantian dengan TP/SL.

![Demo](assets/demo.gif)

100% Python stdlib — tanpa dependensi eksternal. Mulai dari paper (simulasi, tanpa uang asli).

---

## 📦 Instalasi

```bash
# 1. Masuk folder project
cd azzx-fast-scalping

# 2. Buat virtual environment (disarankan)
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate          # Windows

# 3. Install (stdlib only — tidak mengunduh apa pun)
pip install -r requirements.txt
```

---

## 🚀 Tutorial: 3 Langkah

### Langkah 1 — Scan coin terbaik

```bash
python scan.py
```

Keluar tabel ranking coin yang paling cocok untuk scalping
(berdasar volume, volatilitas, dan momentum), plus rekomendasi:

```
🥇  GTCUSDT    0.23831    +23.8%    $105M    1.97%    91
🥈  BTWUSDT    2.3115     +18.1%    $283M    3.01%    90
...
⭐ Rekomendasi: GTCUSDT (skor 91)
```

### Langkah 2 — Jalankan bot (paper dulu!)

```bash
python -m scalping GTCUSDT --margin 100 --lev 10 isolated paper
```

Artinya: pakai **margin 100 USDT**, **leverage 10x** (= posisi 1000 USDT),
**isolated**, mode **paper** (simulasi, tanpa uang asli).

Contoh output:

```
[07:43:13] OPEN LONG GTCUSDT @ 0.238310 (TP 0.241125 / SL 0.236402)
07:48:06 | LONG | Entry: 0.238310 | TP: 0.241125 | PnL: +2.17 [TP]
[07:48:07] OPEN SHORT GTCUSDT @ 0.241125 (TP 0.238310 / SL 0.242980)
```

- 🟢 Hijau = profit / posisi LONG
- 🔴 Merah = loss / posisi SHORT
- Bot bergantian LONG → SHORT → LONG sampai kamu hentikan (`Ctrl+C`)

### Langkah 3 — Pantau lewat dashboard (opsional)

```bash
python -m scalping.webui
```

Buka http://127.0.0.1:8080 di browser — harga live, P&L sesi,
dan trade log berwarna yang update otomatis.

---

## 📖 Perintah Lengkap

```
python -m scalping [SYMBOL] [opsi]
```

| Opsi | Contoh | Keterangan |
|---|---|---|
| `--margin` | `--margin 100` | Modal margin dalam USDT |
| `--lev` | `--lev 10` | Leverage 1–125 |
| `isolated` / `cross` | `isolated` | Mode margin (bisa juga `--margin-mode isolated`) |
| `paper` / `testnet` / `live` | `paper` | Mode trading (bisa juga `--mode paper`) |
| `-e` | `-e bybit` | Exchange: `binance` (default), `okx`, `bybit`, `bitget` |
| `--max-trades` | `--max-trades 10` | Berhenti setelah N trade (default: jalan terus) |

Contoh lain:

```bash
# Bybit, margin 50 USDT, lev 20x, paper
python -m scalping BTCUSDT -e bybit --margin 50 --lev 20 --mode paper

# OKX testnet
python -m scalping ETHUSDT -e okx --margin 100 --lev 5 testnet

# Cross margin, berhenti setelah 5 trade
python -m scalping SOLUSDT --margin 200 --lev 10 cross paper --max-trades 5
```

---

## 🛡️ Mode Trading

| Mode | Uang asli? | Butuh apa? |
|---|---|---|
| `paper` | ❌ Tidak | Tidak butuh apa-apa. **Selalu mulai dari sini.** |
| `testnet` | ❌ Tidak | API key testnet/demo dari exchange |
| `live` | ✅ **YA** | API key asli + `--i-understand` |

### Testnet

Daftar API key testnet gratis di masing-masing exchange, lalu:

```bash
export BYBIT_KEY="..."
export BYBIT_SECRET="..."
python -m scalping BTCUSDT -e bybit --margin 50 --lev 10 testnet
```

### Live (uang asli) ⚠️

```bash
export BINANCE_LIVE_KEY="..."
export BINANCE_LIVE_SECRET="..."
python -m scalping BTCUSDT --margin 100 --lev 10 isolated live --i-understand
```

> ⚠️ **Peringatan:** mode live memakai **uang sungguhan**.
> Tools selalu menampilkan banner merah + cek saldo dulu sebelum order.
> Mulai dari margin kecil. Jangan pernah pakai uang yang tidak siap hilang.

---

## 🔑 API Key per Exchange

API key **hanya** via environment variable — tidak pernah ditulis di file/kode.

| Exchange | Environment variable |
|---|---|
| binance | `BINANCE_TESTNET_KEY` + `BINANCE_TESTNET_SECRET` (testnet), `BINANCE_LIVE_KEY` + `BINANCE_LIVE_SECRET` (live) |
| okx | `OKX_KEY`, `OKX_SECRET`, `OKX_PASSPHRASE` (tambah `OKX_DEMO=1` untuk demo) |
| bybit | `BYBIT_KEY`, `BYBIT_SECRET` |
| bitget | `BITGET_KEY`, `BITGET_SECRET`, `BITGET_PASSPHRASE` (live only — tidak ada testnet) |

---

## ❓ FAQ

**Q: Kenapa paper-ku lose terus?**
A: Mode default eksekusi **bergantian buta** (LONG → SHORT → LONG) tanpa melihat sinyal pasar.
Itu memang untuk eksekusi cepat, bukan strategi profit. Pakai `scan.py` untuk pilih coin
yang sedang aktif, dan pertimbangkan hasil paper sebagai data — bukan janji profit.

**Q: Berapa fee-nya?**
A: Taker 0.05% per sisi (0.1% per round-trip). Dihitung otomatis di paper supaya realistis.

**Q: Bisa ganti TP/SL?**
A: Saat ini TP = 1.5× ATR, SL = 1× ATR, time-stop 15 menit (otomatis dari volatilitas coin).

**Q: Data harga dari mana?**
A: Langsung dari API publik masing-masing exchange (klines 1m/15m). Tanpa API key untuk data.

---

## ⚖️ Disclaimer

Tools ini untuk edukasi dan eksperimen. Trading futures ber-leverage **berisiko tinggi** —
kamu bisa kehilangan seluruh margin dalam hitungan menit. Penulis tidak bertanggung jawab
atas kerugian apa pun. DYOR. Mulai dari paper & testnet.

© 2026 aziz-sudrajat — All rights reserved.
