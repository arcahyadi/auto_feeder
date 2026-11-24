# Komponen Evaluasi & Nilai Komponen (catatan reverse-engineer)

Catatan ini hasil eksplorasi API Feeder untuk menu **Rencana Evaluasi** dan
**Nilai Komponen**. Semua diuji di **sandbox** (2026-10-01). Simpan untuk
rujukan script/web berikutnya.

## Istilah & halaman

| Halaman | Isi |
|---|---|
| `#/kelas/detail/{id_kls}` tab **Rencana Evaluasi** | struktur komponen kelas (Basis, Komponen, Bobot) |
| `#/nilai/detail/{id_kls}` tombol **Edit** | nilai komponen per mahasiswa + nilai akhir |
| `#/list_matkul/detail/{id_mk}` tab **Rencana Evaluasi** | master rencana evaluasi MK |

**Jenis evaluasi (`id_jns_eval`)** — dari `GET /kelas/filter` → `list_jenisevaluasi`:

| id | nm_jns_eval | Slot UI |
|---|---|---|
| 1 | Evaluasi Akademik | (tidak dipakai) |
| 2 | Aktivitas Partisipatif | `nilai_aktivitas_partisipatif` |
| 3 | Hasil Proyek | `nilai_hasil_proyek` |
| 4 | Kognitif/ Pengetahuan | tergantung `nama`: `TGS`/`QIZ`/`UTS`/`UAS` |

Nama pendek di UI: `TGS`→"Tugas", `QIZ`→"Quiz", `UTS`→"Ujian Tengah
Semester", `UAS`→"Ujian Akhir Semester".

## Endpoint

| Fungsi | Endpoint | Catatan |
|---|---|---|
| Baca komponen kelas | `GET /kelas/editrencanaevaluasi/{id_kls}` | list; field `id_komp_eval`, `id_jns_eval`, `nama`, `bobot`, `urutan` |
| Baca komponen master MK | `GET /matakuliah/editrencanaeval/{id_mk}` | field `id_re_mk` (bukan `id_komp_eval`) |
| Tambah komponen kelas | `POST /kelas/addrencanaevaluasi` | body = **array**; item baru ditandai `"tambah":"oke"` |
| Ubah bobot komponen | `PUT /kelas/editrencanaevaluasi` | body = **array semua baris** existing |
| Hapus komponen | `DELETE /kelas/deletekomponenevaluasi?id_kls=&id_jns_eval=&nama=&id_komp_eval=` | by `nama` (baris bernama) **atau** by `id_komp_eval` (baris kosong); lihat "Cara hapus baris komponen KELAS" |
| Salin master → kelas | `GET /kelas/salinrencanaevaluasi/{id_kls}/{id_mk}` | hanya jalan bila kelas **kosong** |
| Baca nilai 1 komponen 1 mhs | `GET /kelas/nilaikomeval/{id_komp_eval}/{id_reg_pd}` | `[]` = kosong |
| Tulis nilai komponen | `POST /nilai/update` | body = **array mahasiswa** (seluruh kelas) |
| Nilai akhir kelas | `GET /nilai/detail/{id_kls}` | `data.mahasiswa[]` (`nilai_angka`, `nilai_huruf`) |

## Aturan penting (hasil uji)

1. **Total bobot payload harus tepat 100.** `PUT`/`POST addrencanaevaluasi`
   menolak dengan `402` bila kurang/lebih ("Bobot tidak diperbolehkan kurang
   dari 100" / "lebih dari 100").
2. **`PUT` hanya menerapkan `bobot` (+ `nama_inggris`).** `id_jns_eval`,
   `nama`, `urutan` diabaikan — ubah struktur = hapus + tambah.
3. **`POST /nilai/update` menyimpan nilai akhir DAN nilai komponen.** Tiap
   mahasiswa dikirim dengan pasangan `id_komp_eval_nilai_<field>` +
   `nilai_<field>` per slot (lihat payload di bawah). Nilai komponen **hanya
   tersimpan bila tidak ada komponen "asing"** (lihat #5).
4. **Nilai komponen tidak bisa dikosongkan lewat API.** Kirim `""`/`null`
   diabaikan; hanya angka yang tersimpan. Kosongkan manual di UI.
5. **Komponen "asing" memblokir seluruh penulisan nilai.** Contoh: baris
   `id_jns_eval=4, nama="Hasil Proyek"` (seharusnya jns3) tidak punya slot
   UI; selama baris itu ada **dan berisi nilai**, semua `POST /nilai/update`
   dibalas `200 "Update Sukses"` tapi tidak ada yang tersimpan. Solusinya:
   lepas baris asing itu dulu (lihat resep), baru tulis nilai.
6. **`DELETE` menolak komponen yang nilainya terisi**:
   *"Nilai Komponen Ubah Jadi 0 ada data yang masih terisi"* (402).
   Namun `DELETE` **menghapus berdasarkan `nama`** — bila ada ≥2 baris bernama
   sama, memanggil DELETE pada salah satunya ikut menghapus baris lain yang
   bernama sama (termasuk yang masih berisi nilai). Trik "buat baris kembar
   lalu hapus" memanfaatkan ini untuk melepas komponen asing yang nilainya
   terisi.
7. **Menghapus komponen tidak menghapus nilai akhir mahasiswa.** Nilai akhir
   (`/nilai/detail`) tetap utuh; hanya nilai komponen yang terlepas.
8. **Kelas lama bisa beda dari master.** Kelas dibuat dari template bawaan
   Feeder (versi template berubah antar waktu), master MK diisi terpisah,
   **tidak ada sinkronisasi otomatis**. Kelas baru + "Salin master" = struktur
   master; kelas lama harus dirapikan manual.
9. **`POST /kelas/addrencanaevaluasi` = HANYA baris baru, dan total bobot
   payload harus = 100.** Kirim **satu** baris baru dengan `bobot: 100`
   (baris baru tidak menambah total tersimpan). Berbobot 0 → `402`
   ("Bobot tidak diperbolehkan kurang dari 100") **tanpa** membuat baris.
   Menyertakan baris existing → baris existing **tergandakan** (jangan).
10. **`DELETE` by `id_komp_eval` HANYA menghapus baris yang nilainya kosong.**
    Baris berisi nilai dibalas `402 "... masih terisi"` walau pakai `id_komp_eval`.
    Untuk baris **nama kosong** (jns2/jns3) yang nilainya kosong, DELETE dengan
    **params lengkap** (`id_kls`+`id_jns_eval`+`nama`+`id_komp_eval`) **bisa**
    menghapus (server lambat → retry + verifikasi). DELETE dengan **hanya
    `id_komp_eval`** = `200` tapi no-op.
11. **`PUT` flaky**: kadang hanya menerapkan sebagian baris. Ulangi (baca-ulang
    → kirim ulang) sampai total bobot terbaca = 100 dan jumlah baris sesuai.

## Cara hapus baris komponen KELAS (ringkas, hasil uji 2026-10-07)

Baris komponen kelas **tidak bisa** dihapus dari UI, tapi **bisa** via API —
pilih metode sesuai kondisi:

| Kondisi baris | Metode | Catatan |
|---|---|---|
| Kosong (tak ada nilai) | `DELETE /kelas/deletekomponenevaluasi` params **lengkap** + retry | Bekerja juga untuk `nama` kosong (jns2/jns3) |
| **Berisi nilai** | **Trik kembar** (POST baris kembar `bobot:100` + `tambah:oke`, lalu DELETE by-nama) | by-id pasti `402` |
| Berisi nilai **& tanpa nama** | ❌ tak bisa via API | lapor perlu-manual |

`komponen_evaluasi_fix.py` memilih otomatis: `baris_ada_nilai()` → kalau ada
nilai langsung trik kembar; kalau kosong coba by-id, fallback trik kembar.
Trik kembar butuh **total bobot kelas = 100** dulu (syarat POST add).

> **Server lambat (eventual-consistent).** Operasi DELETE kadang baru terlihat
> beberapa saat kemudian → **verifikasi bisa melaporkan gagal padahal sudah
> berhasil**. Jalur hapus memakai **polling bertahap** (0.3→2.0 s) & cek `id`
> hilang, bukan jeda tetap. Bila tetap gagal, jalankan ulang script
> (idempotent).

**Bug diperbaiki (2026-10-07):**
- `_kembar_dan_hapus` dulu mengukur keberhasilan lewat "jumlah baris **asing**
  berkurang" → **selalu gagal** untuk baris jns2/jns3 bernama (bukan asing).
  Kini mengukur lewat **jumlah total baris** berkurang.
- `bersihkan_nama_salah` kini **prioritas hapus baris bernama yang nilainya
  kosong** (by-id, cepat) dulu, baru yang berisi nilai (trik kembar) — supaya
  tak mentok di baris berisi nilai.

### Buang slot ganda (dedupe jns2/jns3) — 2026-10-07
Kalau kelas disalin dari **master duplikat**, struktur bisa punya slot ganda
(mis. jns2/jns3 beberapa kali). `hapus_slot_ganda()` menyisakan **satu baris
per slot** via `DELETE` by-`id_komp_eval` (baris ganda nama kosong) — terbukti
bisa (uji SF24665-A: 7 → 6 slot bersih). Urutan di `fix_kelas`:
**lepas asing → bersih nama jns2/jns3 → buang slot ganda → naikkan total →
tambah hilang → PUT bobot**.

**Kelas dari master duplikat tetap bisa dirapikan** (terbukti SF24665, DF244219,
DF244220). Tapi target struktur **ikut master yang duplikat** (bukan
15/35/10/10/15/15) — jadi master-nya tetap perlu dibereskan manual.

## Kesalahan yang pernah bikin kacau (JANGAN diulang)

Dicatat dari kejadian nyata (SF24217 A rusak lalu dipulihkan). Baca sebelum
menulis komponen.

1. **`POST /kelas/addrencanaevaluasi` = HANYA baris baru, total payload wajib 100.**
   Mengirim `[existing...] + [baris_baru]` membuat server **menggandakan** baris
   existing. Mengirim baris baru berbobot 0 juga gagal (`402`, tak dibuat).
   → **Kirim hanya baris baru dengan `bobot: 100`.**
2. **Loop `DELETE` pada baris `nama` kosong (tanpa `id_komp_eval`).** Dulu
   dianggap tak bisa. Kini terbukti **bisa** dengan `id_komp_eval` + params
   lengkap + retry; DELETE **tanpa** `id_komp_eval` tetap no-op. Lihat "Cara
   hapus baris komponen KELAS".
3. **Mengira `PUT` menerapkan struktur.** `PUT /kelas/editrencanaevaluasi`
   **hanya** menerapkan `bobot` (+ `nama_inggris`). `id_jns_eval`/`nama`/
   `urutan` diabaikan. Perubahan struktur wajib lewat hapus + tambah.
4. **Mengharap `POST add` selalu membuat baris.** Kadang balas `402` **tanpa**
   membuat baris (flaky). → Cek jumlah baris setelah setiap POST; bila belum
   bertambah, ulangi (urutan `[baris_baru] + [existing]` lebih andal, tapi
   **tetap hanya kirim baris baru**, jangan existing — lihat #1).
5. **Menganggap `DELETE` 200 = terhapus.** Selalu baca-ulang jumlah baris
   setelah DELETE. `402 "Nilai Komponen Ubah Jadi 0 ada data yang masih
   terisi"` = baris masih pegang nilai; `200` pada nama kosong = no-op.
6. **`POST /nilai/update` balas `200 "Update Sukses"` padahal tidak tersimpan.**
   Terjadi bila ada komponen asing (aturan #5). Jangan percaya status code —
   selalu verifikasi baca-ulang nilai komponen.

**Cara aman (ringkas):**
- Backup dulu (komponen + nilai akhir + nilai komponen tiap mhs).
- Ubah bobot: `PUT` array **penuh** (total harus 100), ulangi bila flaky.
- Tambah baris: `POST` **hanya baris baru**; cek jumlah baris bertambah.
- Hapus baris: `DELETE` pada baris **bernama**; verifikasi jumlah turun.
- Tulis nilai: `POST /nilai/update` array penuh; verifikasi tiap sel.

## Payload `POST /nilai/update`

Body = array objek mahasiswa (satu per mhs di kelas). Tiap objek: seluruh
field dari `GET /nilai/detail` + pasangan field komponen:

```json
{
  "id_kls": "...", "id_reg_pd": "...", "id_sms": "...",
  "nilai_angka": "82.0000", "nilai_huruf": "A", "nilai_indeks": "A#4,00",
  "canUpdateevaluasi": true,

  "id_komp_eval_nilai_aktivitas_partisipatif": "<uuid jns2>",
  "nilai_aktivitas_partisipatif": "15",

  "id_komp_eval_nilai_hasil_proyek": "<uuid jns3>",
  "nilai_hasil_proyek": "35",

  "id_komp_eval_nilai_quiz": "<uuid jns4 QIZ>",
  "nilai_quiz": "10",

  "id_komp_eval_nilai_tugas": "<uuid jns4 TGS>",
  "nilai_tugas": "10",

  "id_komp_eval_nilai_ujian_tengah_semester": "<uuid jns4 UTS>",
  "nilai_ujian_tengah_semester": "15",

  "id_komp_eval_nilai_ujian_akhir_semester": "<uuid jns4 UAS>",
  "nilai_ujian_akhir_semester": "15"
}
```

## Resep merapikan kelas lama (terbukti: SF23667 A & B, SF24211/16/17/19/20 A, sandbox)

Kasus khas: kelas punya komponen **jns4 `nama="Hasil Proyek"`** (asing, tanpa
slot UI) yang nilainya terisi, total bobot ≠ 100.

> **Syarat `POST /kelas/addrencanaevaluasi`: total bobot payload harus = 100.**
> Karena POST = **hanya baris baru**, kirim baris baru dengan `bobot: 100`
> (tidak menambah total tersimpan). Bila payload hanya baris baru berbobot 0,
> server balas `402` **tanpa** membuat baris. Trik: naikkan total kelas ke 100
> dulu, lalu POST baris baru berbobot 100.

1. **Cek DB**: `GET /pengaturan/sandbox` harus `sandbox` (jangan `pddikti`
   tanpa izin eksplisit user).
2. **Backup**: komponen + nilai akhir + nilai komponen tiap mhs.
3. **Naikkan total bobot ke 100 dulu** lewat `PUT /kelas/editrencanaevaluasi`
   (set bobot target ke baris existing; ulangi sampai terbaca 100). Tanpa ini,
   `POST add` untuk baris kembar akan ditolak.
4. **Buat baris kembar**: `POST /kelas/addrencanaevaluasi` dengan payload
   **hanya 1 baris baru** ber-`nama` sama dengan komponen asing, `bobot: 100`,
   `"tambah":"oke"` — **jangan** ikutkan baris existing (bikin ganda). Cek
   jumlah baris bertambah.
5. **Hapus by-nama**: `DELETE /kelas/deletekomponenevaluasi` pada baris
   **kembar** (baru, kosong) — **bukan** baris asing yang berisi nilai (kalau
   salah target → `402 "Nilai Komponen Ubah Jadi 0 ada data yang masih
   terisi"`). Menghapus by-nama melepas semua baris bernama sama, termasuk
   baris asing yang berisi nilai. Cek: baris asing hilang, **nilai akhir mhs
   tetap utuh** (verifikasi `/nilai/detail`).
6. **Susun struktur**: `POST /kelas/addrencanaevaluasi` (hanya baris baru)
   untuk menambah baris yang kurang (mis. jns2 Aktivitas Partisipatif) + `PUT
   /kelas/editrencanaevaluasi` (array penuh) untuk bobot — **total payload
   tepat 100**. Ulangi sampai struktur = master (jns2 15, jns3 35, TGS 10,
   QIZ 10, UTS 15, UAS 15) dan jumlah baris = 6.
7. **Isi nilai**: `POST /nilai/update` dengan array seluruh mahasiswa; isi
   tiap slot dari data sumber (mis. nilai = bobot komponen). Nilai lama
   di komponen asing sudah terlepas di langkah 5.
8. **Verifikasi**: `GET /kelas/nilaikomeval/{id_komp_eval}/{id_reg_pd}` per
   mhs + `GET /nilai/detail/{id_kls}` (nilai akhir tidak berubah).

## Kondisi uji & contoh skenario

- Kelas lama yang memiliki komponen asing `id_jns_eval=4` (mis. `nama="Hasil Proyek"` atau `"Aktivitas Partisipatif"`) berhasil dilepas dengan trik kembar, dirapikan ke struktur 6 slot (total 100), dan nilai komponennya diisi sesuai bobot tanpa mengubah nilai akhir mahasiswa.
- Kelas baru kosong yang diisi lewat `GET /kelas/salinrencanaevaluasi/{id_kls}/{id_mk}` langsung mendapat 6 slot bersih sesuai master.
- Nilai komponen yang sudah terisi angka (mis. `0.0000`) tidak bisa dikosongkan via API dan harus dikosongkan manual di UI Feeder.

### Gate produksi `--pddikti`
Kedua script (`master_evaluasi_fix.py`, `komponen_evaluasi_fix.py`) default
**sandbox**; untuk pddikti wajib `--pddikti` **dan** `--confirm TULIS-PRODUKSI`
(keduanya). Tanpa itu → STOP. Jalur first-time: backup read-only dulu (kedua
script menyimpan backup otomatis ke `responses/` saat `--go`), dry-run, baru
`--go`.

## Dua script (pemisahan 2026-10-07)

- **`master_evaluasi_fix.py`** — hanya mengurus **master MK**
  (`/matakuliah/editrencanaeval/{id_mk}`). Paksa 15/35/10/10/15/15. Pengaman
  keras: bentuk awal wajib **0 baris atau tepat 6 slot**, selain itu stop.
  Backup otomatis semua jalur tulis. Kelas dipetakan per `id_mk` (MK bisa
  punya >1 master). CLI: `--mk` / `--prefix`, `--go`, gate pddikti.
- **`komponen_evaluasi_fix.py`** — hanya mengurus **kelas perkuliahan**
  (struktur komponen) + **isi nilai komponen** = bobot. Jalankan **setelah**
  master benar. Kelas **kosong** disalin dari master (`salinrencanaevaluasi`),
  **bukan** POST. Backup otomatis ke `responses/` saat `--go`.
  CLI: `--mk` / `--kelas` / `--prefix`, `--go`, gate pddikti.
  **`--umum`**: paksa target **15/35/10/10/15/15** (abaikan master) — untuk
  kelas yang masternya **duplikat/kacau**
  **`--sf20`**: paksa target **30/20/0/0/25/25** (abaikan master) — untuk
  prefix **SF20/DF20**.
  **Gate nilai akhir**: toleransi 0.01 (kelas bobot-0 yang diisi bisa bergeser
  ≤ 0.01 karena pembulatan server); daftar mhs berubah tetap ditolak.

- **Helper bersama** (`cek_token`, `gate_db`, `client_gated`, `PDDIKTI_CONFIRM`)
  hidup di `komponen_evaluasi_fix.py` dan dipakai `master_evaluasi_fix.py`
  lewat `import komponen_evaluasi_fix as base`.

## Aturan umum (hasil grilling 2026-10-07)

Berlaku untuk MK **prefix `SF23*`, `SF24*`, `DF24*`**.

1. **Rapikan master dulu**, paksa jadi **15/35/10/10/15/15** — walau sudah berisi
   (bukan hanya bila kosong). Pengaman keras: master hanya disentuh bila
   **0 baris** atau **tepat 6 slot**; bentuk lain (1–5, >6, asing, duplikat) →
   lapor & stop (lihat "PENGAMAN KERAS master").
2. Master **berisi baris asing → lapor & stop** (asing mustahil di master;
   asing fenomena kelas).
3. Master **kosong** → `POST /matakuliah/addrencanaeval` (6 baris, `tambah:oke`).
   Master **6 slot** → ubah bobot via `PUT /matakuliah/editrencanaeval/{id_re_mk}`,
   payload **array semua baris**. **Tidak ada endpoint hapus** baris
   master; edit master hanya **bobot + deskripsi** (jns & nama tak bisa diubah).
4. Setelah master benar, **tiap kelas** dalam MK ikut master: lepas asing, tambah
   slot hilang, set bobot (total 100).
5. **Nilai komponen = bobot master** (15/35/10/10/15/15).
6. **Nilai akhir wajib utuh**; berubah 1 → perlu-manual.
7. **Batch stop total** bila satu MK perlu-manual.
8. CLI: `--mk SF23667` (satu MK) atau `--prefix "SF23,SF24,DF24"` (batch).

### Catatan temuan `PUT` array master
`PUT` array master bisa **parsial** untuk nilai tertentu
(mis. `[30,20,0,0,25,25]` → baris terakhir jadi 0), padahal balas `200`. Target
15/35/10/10/15/15 tersimpan penuh. **Wajib verifikasi baca-ulang** setiap kali.

### TEMUAN TERBUKA: master KOSONG tak bisa diisi lewat API (2026-10-07)
Uji sandbox SF24436 (master `[]`, 2 kelas):
- `POST /matakuliah/addrencanaeval` payload **array 6 baris total 100** →
  `200 "Data berhasil ditambahkan"` tapi **master tetap kosong** (tidak
  tersimpan). Payload array total < 100 → `402`.
- `POST` **objek tunggal** → `200 "Data berhasil ditambahkan"`, juga **tidak
  tersimpan**.
- Tidak ada endpoint `salinrencanaeval` arah kelas→master (404).
- Kesimpulan: master kosong **tidak bisa diisi via API yang sudah dipetakan**.
  Script berhenti `perlu-manual` (stop-on-unknown). **Butuh info user**: isi
  manual di UI, atau temukan endpoint yang benar.

Sementara: master yang **sudah ada isinya** (6 slot total 100) **bisa** diubah
bobotnya via `PUT /matakuliah/editrencanaeval/{id_re_mk}` (terbukti penuh).

### TERPECAHKAN: isi master kosong butuh `"tambah":"oke"` (2026-10-07)
Dari cURL browser user (isi master SF24665 via modal "Tambah Rencana
Evaluasi"): `POST /matakuliah/addrencanaeval` **berhasil** bila **setiap baris
punya `"tambah":"oke"`**. Tanpa field itu server balas `200` tapi tidak
menyimpan. Pola baris yang benar (persis browser):
```json
{"urutan":1,"id_jns_eval":2,"id_mk":"<id_mk>","nama":null,"deskripsi":"",
 "deskripsi_en":"","bobot":"15","nm_jns_eval":"Aktivitas Partisipatif","tambah":"oke"}
```
- `nama`: `null` untuk jns2/jns3; `"TGS"/"QIZ"/"UTS"/"UAS"` untuk jns4.
- `bobot`: **string**.
- Payload POST **wajib total 100** (kalau < 100 → `402`). Untuk master parsial,
  baris baru diberi bobot target dengan sisa ditaruh di baris terakhir agar
  total 100; setelah tersimpan, `PUT` menyetel bobot final.
- Terbukti: SF24436 (kosong) & SF24211 (kosong) → master 15/35/10/10/15/15.

**Masih terbuka:** endpoint **hapus** baris master belum ditemukan. Kandidat
`DELETE /matakuliah/deleterencanaeval/{id_re_mk}` ada tapi menuntut `id_rp_mk`
yang tak ada di data baris → selalu `404 "id_rp_mk not found"`. Jadi master
**duplikat/berlebih** belum bisa dibersihkan via API. Uji saya sempat membuat
SF24665 duplikat 12 baris (SANDBOX) — perlu dibersihkan manual di UI.

### PENGAMAN KERAS master (2026-10-07)
Karena baris master **tak bisa dihapus** (UI & API), script TIDAK PERNAH boleh
membuat bentuk master yang salah. `rapikan_master` kini:

1. **`master_bentuk_aman`**: bentuk awal master WAJIB **0 baris** atau **tepat
   6 slot** (tanpa asing, tanpa duplikat). Bentuk lain (1..5, >6, asing,
   duplikat) → `perlu-manual`, **tidak menulis apa pun**.
2. **Master sudah benar** (6 slot, total 100, bobot = 15/35/10/10/15/15) →
   langsung `ok`, dilewati (tak ada PUT sia-sia).
3. **Master kosong** → POST **6 baris sekaligus** (`tambah:"oke"`, total 100).
   Server menambah persis yang dikirim → tak akan melebihi 6.
4. **Master 6 slot** → hanya PUT bobot (tidak menambah baris).
5. **`master_benar`** memverifikasi hasil akhir: **wajib tepat 6 baris** & bobot
   = target, kalau tidak → `perlu-manual`.

Terbukti: SF24665 (master duplikat 12 baris) → ditolak `perlu-manual` tanpa
menulis; SF24436 (master sudah benar) → `ok` dilewati.

## Catatan lain

- `GET /kelas/salinrencanaevaluasi/{id_kls}/{id_mk}` pada kelas berisi =
  no-op (200 tanpa perubahan). Pada kelas kosong = menanam struktur master
  (**tepat 6 baris**).
- **BAHAYA kelas kosong (2026-10-07):** `POST /kelas/addrencanaevaluasi` ke
  kelas yang **0 komponen** membuat server menyemai **6 template bawaan + 1
  baris kita = 7 baris**. Untuk kelas kosong **JANGAN pakai POST** — pakai
  `salinrencanaevaluasi` (hasil tepat 6). (Uji saya sempat merusak
  DF242401P-B, DF244405-B, DF242208-A, DF242218P-B di sandbox.)
- **Hapus baris KELAS**: baris **kosong** → `DELETE` params lengkap (+`id_komp_eval`) + retry; baris **berisi nilai** → trik kembar. Lihat "Cara hapus baris komponen KELAS".
- Master MK lama banyak yang **kosong** (0 komponen) meski kelasnya punya
  6 komponen — kelas berjalan mandiri, bukan turunan master.
