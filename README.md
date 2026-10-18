# azzx-fast-scalping ⚡

Tools scalping cepat untuk **futures** (Binance, OKX, Bybit, Bitget).
Scan coin terbaik → eksekusi otomatis dengan TP/SL → stop-loss darurat di exchange.

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
[07:43:13] strategi: SIGNAL (fleksibel, ikut arah sinyal)
[07:43:13] OPEN LONG GTCUSDT @ 0.238310 (TP 0.241125 / SL 0.236402) [strength 2/3: ...]
07:48:06 | LONG | Entry: 0.238310 | TP: 0.241125 | PnL: +2.17 [TP]
[07:48:06] tidak ada sinyal — menunggu 15.0s...
```

- 🟢 Hijau = profit / posisi LONG
- 🔴 Merah = loss / posisi SHORT
- `Ctrl+C` untuk berhenti

### Langkah 3 — Pantau lewat dashboard (opsional)

```bash
python -m scalping.webui
```

Buka http://127.0.0.1:8080 di browser — harga live, P&L sesi,
dan trade log berwarna yang update otomatis.

> Dashboard hanya untuk paper/testnet. Live trading hanya via CLI
> dengan flag `--i-understand`.

---

## 🧠 Dua Strategi

| Strategi | Flag | Cara kerja |
|---|---|---|
| **signal** (default) | `--strategy signal` | Entry **hanya bila ScalpEngine memberi sinyal** (bias 15m → trigger 1m → konfirmasi 5m → filter ATR/volume/spread). Setelah TP: evaluasi ulang — sinyal masih searah boleh re-entry, berbalik baru ikut, tidak ada sinyal = tunggu. **Tidak pernah memaksa arah.** |
| alternating | `--strategy alternating` | Paksa gantian LONG → SHORT → LONG tanpa melihat sinyal. Untuk eksekusi cepat / testing. |

```bash
# strategi signal (default) — fleksibel
python -m scalping GTCUSDT --margin 100 --lev 10 isolated paper

# strategi alternating — paksa gantian
python -m scalping GTCUSDT --margin 100 --lev 10 isolated paper --strategy alternating
```

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
| `--strategy` | `--strategy signal` | `signal` (default, fleksibel) atau `alternating` (paksa gantian) |
| `--max-trades` | `--max-trades 10` | Berhenti setelah N trade (default: jalan terus) |

Contoh lain:

```bash
# Bybit, margin 50 USDT, lev 20x, paper, strategi signal
python -m scalping BTCUSDT -e bybit --margin 50 --lev 20 --mode paper

# OKX testnet, strategi alternating
python -m scalping ETHUSDT -e okx --margin 100 --lev 5 testnet --strategy alternating

# Cross margin, berhenti setelah 5 trade
python -m scalping SOLUSDT --margin 200 --lev 10 cross paper --max-trades 5
```

---

## 🛡️ Mode Trading & Proteksi

| Mode | Uang asli? | Butuh apa? |
|---|---|---|
| `paper` | ❌ Tidak | Tidak butuh apa-apa. **Selalu mulai dari sini.** |
| `testnet` | ❌ Tidak | API key testnet/demo dari exchange |
| `live` | ✅ **YA** | API key asli + `--i-understand` |

### Proteksi yang selalu aktif (paper/testnet/live)

- **Stop-loss di exchange** — setiap open otomatis dipasang STOP_MARKET darurat
  di exchange (bukan cuma dipantau bot). Kalau bot mati/internet putus,
  posisi tetap terproteksi.
- **Risk guard** — batas rugi harian (default $100, reset otomatis tiap hari),
  cooldown 5 menit setelah loss, kill-switch.
- **Rekonsiliasi start** — bot menolak jalan bila ada posisi nyangkut dari
  run sebelumnya (harus ditutup manual dulu).
- **Qty otomatis di-quantize** ke LOT_SIZE exchange (tidak ditolak exchange).
- **Fail-closed** — gagal set leverage/margin, kredensial salah, atau data
  gagal = berhenti dengan pesan jelas, bukan jalan dengan setting salah.

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

**Q: Kenapa bot diam tidak entry-entry?**
A: Strategi default (`signal`) hanya entry bila ScalpEngine memberi sinyal yang
lolos semua filter (bias 15m, trigger 1m, konfirmasi 5m, filter ATR/volume/spread).
Tidak ada sinyal = tidak ada trade. Itu disengaja — lebih baik tidak entry
daripada entry jelek. Kalau mau aksi terus, pakai `--strategy alternating`.

**Q: Kenapa paper-ku lose terus?**
A: Kalau pakai `--strategy alternating`, bot eksekusi bergantian buta tanpa melihat
sinyal — itu untuk testing eksekusi, bukan strategi profit. Pakai strategi default
(`signal`) untuk entry berbasis sinyal.

**Q: Berapa fee-nya?**
A: Taker 0.05% per sisi (0.1% per round-trip). Dihitung otomatis di paper supaya realistis.

**Q: Bisa ganti TP/SL?**
A: Saat ini TP = 1.5× ATR, SL = 1× ATR, time-stop 15 menit (otomatis dari volatilitas coin).

**Q: Data harga dari mana?**
A: Langsung dari API publik masing-masing exchange (ticker real-time untuk entry/monitor,
klines 1m/5m/15m untuk sinyal). Tanpa API key untuk data.

**Q: Apakah aman untuk live?**
A: Sudah diaudit 3 agent independen (161 test hijau). Proteksi: stop-loss di exchange,
risk guard, rekonsiliasi, fail-closed. Tapi **risiko trading tetap ada** — mulai dari
margin kecil dan jangan pakai uang yang tidak siap hilang.

---

## ⚖️ Disclaimer

Tools ini untuk edukasi dan eksperimen. Trading futures ber-leverage **berisiko tinggi** —
kamu bisa kehilangan seluruh margin dalam hitungan menit. Penulis tidak bertanggung jawab
atas kerugian apa pun. DYOR. Mulai dari paper & testnet.

© 2026 aziz-sudrajat — All rights reserved.
