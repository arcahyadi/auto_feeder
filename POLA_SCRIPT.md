# Pola Baku Script & Peta Endpoint Automation Feeder

Single source of truth untuk pola script API dan endpoint Neo Feeder (`/ws`) yang sudah terpetakan.

## 1. Struktur wajib script
1. **Cek token dulu:** `GET /ws/mhslulus/filter`, pastikan mengandung `"list_status"`. Jika gagal, stop dan minta user perbarui `.env` tanpa menulis apa pun.
2. **Cek DB sebelum tulis:** `GET /ws/pengaturan/sandbox` -> wajib `sandbox` kecuali user memberi izin eksplisit untuk `pddikti` di permintaan itu.
3. **TA aktif dinamis:** `GET /ws/history_pendidikan/periode_aktif`, ambil `id_smt` pertama (`id_sms: null`). Untuk cek periode yang terbuka untuk edit per prodi, baca `/history_pendidikan/periode_aktif_all`.
4. **List + paginasi:** `GET <modul>/index` -> `[[rows], count]`, `rowsPerPage: 100`, loop sampai `< 100`. `setFilter.semester` wajib `[{"id_smt":..,"nm_smt":..}]` (ambil `nm_smt` dari `<modul>/filter`, atau `/kelas/filter` jika filter modul error).
5. **Tulis butuh objek form utuh:** POST/PUT kirim objek lengkap (`list_*`, `filter_*`, field masking) seperti browser; payload minimal dibalas `{}` diam-diam.
6. **Verifikasi:** tulis -> baca-ulang per baris; baca -> CSV + ringkasan anomali.

## 2. Serialisasi param (`axios0`)
Backend mengharapkan format **axios 0.x**, bukan brackets (`a[b]=1` selalu dibalas `{}`):
- objek nested -> JSON string: `pagination={"page":1,...}`
- array -> `key[]` berulang: `columnSearch[]=k.nm_kls&columnSearch[]=...`
- bool -> `true`/`false` lowercase
- `None` -> key di-skip

Fungsi rujukan: `axios0()` di `kelaskuliah_check.py` / `feeder_client.py`.

## 3. Peta endpoint terpetakan

| Modul | Baca (GET) | Tulis / Hapus (POST / PUT / DELETE) | Catatan |
|---|---|---|---|
| **Auth & DB** | `/pengaturan/sandbox`, `/user/filter`, `/user/waktu`, `/user/app_detail` | `POST /pengaturan/sandbox/change_db_connection` (`{"connection":"pddikti"\|"sandbox"}`), `POST /user/update_token` | `change_db_connection` tetap jalan meski DB sandbox error `3D000` |
| **Periode** | `/history_pendidikan/periode_aktif`, `/history_pendidikan/periode_aktif_all` | — | Sumber TA aktif & gate periode terbuka |
| **Mahasiswa** | `/mahasiswa/index` (`filter` server jalan), `/mahasiswa/detail/:id_pd` (70 field), `/mahasiswa/select/edit`, `/history_pendidikan/get/select`, `/history_pendidikan/index/:id_pd` | `PUT /mahasiswa/edit`, `POST /mahasiswa/add`, `POST /history_pendidikan/add` | Payload PUT/POST mahasiswa tidak butuh objek `user` |
| **Lulus / DO** | `/mhslulus/filter`, `/mhslulus/index`, `/mhslulus/cari_mahasiswa?nm_pd=` (`false` = sudah lulus) | `POST /mhslulus/add`, `PUT /mhslulus/edit/:id_reg_pd` | Nomor ijazah tertolak jika sudah sync |
| **AKM** | `/kuliahmhs/filter`, `/kuliahmhs/index`, `/kuliahmhs/cari_mahasiswa?nm_pd=` | `PUT /kuliahmhs/edit?id_reg_pd=&id_smt=` | `/kuliahmhs/filter` hanya punya status `A/C/M/U/N/G` (tanpa `L`) |
| **Kelas & Dosen** | `/kelas/filter`, `/kelas/index`, `/kelas/detail/:id_kls`, `/kelas/jumlah_jmlmhs/:id_kls`, `/kelas/jumlah_jmlsudahdinilai/:id_kls`, `/kelas/cari_dosen?nama=` | `PUT /kelas/edit/:id_kls`, `POST /kelas/add/dosen`, `PUT /kelas/edit/dosen/:id_ajar`, `DELETE /kelas/delete/dosen/:id_ajar`, `DELETE /kelas/delete/mahasiswa/:id_kls/:id_reg_pd`, `DELETE /kelas/delete/:id_kls` | Hapus mhs & dosen dulu sebelum hapus kelas; komponen evaluasi tidak memblokir |
| **Nilai & Bobot** | `/nilai/filter`, `/nilai/index`, `/nilai/detail/:id_kls`, `/bobotnilai/index` | `POST /nilai/update`, `PUT /bobotnilai/edit/:id` | Rencana evaluasi & nilai komponen: lihat `KOMPONEN_EVALUASI.md` |
| **Aktivitas Mhs** | `/aktmhs/index`, `/aktmhs/edit/:id` (`data`, `anggota[]`, `dosenpembimbing[]`, `dosenpenguji[]`), `/aktmhs/cari_dosen?nama=` | `POST /aktmhs/add`, `PUT /aktmhs/edit/:id`, `POST /aktmhs/addanggota`, `POST /aktmhs/adddosenpembimbing`, `DELETE /aktmhs/deleteanggota/:id_akt/:id_reg`, `DELETE /aktmhs/deletedosenpembimbing/:id_akt/:id_sdm`, `DELETE /aktmhs/delete/:id` | `/aktmhs/filter` error PG `0A000`; `addanggota` wajib `list_mahasiswa` + top-level `id_reg_pd`/`nipd`/`nm_pd` |
| **Konversi Nilai** | `/nilaiaktmhs/filter`, `/nilaiaktmhs/transfermatkul/:id_ang`, `/nilaiaktmhs/transfermatkuladd/:id_ang` | `POST /nilaiaktmhs/transfermatkul/add` | Template dari `transfermatkuladd/:id_ang` |
| **Daya Tampung** | `/dayatampung/filter`, `/dayatampung/index`, `/dayatampung/edit?id_sms=&id_smt=` | `PUT /dayatampung/edit?id_smt=&id_sms=` | Cek `periode_aktif_all` sebelum edit |

## 4. Aturan kode ringkas
- Stdlib only; token di `.env` (gitignored).
- Default dry-run + `--go` untuk tulis; arsip backup/respons di `responses/` (gitignored).
- Self-check offline: `python3 <script>.py check` (atau `demo`).
- Catat script baru 1 baris di `README.md` dalam commit yang sama.
