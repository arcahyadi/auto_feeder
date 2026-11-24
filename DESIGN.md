# DESIGN.md — Automation Feeder Web

Arah desain untuk dashboard operator. Antislop diterapkan sejak perancangan.

## Design Read

Dashboard operasional akademik untuk staf yang membandingkan banyak baris data
Feeder, memakai bahasa visual netral alat kerja, dial **ENERGY 1 / RHYTHM 1 /
MOTION 1**.

## Identitas

- Nama tampil: **Automation Feeder**; mark teks `AF` pada kotak aksen.
- Karakter: alat kerja, padat, dapat dipindai cepat, tanpa dekorasi.
- Motif: tabel sebagai pusat; status selalu disertai teks (bukan warna saja).

## Palet

Tema light dan dark setara, memakai CSS custom properties di `web/styles.css`.

- Netral: latar abu-hijau sangat muda (light) / hampir hitam kehijauan (dark).
- Aksen: hijau tua (light) dan hijau terang (dark) untuk aksi utama.
- Status: hijau (cocok), merah (anomali/gagal), kuning (peringatan).
- Kontras teks memenuhi WCAG AA di kedua tema.

Alasan: hijau menandakan aksi utama dan status sehat pada alat administrasi,
tanpa gradien atau efek dekoratif.

## Tipografi

- Font sistem (San Francisco/Segoe/Roboto) untuk keterbacaan dan tanpa unduhan.
- Monospace sistem hanya untuk NIM, kode MK, dan nomor.
- Angka sejajar dengan `tabular-nums` pada kolom numerik.

## Layout

- Sidebar fitur tetap di desktop; header berisi koneksi, semester, prodi, tema.
- Filter di baris atas; hasil berupa tabel padat dengan pencarian, urutan,
  pagination, detail baris, dan filter anomali.
- Preview perubahan memakai tabel dengan nilai sebelum/sesudah.
- Lebar sempit: sidebar menjadi baris, filter mengalir, tabel menggulir di
  dalam area sendiri tanpa overflow halaman.

## Gerak

- Tanpa animasi dekoratif; hanya transisi fokus/hover dan progress bar.
- `prefers-reduced-motion` dihormati.

## Status UI

Setiap menu memiliki keadaan: belum dijalankan, memuat (progress), kosong,
selesai, parsial, dan gagal (pesan tegas). Preview terpisah dari penerapan.
