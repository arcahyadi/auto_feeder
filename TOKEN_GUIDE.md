# Panduan Token Feeder (`.env`)

Token tetap di mesin lokal — jangan commit `.env` dan jangan bagikan token ke publik.

## 1. Ambil `_token` dari browser
1. Login ke Neo Feeder (`http://localhost:8100` atau URL Feeder kampus Anda, mis. `https://feeder.kampus.ac.id`).
2. Tekan `F12` (DevTools) -> tab **Application** -> **Local Storage** -> pilih origin Feeder -> salin nilai `_token`.
   - Cara cepat di Console: `localStorage.getItem("_token")`

## 2. Simpan ke `.env`
```bash
cp .env.example .env   # sekali saja jika .env belum ada
```
Isi `.env`:
```dotenv
FEEDER_TOKEN='tempel-token-di-sini'
# FEEDER_BASE='http://localhost:8100/ws'
```
(Jika tidak diatur, default `FEEDER_BASE` adalah `http://localhost:8100/ws`.)

## 3. Verifikasi token & status DB
```bash
python3 web_app.py --check
```
Exit `0` berarti token valid. Semua script membaca `.env` otomatis dan berhenti aman jika token mati.
