# Auto Feeder

Automasi Neo Feeder PDDIKTI (NEO FEEDER 3.1 / DB 3.55) via REST API `/ws` menggunakan token sesi browser, tanpa dependency eksternal (Python stdlib only).

## Setup sekali saja

```bash
cp .env.example .env        # lalu isi FEEDER_TOKEN (dan FEEDER_BASE bila bukan localhost:8100)
# DevTools (F12) > Application > Local Storage > _token
# atau Console: localStorage.getItem("_token")
```

Token mati → semua script berhenti sendiri dengan pesan update `.env`. Lihat panduan lengkap di `TOKEN_GUIDE.md`.

## Web lokal

```bash
python3 web_app.py --host 127.0.0.1 --port 8765
# buka http://127.0.0.1:8765
python3 web_app.py --check     # cek token .env lalu keluar (exit 0 bila valid)
```

Token bisa diambil otomatis dari `.env` repo (`FEEDER_TOKEN`, opsional `FEEDER_BASE`): halaman koneksi menampilkan status valid/tidaknya token dan tombol "Hubungkan dengan token .env", jadi tidak perlu tempel token tiap kali. Token tidak pernah dikirim ke browser; form manual tetap tersedia sebagai cadangan. Masukkan alamat Feeder (mis. `localhost:8100` atau `https://feeder.kampus.ac.id`) dan token dari DevTools > Local Storage `_token`.

Token hanya tersimpan di memori server lokal. Menu: audit + ekspor CSV untuk kelas, TM & SKS dosen, nilai, AKM, aktivitas, konversi, skala nilai, lulus/keluar, mahasiswa (biodata), daya tampung, dan pemantauan nomor ijazah; operasi tulis (lulus/DO, biaya AKM, edit kelas, perbaikan TM/SKS, isi nilai konversi, ubah band skala nilai, edit biodata mahasiswa, tambah mahasiswa baru, ubah daya tampung) memakai alur template/unggah CSV -> preview -> terapkan dengan verifikasi baca-ulang. Nomor ijazah mengikuti sinkronisasi PISN, jadi menu itu hanya memantau.

Test: `python3 -B -m unittest test_web_app`. Arah desain: `DESIGN.md`.

## Script

| Script | Fungsi | Contoh |
|---|---|---|
| `mhslulus_bulk.py` | Bulk tambah lulus/DO dari CSV | `python3 mhslulus_bulk.py batch1.csv --go` |
| `mhs_check.py` | Tarik daftar mahasiswa (+ `--audit` biodata, `ref` lookup ID) | `python3 mhs_check.py --audit --limit 50` |
| `mhs_edit.py` | Edit biodata mahasiswa per baris CSV (allowlist) | `python3 mhs_edit.py data.csv --go` |
| `mhs_add.py` | Tambah mahasiswa baru (biodata + histori pendidikan) | `python3 mhs_add.py data.csv --go` |
| `mhslulus_ijazah_check.py` | Tarik lulusan tahun X tanpa nomor ijazah | `python3 mhslulus_ijazah_check.py --tahun 2026` |
| `mhslulus_ijazah_edit.py` | Isi nomor ijazah (dry-run; Feeder menolak jika sudah sync) | `python3 mhslulus_ijazah_edit.py data.csv` |
| `akm_check.py` | Cek AKM (semua TA aktif / `--nim`) | `python3 akm_check.py --nim 202500001` |
| `akm_biaya.py` | Update `biaya_smt` AKM dari CSV | `python3 akm_biaya.py batch_biaya.csv --go` |
| `kelaskuliah_check.py` | Tarik kelas TA aktif (+ `--kode`) | `python3 kelaskuliah_check.py --kode MK101` |
| `kelaskuliah_edit.py` | Edit nama kelas (`nm_kls`) per baris CSV | `python3 kelaskuliah_edit.py data.csv --go` |
| `kelas_tm_fix.py` | Audit + perbaiki TM & SKS dosen | `python3 kelas_tm_fix.py MK101 --kelas A --go` |
| `nilaikuliah_check.py` | Tarik nilai TA aktif (+ `--kode`) | `python3 nilaikuliah_check.py` |
| `komponen_evaluasi_check.py` | Audit komponen evaluasi kelas per kode MK (vs master) | `python3 komponen_evaluasi_check.py --mk MK101` |
| `komponen_evaluasi_fix.py` | Rapikan komponen KELAS + isi nilai = bobot per kode MK (dry-run; `--go`; `--kelas`; batch `--prefix`; sandbox default; pddikti perlu `--pddikti --confirm TULIS-PRODUKSI`) | `python3 komponen_evaluasi_fix.py --mk MK101 --go` |
| `master_evaluasi_fix.py` | Perbaiki MASTER rencana evaluasi MK paksa 15/35/10/10/15/15 (dry-run; `--go`; `--prefix`; pddikti perlu `--pddikti --confirm TULIS-PRODUKSI`) | `python3 master_evaluasi_fix.py --mk MK101 --go` |
| `aktivitasmhs_check.py` | Tarik aktivitas TA aktif (+ `--cari`) | `python3 aktivitasmhs_check.py --cari KKN` |
| `konversi_check.py` | Cek nilai konversi aktivitas TA aktif | `python3 konversi_check.py` |
| `bobotnilai_edit.py` | Baca + edit band huruf/indeks per prodi (dry-run; `--go` tulis) | `python3 bobotnilai_edit.py data.csv --go` |
| `konversi_isi.py` | Isi nilai konversi satu NIM atau batch CSV | `python3 konversi_isi.py --csv batch.csv --mk MK101 --jenis Skripsi --go` |
| `dayatampung_check.py` | Tarik daya tampung per prodi (+ `--semester`) | `python3 dayatampung_check.py` |
| `dayatampung_edit.py` | Edit daya tampung per baris CSV (allowlist 8 field) | `python3 dayatampung_edit.py data.csv --go` |
| `feeder_browser.py` | Cadangan browser visible (captcha manual) | — |
| `feeder_bulk.py` | Pengirim POST generik dari CSV (butuh `--path`) | `python3 feeder_bulk.py data.csv --path /mahasiswa/save --dry-run` |
| `feeder_client.py` | Koneksi Feeder per sesi web (normalisasi alamat, axios0) | `python3 feeder_client.py check` |
| `web_operations.py` | Operasi audit/tulis web dari script | `python3 web_operations.py check` |
| `web_app.py` | Server web lokal + UI | `python3 web_app.py --port 8765` |
| `test_web_app.py` | Test stdlib web + fake Feeder | `python3 -B -m unittest test_web_app` |

Semua: cek token dulu, TA aktif dinamis, default dry-run (kecuali script cek yang read-only).
Self-check offline: `python3 <script>.py check` (atau `demo`).

Catatan teknis menu **Rencana Evaluasi** & **Nilai Komponen** (endpoint, payload, aturan total bobot 100, komponen "asing" yang memblokir tulis): lihat `KOMPONEN_EVALUASI.md`.

## Aturan main

* **Token & output kerja tidak di-commit** — `.env`, `*.log`, `*.csv`, `*.xlsx`, `responses/`, `screenshots/` sudah masuk `.gitignore`.
* **Cek database sebelum tulis** — selalu uji di database `sandbox` lebih dulu sebelum menulis ke `pddikti` (produksi).
* Tulis (POST/PUT/DELETE): default dry-run + `--go`, verifikasi baca-ulang per baris.
* Baca (GET): langsung jalan, simpan CSV + ringkasan anomali.
* Lihat `POLA_SCRIPT.md` untuk pola baku script dan peta endpoint `/ws`.
