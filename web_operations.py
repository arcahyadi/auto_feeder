#!/usr/bin/env python3
"""Operasi bisnis web: audit/ekspor dan operasi tulis Feeder.

Semua fungsi menerima FeederClient bertoken eksplisit, sehingga bisa diuji
dengan fake client. Logika diadaptasi dari script CLI (kelaskuliah_check.py,
kelas_tm_fix.py, akm_biaya.py, mhslulus_bulk.py, konversi_check.py, dll).

Kontrak:
- run_*   : audit/ekspor -> {"rows", "summary", "columns", "notes"}
- preview_*: validasi baca-saja -> {"items", "errors", "summary"}
- apply_item: eksekusi satu item + verifikasi baca-ulang
"""
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from feeder_client import Cancelled, FeederError, fetch_all, page_rows

# ---------------------------------------------------------------------------
# util


def blank(v):
    return v is None or str(v).strip() == ""


def dec(v, default="0"):
    try:
        return Decimal(str(v if v is not None else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def strict_dec(v):
    try:
        value = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError("bukan angka desimal")
    if not value.is_finite():
        raise ValueError("angka tidak terbatas")
    return value


def fmt_sks(v):
    return f"{dec(v):.2f}"


def fmt_nilai(v):
    return f"{strict_dec(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def bagi_tm_sks(n, total_sks):
    """Aturan distribusi TM & SKS dosen; urutan = urutan feeder."""
    sen = round(float(total_sks) * 100)
    if n == 1:
        return [(16, 16, f"{sen / 100:.2f}")]
    if n == 2:
        s = f"{sen / 200:.2f}"
        return [(8, 8, s), (8, 8, s)]
    if n == 3:
        depan = (sen + 1) // 3
        blk = sen - 2 * depan
        return [(5, 5, f"{depan / 100:.2f}"), (5, 5, f"{depan / 100:.2f}"),
                (6, 6, f"{blk / 100:.2f}")]
    raise ValueError("aturan hanya untuk 1/2/3 dosen")


def csv_safe(value):
    """Cegah formula injection pada CSV; pertahankan NIM/nomor sebagai teks."""
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def ipk_str(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def prodi_ids_of(ctx, params):
    pid = (params or {}).get("prodi") or ""
    if not pid:
        return []
    hit = [p for p in ctx["prodi"] if p.get("id_sms") == pid]
    if len(hit) != 1:
        raise FeederError("input", "Program studi tidak ada di daftar Feeder")
    return hit


def semester_of(ctx, params):
    sid = (params or {}).get("semester") or ctx["active_sem"]["id_smt"]
    for s in ctx["semesters"]:
        if s["id_smt"] == sid:
            return s
    raise FeederError("input", f"Semester {sid} tidak ada di daftar Feeder")


# ---------------------------------------------------------------------------
# audit: kelas

def run_kelas(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    kode = (params.get("kode") or "").strip().upper()
    rows = fetch_all(client, "/kelas/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
                     cancel=cancel, progress=progress)
    targets = [r for r in rows if not kode or kode in (r.get("kode_mk") or "").upper()]
    out = []
    total = len(targets)
    for done, r in enumerate(targets, 1):
        if cancel and cancel():
            raise Cancelled()
        anomali = []
        if not (r.get("dosen") or "").strip():
            anomali.append("tanpa dosen")
        if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
            anomali.append("belum sync")
        try:
            jmlmhs = int(client.get(f"/kelas/jumlah_jmlmhs/{r['id_kls']}"))
        except (FeederError, TypeError, ValueError):
            jmlmhs = ""
            anomali.append("jumlah peserta gagal dibaca")
        if jmlmhs == 0:
            anomali.append("peserta 0")
        out.append({
            "kode_mk": r.get("kode_mk", ""), "nm_mk": r.get("nm_mk", ""),
            "nm_kls": r.get("nm_kls", ""), "sks_mk": r.get("sks_mk", ""),
            "nm_lemb": r.get("nm_lemb", ""), "dosen": r.get("dosen", ""),
            "jmlmhs": jmlmhs, "id_kls": r.get("id_kls", ""),
            "anomali": anomali,
        })
        if progress:
            progress(done, total)
    summary = {
        "total": len(out),
        "tanpa_dosen": sum("tanpa dosen" in r["anomali"] for r in out),
        "belum_sync": sum("belum sync" in r["anomali"] for r in out),
        "peserta_gagal": sum("jumlah peserta gagal dibaca" in r["anomali"] for r in out),
        "peserta_0": sum("peserta 0" in r["anomali"] for r in out),
    }
    return {
        "rows": out, "summary": summary,
        "columns": [("kode_mk", "Kode"), ("nm_mk", "Mata kuliah"),
                    ("nm_kls", "Kelas"), ("sks_mk", "SKS"),
                    ("nm_lemb", "Prodi"), ("dosen", "Dosen"),
                    ("jmlmhs", "Peserta"), ("anomali", "Anomali")],
    }


# ---------------------------------------------------------------------------
# audit: TM & SKS dosen

def run_tm_sks(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    kode = (params.get("kode") or "").strip().upper()
    kelas = (params.get("kelas") or "").strip().upper()
    rows = fetch_all(client, "/kelas/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
                     cancel=cancel)
    targets = [r for r in rows
               if (not kode or kode in (r.get("kode_mk") or "").upper())
               and (not kelas or (r.get("nm_kls") or "").upper() == kelas)]
    out, total, done = [], len(targets), 0
    for r in targets:
        if cancel and cancel():
            raise Cancelled()
        det = client.get("/kelas/detail/" + r["id_kls"])
        data = det.get("data") if isinstance(det, dict) else None
        if not isinstance(data, dict):
            out.append({"kode_mk": r.get("kode_mk", ""), "nm_mk": r.get("nm_mk", ""),
                        "nm_kls": r.get("nm_kls", ""), "sks_mk": r.get("sks_mk", ""),
                        "jml_dosen": "", "total_tm_renc": "", "total_tm_real": "",
                        "total_sks": "", "anomali": ["detail gagal dibaca"],
                        "dosen": []})
            done += 1
            if progress:
                progress(done, total)
            continue
        dosen = data.get("dosen") or []
        sks_mk = dec(data.get("detail", {}).get("sks_mk") or r.get("sks_mk"))
        tr = sum((dec(d.get("jml_tm_renc")) for d in dosen), Decimal(0))
        tl = sum((dec(d.get("jml_tm_real")) for d in dosen), Decimal(0))
        sks = sum((dec(d.get("sks_subst_tot")) for d in dosen), Decimal(0))
        names = Counter((d.get("nm_sdm") or "").strip() for d in dosen)
        anomali = []
        if not dosen:
            anomali.append("tanpa dosen")
        if any(n > 1 for n in names.values()):
            anomali.append("duplikat nama dosen")
        if len(dosen) > 3:
            anomali.append("lebih dari 3 dosen")
        if dosen and tr != 16:
            anomali.append(f"total TM rencana {tr} != 16")
        if dosen and tl != 16:
            anomali.append(f"total TM realisasi {tl} != 16")
        if dosen and sks != sks_mk:
            anomali.append(f"total SKS dosen {sks} != SKS MK {sks_mk}")
        out.append({
            "kode_mk": r.get("kode_mk", ""), "nm_mk": r.get("nm_mk", ""),
            "nm_kls": r.get("nm_kls", ""), "sks_mk": fmt_sks(sks_mk),
            "jml_dosen": len(dosen), "total_tm_renc": str(tr), "total_tm_real": str(tl),
            "total_sks": fmt_sks(sks), "anomali": anomali,
            "dosen": [{"nm_sdm": d.get("nm_sdm", ""),
                       "jml_tm_renc": d.get("jml_tm_renc", ""),
                       "jml_tm_real": d.get("jml_tm_real", ""),
                       "sks_subst_tot": d.get("sks_subst_tot", "")} for d in dosen],
        })
        done += 1
        if progress:
            progress(done, total)
    summary = {
        "total": len(out),
        "sesuai": sum(not r["anomali"] for r in out),
        "anomali": sum(bool(r["anomali"]) for r in out),
    }
    return {
        "rows": out, "summary": summary,
        "columns": [("kode_mk", "Kode"), ("nm_mk", "Mata kuliah"),
                    ("nm_kls", "Kelas"), ("sks_mk", "SKS"), ("jml_dosen", "Dosen"),
                    ("total_tm_renc", "TM rencana"), ("total_tm_real", "TM realisasi"),
                    ("total_sks", "SKS dosen"), ("anomali", "Anomali")],
    }


def csv_rows_tm_sks(rows):
    """Ekspor per penugasan dosen (seperti script)."""
    out = []
    for r in rows:
        if not r.get("dosen"):
            out.append({"kode_mk": r.get("kode_mk", ""), "nm_mk": r.get("nm_mk", ""),
                        "nm_kls": r.get("nm_kls", ""), "sks_mk": r.get("sks_mk", ""),
                        "nm_sdm": "", "jml_tm_renc": "", "jml_tm_real": "",
                        "sks_subst_tot": "", "anomali": "; ".join(r.get("anomali", []))})
            continue
        for d in r["dosen"]:
            out.append({"kode_mk": r.get("kode_mk", ""), "nm_mk": r.get("nm_mk", ""),
                        "nm_kls": r.get("nm_kls", ""), "sks_mk": r.get("sks_mk", ""),
                        "nm_sdm": d.get("nm_sdm", ""), "jml_tm_renc": d.get("jml_tm_renc", ""),
                        "jml_tm_real": d.get("jml_tm_real", ""),
                        "sks_subst_tot": d.get("sks_subst_tot", ""),
                        "anomali": "; ".join(r.get("anomali", []))})
    return out


# ---------------------------------------------------------------------------
# audit: nilai perkuliahan

def run_nilai(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    kode = (params.get("kode") or "").strip().upper()
    rows = fetch_all(client, "/nilai/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk"],
                     sort_by="kode_mk", cancel=cancel)
    targets = [r for r in rows if not kode or kode in (r.get("kode_mk") or "").upper()]
    out, total, done = [], len(targets), 0
    for r in targets:
        if cancel and cancel():
            raise Cancelled()
        anomali = []
        jmlmhs = sud = None
        try:
            jmlmhs = int(client.get(f"/kelas/jumlah_jmlmhs/{r['id_kls']}"))
        except (FeederError, TypeError, ValueError):
            anomali.append("jumlah peserta gagal dibaca")
        try:
            sud = int(client.get(f"/kelas/jumlah_jmlsudahdinilai/{r['id_kls']}"))
        except (FeederError, TypeError, ValueError):
            anomali.append("jumlah dinilai gagal dibaca")
        if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
            anomali.append("belum sync")
        if jmlmhs is not None and sud is not None and sud < jmlmhs:
            anomali.append(f"dinilai {sud} dari {jmlmhs} peserta")
        out.append({
            "kode_mk": r.get("kode_mk", ""), "nm_mk": r.get("nm_mk", ""),
            "nm_kls": r.get("nm_kls", ""), "sks_mk": r.get("sks_mk", ""),
            "jmlmhs": "" if jmlmhs is None else jmlmhs,
            "jml_sudahdinilai": "" if sud is None else sud,
            "anomali": anomali,
        })
        done += 1
        if progress:
            progress(done, total)
    summary = {
        "total": len(out),
        "dinilai_kurang": sum(any("dinilai" in a for a in r["anomali"]) for r in out),
        "gagal_baca": sum(any("gagal dibaca" in a for a in r["anomali"]) for r in out),
    }
    return {
        "rows": out, "summary": summary,
        "columns": [("kode_mk", "Kode"), ("nm_mk", "Mata kuliah"),
                    ("nm_kls", "Kelas"), ("sks_mk", "SKS"), ("jmlmhs", "Peserta"),
                    ("jml_sudahdinilai", "Sudah dinilai"), ("anomali", "Anomali")],
    }


# ---------------------------------------------------------------------------
# audit: AKM

def _akm_studi(status):
    """True jika status berarti sedang studi. Keluar/Cuti/Non-Aktif/Lulus
    wajar ber-IPS 0 dan SKS semester 0 -> bukan anomali."""
    s = " ".join((status or "").strip().lower().replace("-", " ").split())
    return not any(k in s for k in ("keluar", "cuti", "lulus", "non aktif",
                                    "nonaktif", "tidak aktif", "undur"))


def run_akm(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    nim = (params.get("nim") or "").strip()
    rows = fetch_all(client, "/kuliahmhs/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     extra_setfilter={"angkatan": [], "status": []},
                     column_search=["pd.nm_pd", "p.nipd"],
                     sort_by="st.nm_smt", cancel=cancel, progress=progress)
    out = []
    for r in rows:
        if nim and nim not in (r.get("nipd") or ""):
            continue
        anomali = []
        nol = str(r.get("ips") or "0") in ("", "0") and str(r.get("sks_smt") or "0") == "0"
        if nol and _akm_studi(r.get("nm_stat_mhs")):
            anomali.append("IPS/SKS 0")
        if blank(r.get("biaya_smt")) or dec(r.get("biaya_smt")) == 0:
            anomali.append("biaya kosong")
        out.append({
            "nipd": r.get("nipd", ""), "nm_pd": r.get("nm_pd", ""),
            "nm_lemb": r.get("nm_lemb", ""), "nm_smt": r.get("nm_smt", ""),
            "nm_stat_mhs": r.get("nm_stat_mhs", ""), "ips": r.get("ips", ""),
            "ipk": r.get("ipk", ""), "sks_smt": r.get("sks_smt", ""),
            "sks_total": r.get("sks_total", ""), "nm_pembiayaan": r.get("nm_pembiayaan", ""),
            "biaya_smt": r.get("biaya_smt", ""), "anomali": anomali,
        })
    summary = {
        "total": len(out),
        "biaya_kosong": sum("biaya kosong" in r["anomali"] for r in out),
        "ips_sks_nol": sum("IPS/SKS 0" in r["anomali"] for r in out),
    }
    return {
        "rows": out, "summary": summary,
        "columns": [("nipd", "NIM"), ("nm_pd", "Nama"), ("nm_lemb", "Prodi"),
                    ("nm_stat_mhs", "Status"), ("ips", "IPS"), ("ipk", "IPK"),
                    ("sks_smt", "SKS smt"), ("sks_total", "SKS total"),
                    ("nm_pembiayaan", "Pembiayaan"), ("biaya_smt", "Biaya"),
                    ("anomali", "Anomali")],
    }


# ---------------------------------------------------------------------------
# audit: aktivitas mahasiswa

def run_aktivitas(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    cari = (params.get("cari") or "").strip().upper()
    rows = fetch_all(client, "/aktmhs/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     extra_setfilter={"jenisaktivitas": [], "asaldata": []},
                     column_search=["m.judul_akt_mhs", "m.sk_tugas", "angg.nm_pd",
                                    "angg.nipd", "uji.nm_sdm", "bimb.nm_sdm"],
                     sort_by="st.nm_smt", cancel=cancel, progress=progress)
    out = []
    for r in rows:
        judul = r.get("judul_akt_mhs") or ""
        if cari and cari not in judul.upper() and cari not in (r.get("sk_tugas") or "").upper():
            continue
        anomali = []
        if blank(r.get("sk_tugas")):
            anomali.append("SK kosong")
        if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
            anomali.append("belum sync")
        out.append({
            "judul": judul, "jenis": r.get("nm_jns_akt_mhs", ""),
            "nm_smt": r.get("nm_smt", ""), "tgl_sk": str(r.get("tgl_sk_tugas") or "")[:10],
            "lokasi": r.get("lokasi_kegiatan", ""), "nm_lemb": r.get("nm_lemb", ""),
            "id_akt_mhs": r.get("id_akt_mhs", ""), "anomali": anomali,
        })
    summary = {"total": len(out),
               "sk_kosong": sum("SK kosong" in r["anomali"] for r in out)}
    return {
        "rows": out, "summary": summary,
        "columns": [("judul", "Judul"), ("jenis", "Jenis"), ("nm_smt", "Semester"),
                    ("tgl_sk", "Tgl SK"), ("lokasi", "Lokasi"), ("nm_lemb", "Prodi"),
                    ("anomali", "Anomali")],
    }


# ---------------------------------------------------------------------------
# audit: konversi nilai

def run_konversi(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    jenis = (params.get("jenis") or "").strip().lower()
    nim = (params.get("nim") or "").strip()
    acts = fetch_all(client, "/aktmhs/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     extra_setfilter={"jenisaktivitas": [], "asaldata": []},
                     column_search=["m.judul_akt_mhs", "m.sk_tugas", "angg.nm_pd",
                                    "angg.nipd", "uji.nm_sdm", "bimb.nm_sdm"],
                     sort_by="st.nm_smt", cancel=cancel)
    if jenis:
        acts = [a for a in acts if jenis in (a.get("nm_jns_akt_mhs") or "").lower()]
    out, total, done = [], len(acts), 0
    no_anggota = no_nilai = 0
    for a in acts:
        if cancel and cancel():
            raise Cancelled()
        det = client.get("/aktmhs/edit/" + a["id_akt_mhs"])
        angg = det.get("anggota") or [] if isinstance(det, dict) else []
        if not angg:
            no_anggota += 1
            out.append({"judul": a.get("judul_akt_mhs", ""), "jenis": a.get("nm_jns_akt_mhs", ""),
                        "nipd": "", "nama": "", "mk": "", "sks": "", "angka": "",
                        "huruf": "", "indeks": "", "status": "tanpa anggota"})
        for m in angg:
            if nim and nim not in (m.get("nipd") or ""):
                continue
            try:
                t = client.get("/nilaiaktmhs/transfermatkul/" + m["id_ang_akt_mhs"])
            except FeederError as e:
                no_nilai += 1
                out.append({"judul": a.get("judul_akt_mhs", ""), "jenis": a.get("nm_jns_akt_mhs", ""),
                            "nipd": m.get("nipd", ""), "nama": m.get("nm_pd", ""), "mk": "",
                            "sks": "", "angka": "", "huruf": "", "indeks": "",
                            "status": f"gagal baca: {e.message[:60]}"})
                continue
            konv = (t.get("konversi") or []) if isinstance(t, dict) else []
            kosong = [k for k in konv if blank(k.get("nilai_angka"))]
            if not konv or kosong:
                no_nilai += 1
                if not konv:
                    out.append({"judul": a.get("judul_akt_mhs", ""),
                                "jenis": a.get("nm_jns_akt_mhs", ""),
                                "nipd": m.get("nipd", ""), "nama": m.get("nm_pd", ""),
                                "mk": "", "sks": "", "angka": "", "huruf": "",
                                "indeks": "", "status": "tanpa nilai (0/0)"})
            for k in konv:
                out.append({"judul": a.get("judul_akt_mhs", ""), "jenis": a.get("nm_jns_akt_mhs", ""),
                            "nipd": m.get("nipd", ""), "nama": m.get("nm_pd", ""),
                            "mk": k.get("nm_mk", ""), "sks": k.get("sks_mk", ""),
                            "angka": k.get("nilai_angka", ""),
                            "huruf": (k.get("nilai_huruf") or "").strip(),
                            "indeks": k.get("nilai_indeks", ""),
                            "status": "tanpa nilai" if blank(k.get("nilai_angka")) else "bernilai"})
        done += 1
        if progress:
            progress(done, total)
    summary = {"aktivitas": len(acts), "baris": len(out),
               "aktivitas_tanpa_anggota": no_anggota, "anggota_tanpa_nilai": no_nilai}
    return {
        "rows": out, "summary": summary,
        "columns": [("judul", "Judul"), ("jenis", "Jenis"), ("nipd", "NIM"),
                    ("nama", "Nama"), ("mk", "Mata kuliah"), ("sks", "SKS"),
                    ("angka", "Angka"), ("huruf", "Huruf"), ("indeks", "Indeks"),
                    ("status", "Status")],
    }


# ---------------------------------------------------------------------------
# audit: mahasiswa keluar/lulus + pemantauan ijazah

def fetch_mhslulus_all(client, cancel=None, progress=None):
    """Paginasi /mhslulus/index semua semester (filter server tak andal)."""
    rows, page, total = [], 1, None
    while True:
        if cancel and cancel():
            raise Cancelled()
        data = client.get("/mhslulus/index", params={
            "pagination": {"sortBy": "id_reg_pd", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "", "columnSearch": ["m.nm_pd", "m.nipd", "p.nm_prodi"],
            "setFilter": {"semester": [], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        if not (isinstance(data, list) and len(data) == 2 and isinstance(data[0], list)):
            raise FeederError("response", "/mhslulus/index tidak berbentuk daftar")
        batch = data[0]
        try:
            count = int(data[1])
        except (TypeError, ValueError):
            raise FeederError("response", "Jumlah /mhslulus/index tidak valid")
        total = count if total is None else total
        if count != total:
            raise FeederError("response", "Jumlah /mhslulus/index berubah saat paginasi")
        rows += batch
        if progress:
            progress(len(rows), total)
        if len(batch) < 100:
            break
        page += 1
    if len(rows) != total:
        raise FeederError("response", "Paginasi /mhslulus/index tidak lengkap")
    return rows


def run_mhslulus(client, ctx, params, progress, cancel, preset=None):
    preset = preset or {}
    tahun = (params.get("tahun") or "").strip()
    status = (params.get("status") or "").strip()
    prodi = (params.get("prodi") or "").strip()
    nim = (params.get("nim") or "").strip()
    tanpa = params.get("tanpa_ijazah")
    if preset.get("tanpa_ijazah") is not None:
        tanpa = preset["tanpa_ijazah"]
    if preset.get("status"):
        status = preset["status"]
    rows = fetch_mhslulus_all(client, cancel=cancel, progress=progress)
    out = []
    for r in rows:
        if tahun and str(r.get("tgl_keluar") or "")[:4] != tahun:
            continue
        if status and (r.get("ket_keluar") or "").strip().lower() != status.lower():
            continue
        if prodi and prodi not in (r.get("nm_lemb") or ""):
            continue
        if nim and nim not in (r.get("nipd") or ""):
            continue
        if tanpa and not blank(r.get("no_seri_ijazah")):
            continue
        anomali = []
        lulus = (r.get("ket_keluar") or "").strip().lower() == "lulus"
        if not lulus:
            anomali.append(f"bukan lulus ({r.get('ket_keluar') or '?'})")
        elif blank(r.get("no_seri_ijazah")):
            anomali.append("belum ada nomor ijazah")
        out.append({
            "nipd": r.get("nipd", ""), "nm_pd": r.get("nm_pd", ""),
            "nm_lemb": r.get("nm_lemb", ""), "ket_keluar": r.get("ket_keluar", ""),
            "tgl_keluar": str(r.get("tgl_keluar") or "")[:10],
            "smt_yudisium": r.get("smt_yudisium", ""),
            "nm_smt_keluar": r.get("nm_smt_keluar", ""),
            "sk_yudisium": r.get("sk_yudisium", ""), "ipk": ipk_str(r.get("ipk")),
            "no_seri_ijazah": r.get("no_seri_ijazah") or "",
            "tgl_terbit_ijazah": str(r.get("tgl_terbit_ijazah") or "")[:10],
            "anomali": anomali,
        })
    lulus = [r for r in out if r["ket_keluar"].strip().lower() == "lulus"]
    summary = {
        "total": len(out),
        "lulus": len(lulus),
        "bukan_lulus": len(out) - len(lulus),
        "lulus_tanpa_ijazah": sum(blank(r["no_seri_ijazah"]) for r in lulus),
    }
    return {
        "rows": out, "summary": summary,
        "columns": [("nipd", "NIM"), ("nm_pd", "Nama"), ("nm_lemb", "Prodi"),
                    ("ket_keluar", "Status keluar"), ("tgl_keluar", "Tgl keluar"),
                    ("nm_smt_keluar", "Semester"), ("sk_yudisium", "SK yudisium"),
                    ("ipk", "IPK"), ("no_seri_ijazah", "No. ijazah"),
                    ("anomali", "Anomali")],
    }


# ---------------------------------------------------------------------------
# tulis: tambah status lulus/DO

def _mhslulus_maps(client):
    flt = client.get("/mhslulus/filter")
    if not isinstance(flt, dict) or "list_status" not in flt:
        raise FeederError("response", "Filter mhslulus tidak terbaca")
    statuses = {s["ket_keluar"].strip().lower(): s for s in flt["list_status"][0]}
    semesters = {s["id_smt"]: s for s in flt["list_semester"][0]}
    return statuses, semesters


def _exact_student(response, nim):
    """Ambil tepat satu mahasiswa dengan NIM persis dari list nested Feeder."""
    raw = response.get("list_mahasiswa") if isinstance(response, dict) else None
    if not raw or raw is False:
        return None
    candidates = raw[0] if raw and isinstance(raw[0], list) else raw
    hit = [m for m in candidates if isinstance(m, dict)
           and str(m.get("nipd") or "").strip() == nim]
    return hit[0] if len(hit) == 1 else None


def _write_failure(error):
    """Koneksi putus saat mutasi berarti hasil belum dapat dipastikan."""
    uncertain = error.kind in ("connection", "timeout", "response", "server")
    return {"status": "unknown" if uncertain else "fail",
            "message": ("hasil tulis belum pasti: " if uncertain else "") + error.message}


def _readback_failure(error):
    return {"status": "unknown", "message": "tersimpan; baca-ulang gagal: " + error.message}


def preview_mhslulus_add(client, ctx, form, progress=None, cancel=None):
    rows = form.get("rows") or []
    statuses, semesters = _mhslulus_maps(client)
    existing = {}
    lulus_rows = fetch_mhslulus_all(client, cancel=cancel)
    for r in lulus_rows:
        existing[str(r.get("nipd"))] = r
    items, errors, seen = [], [], set()
    for i, row in enumerate(rows, 1):
        nim = (row.get("nim") or "").strip()
        jenis = (row.get("jenis_keluar") or "").strip()
        periode = (row.get("periode") or "").strip()
        tgl = (row.get("tgl_keluar") or "").strip()
        tgl_sk = (row.get("tgl_sk") or "").strip()
        no_sk = (row.get("no_sk") or "").strip()
        ipk = (row.get("ipk") or "").strip()
        errs = []
        if not nim:
            errs.append("NIM kosong")
        elif nim in seen:
            errs.append("NIM duplikat dalam CSV")
        else:
            seen.add(nim)
        if jenis.lower() not in statuses:
            errs.append(f"jenis keluar '{jenis}' tidak dikenal")
        if periode not in semesters:
            errs.append(f"periode '{periode}' tidak dikenal")
        for label, val in (("tanggal keluar", tgl), ("tanggal SK", tgl_sk)):
            if val and not _is_date(val):
                errs.append(f"{label} harus YYYY-MM-DD")
        if not tgl:
            errs.append("tanggal keluar kosong")
        if not tgl_sk:
            errs.append("tanggal SK kosong")
        if not no_sk:
            errs.append("nomor SK kosong")
        try:
            ipk_val = float(ipk)
            if not 0 <= ipk_val <= 4:
                errs.append("IPK harus 0-4")
        except ValueError:
            errs.append("IPK harus angka")
        if nim and nim in existing:
            errs.append("NIM sudah tercatat keluar di Feeder")
        mhs = None
        if not errs:
            found = client.get("/mhslulus/cari_mahasiswa", params={"nm_pd": nim})
            mhs = _exact_student(found, nim)
            if not mhs:
                errs.append("NIM persis tidak ditemukan, ambigu, atau sudah lulus")
        item = {
            "id": i, "target": {"nim": nim, "nama": (mhs or {}).get("nm_pd", ""),
                                  "id_reg_pd": (mhs or {}).get("id_reg_pd", "")},
            "after": {"jenis_keluar": jenis, "periode": periode, "tgl_keluar": tgl,
                      "tgl_sk": tgl_sk, "no_sk": no_sk, "ipk": ipk},
            "errors": errs,
        }
        (errors if errs else items).append(item)
    return {"items": items, "errors": errors, "summary": {"siap": len(items), "error": len(errors)}}


def _is_date(s):
    try:
        return date.fromisoformat(s).isoformat() == s
    except (TypeError, ValueError):
        return False


def apply_mhslulus_add(client, ctx, item):
    nim = item["target"]["nim"]
    after = item["after"]
    statuses, semesters = _mhslulus_maps(client)
    status = statuses.get(after["jenis_keluar"].lower())
    sem = semesters.get(after["periode"])
    if not status or not sem:
        return {"status": "fail", "message": "jenis keluar/periode tidak lagi valid"}
    found = client.get("/mhslulus/cari_mahasiswa", params={"nm_pd": nim})
    mhs = _exact_student(found, nim)
    if not mhs or mhs.get("id_reg_pd") != item["target"]["id_reg_pd"]:
        return {"status": "konflik", "message": "target mahasiswa berubah sejak preview"}
    payload = {
        "list_mahasiswa": mhs, "filter_status": status, "filter_semester": sem,
        "id_jns_keluar": status["id_jns_keluar"], "id_reg_pd": mhs["id_reg_pd"],
        "smt_yudisium": after["periode"], "tgl_keluar": after["tgl_keluar"],
        "tgl_sk_yudisium": after["tgl_sk"], "sk_yudisium": after["no_sk"],
        "ipk": after["ipk"], "ipktranskrip": "-",
    }
    try:
        res = client.post("/mhslulus/add", payload=payload)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        rows = fetch_mhslulus_all(client)
    except FeederError as e:
        return _readback_failure(e)
    hit = [r for r in rows if str(r.get("nipd")) == nim]
    if len(hit) != 1:
        return {"status": "unknown", "message": "tersimpan tetapi belum terbaca ulang"}
    hit = hit[0]
    ok = (str(hit.get("id_jns_keluar")) == str(status["id_jns_keluar"])
          and str(hit.get("smt_yudisium")) == after["periode"]
          and str(hit.get("tgl_keluar") or "")[:10] == after["tgl_keluar"]
          and (hit.get("sk_yudisium") or "") == after["no_sk"])
    if not ok:
        return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}
    return {"status": "ok", "message": f"{nim} {after['jenis_keluar']} terverifikasi"}


# ---------------------------------------------------------------------------
# tulis: biaya AKM

def preview_akm_biaya(client, ctx, form, progress=None, cancel=None):
    sem = semester_of(ctx, form)
    rows = form.get("rows") or []
    kuliah = fetch_all(client, "/kuliahmhs/index", sem,
                       prodi_ids=[], extra_setfilter={"angkatan": [], "status": []},
                       column_search=["pd.nm_pd", "p.nipd"], sort_by="st.nm_smt",
                       cancel=cancel)
    index = {}
    for r in kuliah:
        index.setdefault(str(r.get("nipd")), []).append(r)
    try:
        flt = client.get("/kuliahmhs/filter")
        known_status = {s["id_stat_mhs"] for s in flt.get("list_status", [[]])[0]}
    except (FeederError, KeyError, TypeError):
        known_status = None
    items, errors, seen = [], [], set()
    for i, row in enumerate(rows, 1):
        nim = (row.get("nim") or "").strip()
        biaya = (row.get("biaya") or "").strip()
        errs = []
        if not nim:
            errs.append("NIM kosong")
        elif nim in seen:
            errs.append("NIM duplikat dalam CSV")
        else:
            seen.add(nim)
        if not biaya.isdigit():
            errs.append("biaya harus angka bulat")
        elif int(biaya) <= 0:
            errs.append("biaya harus lebih dari 0")
        hit = index.get(nim, [])
        if nim and not hit:
            errs.append("NIM tidak ditemukan di semester aktif")
        elif len(hit) > 1:
            errs.append("NIM muncul lebih dari satu baris AKM")
        elif hit and known_status is not None and hit[0].get("id_stat_mhs") not in known_status:
            errs.append(f"status '{hit[0].get('nm_stat_mhs') or hit[0].get('id_stat_mhs')}' "
                        "tidak bisa diubah via AKM (Lulus: isi manual)")
        before = hit[0].get("biaya_smt") if len(hit) == 1 else ""
        item = {"id": i, "target": {"nim": nim, "nama": hit[0].get("nm_pd", "") if len(hit) == 1 else "",
                                    "semester": sem["id_smt"], "nm_smt": sem["nm_smt"]},
                "before": {"biaya": before}, "after": {"biaya": biaya}, "errors": errs}
        (errors if errs else items).append(item)
    return {"items": items, "errors": errors, "summary": {"siap": len(items), "error": len(errors)}}


def apply_akm_biaya(client, ctx, item):
    sem = semester_of(ctx, {"semester": item["target"]["semester"]})
    nim = item["target"]["nim"]
    biaya = item["after"]["biaya"]
    kuliah = fetch_all(client, "/kuliahmhs/index", sem,
                       prodi_ids=[], extra_setfilter={"angkatan": [], "status": []},
                       column_search=["pd.nm_pd", "p.nipd"], sort_by="st.nm_smt")
    hit = [r for r in kuliah if str(r.get("nipd")) == nim]
    if len(hit) != 1:
        return {"status": "konflik", "message": "baris AKM berubah sejak preview"}
    row = hit[0]
    try:
        current_cost = strict_dec(row.get("biaya_smt"))
        preview_cost = strict_dec(item["before"]["biaya"])
    except ValueError:
        return {"status": "konflik", "message": "nilai biaya Feeder tidak valid"}
    if current_cost != preview_cost:
        return {"status": "konflik", "message": "biaya berubah sejak preview"}
    flt = client.get("/kuliahmhs/filter")
    m = client.get("/kuliahmhs/cari_mahasiswa", params={"nm_pd": nim})
    mhs = _exact_student(m, nim)
    if not mhs or mhs.get("id_reg_pd") != row.get("id_reg_pd"):
        return {"status": "konflik", "message": "target mahasiswa berubah sejak preview"}
    status = next((s for s in flt["list_status"][0]
                   if s["id_stat_mhs"] == row["id_stat_mhs"]), None)
    pembiayaan = next((s for s in flt["list_pembiayaan"][0]
                       if s["id_pembiayaan"] == row["id_pembiayaan"]), None)
    if not status or not pembiayaan:
        return {"status": "fail", "message": "status/pembiayaan tidak dikenal"}
    data = dict(row)
    data.update({"list_mahasiswa": mhs, "filter_status": status,
                 "filter_pembiayaan": pembiayaan, "id_smt_masking": sem["nm_smt"],
                 "biaya_smt_masking": str(biaya), "id_sms": mhs["id_sms"],
                 "id_reg_pd": mhs["id_reg_pd"], "id_stat_mhs": row["id_stat_mhs"],
                 "id_pembiayaan": row["id_pembiayaan"], "biaya_smt": str(biaya),
                 "id_smt": sem["id_smt"]})
    try:
        res = client.put(f"/kuliahmhs/edit?id_reg_pd={mhs['id_reg_pd']}&id_smt={sem['id_smt']}",
                         payload=data)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        back = fetch_all(client, "/kuliahmhs/index", sem,
                         prodi_ids=[], extra_setfilter={"angkatan": [], "status": []},
                         column_search=["pd.nm_pd", "p.nipd"], sort_by="st.nm_smt")
    except FeederError as e:
        return _readback_failure(e)
    hit2 = [r for r in back if str(r.get("nipd")) == nim]
    try:
        verified_cost = strict_dec(hit2[0].get("biaya_smt")) if len(hit2) == 1 else None
    except ValueError:
        verified_cost = None
    if verified_cost == strict_dec(biaya):
        return {"status": "ok", "message": f"{nim} biaya {biaya} terverifikasi"}
    return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}


# ---------------------------------------------------------------------------
# tulis: edit field kelas

KELAS_EDIT_FIELDS = {"nm_kls": "text"}


def preview_kelas_edit(client, ctx, form, progress=None, cancel=None):
    sem = semester_of(ctx, form)
    rows = form.get("rows") or []
    classes = fetch_all(client, "/kelas/index", sem, prodi_ids=prodi_ids_of(ctx, form),
                        column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
                        cancel=cancel)
    items, errors, seen = [], [], set()
    for i, row in enumerate(rows, 1):
        kode = (row.get("kode_mk") or "").strip().upper()
        kelas = (row.get("kelas") or "").strip().upper()
        field = (row.get("field") or "").strip()
        nilai = (row.get("nilai") or "").strip()
        errs = []
        duplicate_key = (kode, kelas, field)
        if duplicate_key in seen:
            errs.append("target/field duplikat dalam CSV")
        else:
            seen.add(duplicate_key)
        if field not in KELAS_EDIT_FIELDS:
            errs.append(f"field '{field}' tidak diizinkan")
        elif KELAS_EDIT_FIELDS[field] == "decimal":
            try:
                angka = Decimal(nilai)
                if not angka.is_finite():
                    errs.append("nilai harus angka desimal terbatas")
                elif angka < 0:
                    errs.append("nilai tidak boleh negatif")
            except (InvalidOperation, ValueError):
                errs.append("nilai harus angka desimal")
        elif not nilai:
            errs.append("nilai kosong")
        hit = [c for c in classes
               if (c.get("kode_mk") or "").upper() == kode
               and (c.get("nm_kls") or "").upper() == kelas]
        if not hit:
            errs.append("kelas tidak ditemukan")
        elif len(hit) > 1:
            errs.append("kelas ambigu (lebih dari satu baris)")
        before = hit[0].get(field) if len(hit) == 1 else ""
        item = {"id": i, "target": {"kode_mk": kode, "kelas": kelas,
                                    "matkul": hit[0].get("nm_mk", "") if len(hit) == 1 else "",
                                    "id_kls": hit[0].get("id_kls", "") if len(hit) == 1 else "",
                                    "semester": sem["id_smt"]},
                "field": field, "before": {"nilai": before}, "after": {"nilai": nilai},
                "errors": errs}
        (errors if errs else items).append(item)
    return {"items": items, "errors": errors, "summary": {"siap": len(items), "error": len(errors)}}


def apply_kelas_edit(client, ctx, item):
    sem = semester_of(ctx, {"semester": item["target"]["semester"]})
    field = item["field"]
    nilai = item["after"]["nilai"]
    if field not in KELAS_EDIT_FIELDS:
        return {"status": "fail", "message": "field tidak diizinkan"}
    classes = fetch_all(client, "/kelas/index", sem, prodi_ids=[],
                        column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"])
    hit = [c for c in classes if c.get("id_kls") == item["target"]["id_kls"]]
    if len(hit) != 1:
        return {"status": "konflik", "message": "kelas berubah sejak preview"}
    cur = hit[0]
    if str(cur.get(field) or "") != str(item["before"]["nilai"] or ""):
        return {"status": "konflik", "message": "nilai berubah sejak preview"}
    data = dict(cur)
    data[field] = nilai
    try:
        res = client.put("/kelas/edit/" + cur["id_kls"], payload=data)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        back = fetch_all(client, "/kelas/index", sem, prodi_ids=[],
                         column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"])
    except FeederError as e:
        return _readback_failure(e)
    hit2 = [c for c in back if c.get("id_kls") == cur["id_kls"]]
    if hit2 and str(hit2[0].get(field) or "") == str(nilai):
        return {"status": "ok", "message": f"{item['target']['kode_mk']}-{item['target']['kelas']} {field} terverifikasi"}
    return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}


# ---------------------------------------------------------------------------
# tulis: perbaikan TM & SKS

def preview_tm_sks_fix(client, ctx, form, progress=None, cancel=None):
    sem = semester_of(ctx, form)
    requested = []
    for row in form.get("rows") or []:
        requested.append(((row.get("kode_mk") or row.get("kode") or "").strip().upper(),
                          (row.get("kelas") or "").strip().upper()))
    if not requested:
        requested = [((form.get("kode") or "").strip().upper(),
                      (form.get("kelas") or "").strip().upper())]
    if any(not kode for kode, _ in requested):
        raise FeederError("input", "Kode MK wajib diisi")
    if len(set(requested)) != len(requested):
        raise FeederError("input", "Kelas duplikat dalam CSV")
    classes = fetch_all(client, "/kelas/index", sem, prodi_ids=prodi_ids_of(ctx, form),
                        column_search=["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
                        cancel=cancel)
    targets = [c for c in classes if any(
        kode == (c.get("kode_mk") or "").upper()
        and (not kelas or (c.get("nm_kls") or "").upper() == kelas)
        for kode, kelas in requested)]
    found = {((c.get("kode_mk") or "").upper(), (c.get("nm_kls") or "").upper())
             for c in targets}
    missing = [(kode, kelas) for kode, kelas in requested
               if not any(kode == fkode and (not kelas or kelas == fkelas)
                          for fkode, fkelas in found)]
    if not targets and missing:
        raise FeederError("input", "Kelas yang diminta tidak ditemukan")
    items, errors, total = [], [], len(targets) + len(missing)
    for idx, c in enumerate(targets, 1):
        if cancel and cancel():
            raise Cancelled()
        det = client.get("/kelas/detail/" + c["id_kls"])
        data = det.get("data") if isinstance(det, dict) else None
        errs = []
        changes = []
        if not isinstance(data, dict):
            errs.append("detail kelas gagal dibaca")
        else:
            dosen = data.get("dosen") or []
            try:
                sks_mk = strict_dec(data.get("detail", {}).get("sks_mk") or c.get("sks_mk"))
                if sks_mk <= 0:
                    raise ValueError
            except ValueError:
                sks_mk = None
                errs.append("SKS mata kuliah tidak valid")
            names = Counter((d.get("nm_sdm") or "").strip() for d in dosen)
            if not dosen:
                errs.append("tanpa dosen; penugasan perlu dibuat manual")
            if any(n > 1 for n in names.values()):
                errs.append("duplikat nama dosen; periksa manual")
            if len(dosen) > 3:
                errs.append("lebih dari 3 dosen; aturan tidak tersedia")
            if not errs:
                try:
                    target = bagi_tm_sks(len(dosen), sks_mk)
                except ValueError as e:
                    errs.append(str(e))
                else:
                    for d, (tr, tl, sk) in zip(dosen, target):
                        cur = (str(d.get("jml_tm_renc", "")), str(d.get("jml_tm_real", "")),
                               fmt_sks(d.get("sks_subst_tot")))
                        want = (str(tr), str(tl), sk)
                        if cur != want:
                            changes.append({
                                "id_ajar": d["id_ajar"], "nm_sdm": d.get("nm_sdm", ""),
                                "before": {"jml_tm_renc": cur[0], "jml_tm_real": cur[1],
                                           "sks_subst_tot": cur[2]},
                                "after": {"jml_tm_renc": want[0], "jml_tm_real": want[1],
                                          "sks_subst_tot": want[2]},
                            })
        snapshot = [{"id_ajar": d["id_ajar"], "nm_sdm": d.get("nm_sdm", ""),
                     "jml_tm_renc": str(d.get("jml_tm_renc", "")),
                     "jml_tm_real": str(d.get("jml_tm_real", "")),
                     "sks_subst_tot": fmt_sks(d.get("sks_subst_tot"))}
                    for d in (data.get("dosen") or [])] if isinstance(data, dict) else []
        item = {"id": idx, "target": {"kode_mk": c.get("kode_mk", ""),
                                      "kelas": c.get("nm_kls", ""),
                                      "matkul": c.get("nm_mk", ""),
                                      "id_kls": c["id_kls"],
                                      "sks_mk": fmt_sks(sks_mk) if sks_mk is not None else "",
                                      "semester": sem["id_smt"]},
                "snapshot": snapshot, "changes": changes, "errors": errs}
        (errors if errs else items).append(item)
        if progress:
            progress(idx, total)
    for missing_idx, (kode, kelas) in enumerate(missing, len(targets) + 1):
        errors.append({"id": missing_idx,
                       "target": {"kode_mk": kode, "kelas": kelas},
                       "snapshot": [], "changes": [],
                       "errors": ["kelas tidak ditemukan"]})
    items = [it for it in items if it["changes"] or it["errors"]]
    return {"items": items, "errors": errors,
            "summary": {"kelas_diperiksa": total, "perlu_perbaikan": sum(bool(it["changes"]) for it in items),
                        "error": len(errors)}}


def apply_tm_sks_fix(client, ctx, item):
    if item.get("errors"):
        return {"status": "fail", "message": "; ".join(item["errors"])}
    try:
        det = client.get("/kelas/detail/" + item["target"]["id_kls"])
    except FeederError as e:
        return {"status": "konflik", "message": "kelas tidak dapat divalidasi: " + e.message}
    data = det.get("data") if isinstance(det, dict) else None
    if not isinstance(data, dict):
        return {"status": "konflik", "message": "detail kelas gagal dibaca"}
    dosen = data.get("dosen") or []
    try:
        current_sks_value = strict_dec(data.get("detail", {}).get("sks_mk"))
        if current_sks_value <= 0:
            raise ValueError
        current_sks = fmt_sks(current_sks_value)
    except ValueError:
        return {"status": "konflik", "message": "SKS mata kuliah tidak valid"}
    if current_sks != item["target"]["sks_mk"]:
        return {"status": "konflik", "message": "SKS mata kuliah berubah sejak preview"}
    cur = {d["id_ajar"]: d for d in dosen}
    if [d["id_ajar"] for d in dosen] != [snap["id_ajar"] for snap in item["snapshot"]]:
        return {"status": "konflik", "message": "susunan dosen berubah sejak preview"}
    for snap in item["snapshot"]:
        d = cur[snap["id_ajar"]]
        if ((d.get("nm_sdm") or "") != snap["nm_sdm"]
                or str(d.get("jml_tm_renc", "")) != snap["jml_tm_renc"]
                or str(d.get("jml_tm_real", "")) != snap["jml_tm_real"]
                or fmt_sks(d.get("sks_subst_tot")) != snap["sks_subst_tot"]):
            return {"status": "konflik", "message": "data dosen berubah sejak preview"}
    for ch in item["changes"]:
        d = cur.get(ch["id_ajar"])
        if not d or (d.get("nm_sdm") or "") != ch["nm_sdm"]:
            return {"status": "konflik", "message": f"{ch['nm_sdm']} berubah sejak preview"}
        for k, v in ch["before"].items():
            got = fmt_sks(d.get(k)) if k == "sks_subst_tot" else str(d.get(k, ""))
            if got != v:
                return {"status": "konflik", "message": f"{ch['nm_sdm']} nilai berubah sejak preview"}
    applied = 0
    for ch in item["changes"]:
        try:
            rec = client.get("/kelas/edit/dosen/" + ch["id_ajar"])
            rec = rec.get("data", rec) if isinstance(rec, dict) else rec
            if not isinstance(rec, dict) or "id_ajar" not in rec:
                result = {"status": "fail", "message": f"form dosen {ch['nm_sdm']} tak terbaca"}
            elif ((rec.get("nm_sdm") or "") != ch["nm_sdm"]
                  or any((fmt_sks(rec.get(k)) if k == "sks_subst_tot" else str(rec.get(k, ""))) != v
                         for k, v in ch["before"].items())):
                result = {"status": "konflik", "message": f"{ch['nm_sdm']} berubah sebelum ditulis"}
            else:
                rec.update(ch["after"])
                try:
                    res = client.put("/kelas/edit/dosen/" + ch["id_ajar"], payload=rec)
                except FeederError as e:
                    result = _write_failure(e)
                    if applied:
                        return {"status": "unknown",
                                "message": f"parsial: {applied} baris tersimpan; {result['message']}"}
                    return result
                code = res.get("code") if isinstance(res, dict) else None
                if code == 200:
                    applied += 1
                    continue
                msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
                result = {"status": "fail", "message": f"{ch['nm_sdm']}: {msg}"}
        except FeederError as e:
            result = (_readback_failure(e) if applied
                      else {"status": "konflik", "message": e.message})
        if applied:
            return {"status": "unknown",
                    "message": f"parsial: {applied} baris tersimpan; {result['message']}"}
        return result
    try:
        back = client.get("/kelas/detail/" + item["target"]["id_kls"])
    except FeederError as e:
        return _readback_failure(e)
    data2 = back.get("data") if isinstance(back, dict) else None
    if not isinstance(data2, dict):
        return {"status": "unknown", "message": "tersimpan tetapi verifikasi gagal dibaca"}
    now = {d["id_ajar"]: d for d in (data2.get("dosen") or [])}
    for ch in item["changes"]:
        d = now.get(ch["id_ajar"])
        if not d:
            return {"status": "unknown", "message": f"{ch['nm_sdm']} tidak terbaca ulang"}
        if (str(d.get("jml_tm_renc")) != ch["after"]["jml_tm_renc"]
                or str(d.get("jml_tm_real")) != ch["after"]["jml_tm_real"]
                or fmt_sks(d.get("sks_subst_tot")) != ch["after"]["sks_subst_tot"]):
            return {"status": "unknown", "message": f"verifikasi {ch['nm_sdm']} tidak cocok"}
    final_rows = data2.get("dosen") or []
    try:
        final_tm_renc = sum(strict_dec(d.get("jml_tm_renc")) for d in final_rows)
        final_tm_real = sum(strict_dec(d.get("jml_tm_real")) for d in final_rows)
        final_sks = sum(strict_dec(d.get("sks_subst_tot")) for d in final_rows)
    except ValueError:
        return {"status": "unknown", "message": "tersimpan tetapi total akhir tidak valid"}
    if (final_tm_renc != 16 or final_tm_real != 16
            or final_sks != strict_dec(item["target"]["sks_mk"])):
        return {"status": "unknown", "message": "tersimpan tetapi total akhir tidak sesuai"}
    return {"status": "ok", "message": f"{item['target']['kode_mk']}-{item['target']['kelas']} {len(item['changes'])} baris terverifikasi"}


# ---------------------------------------------------------------------------
# tulis: isi nilai konversi aktivitas


def _konversi_norm(s):
    return "".join((s or "").lower().split())


def _konversi_nama_pendek(nm_mk):
    """'SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023' -> 'Skripsi I'."""
    s = str(nm_mk or "").split(" - ", 1)[-1]
    return s.split(" (", 1)[0].strip()


def _konversi_sudah_ada(konv, mk):
    """Record konversi tak menyimpan id_mk; cocokkan nama pendek + SKS."""
    mau = _konversi_norm(_konversi_nama_pendek(mk.get("nm_mk")))
    return any(_konversi_norm(k.get("nm_mk")) == mau
               and fmt_sks(k.get("sks_mk")) == fmt_sks(mk.get("sks_mk"))
               for k in konv)


def _konversi_cocok(baca_ulang, nilai, huruf, indeks):
    """Feeder membulatkan nilai_angka ke 2 desimal dan memberi spasi di huruf."""
    try:
        return (fmt_nilai(baca_ulang.get("nilai_angka")) == fmt_nilai(nilai)
                and (baca_ulang.get("nilai_huruf") or "").strip() == huruf
                and fmt_sks(baca_ulang.get("nilai_indeks")) == indeks)
    except (InvalidOperation, TypeError, ValueError):
        return False


def _konversi_band(rows, id_sms, nilai):
    matches = []
    for r in rows:
        if r.get("id_sms") != id_sms:
            continue
        try:
            minimum = strict_dec(r.get("bobot_nilai_min"))
            maximum = strict_dec(r.get("bobot_nilai_maks"))
            indeks = fmt_sks(strict_dec(r.get("nilai_indeks")))
        except ValueError:
            raise FeederError("response", "Rentang bobot nilai Feeder tidak valid")
        if minimum <= nilai <= maximum:
            matches.append(((r.get("nilai_huruf") or "").strip(), indeks))
    if len(matches) > 1:
        raise FeederError("response", "Rentang bobot nilai Feeder tumpang tindih")
    return matches[0] if matches else (None, None)


def _konversi_bands(client):
    rows, _ = page_rows(client, "/bobotnilai/index", {
        "pagination": {"sortBy": "s.id_sms,m.nilai_huruf", "descending": False,
                       "page": 1, "rowsPerPage": 200, "rowsNumber": 0},
        "filter": "", "columnSearch": ["s.nm_lemb"],
        "setFilter": {"prodi": []}, "extendFilter": {"filter_sync": "semua"},
        "sortList": None})
    return rows


def _konversi_mk(client, kode):
    d = client.get("/matakuliah/search/cari_matakuliah", params={"nm_mk": kode})
    raw = d.get("list_matakuliah") if isinstance(d, dict) else None
    rows = raw[0] if raw and isinstance(raw[0], list) else []
    for r in rows:
        if str(r.get("nm_mk") or "").startswith(kode + " "):
            return r
    return None


def _konversi_detail(client, id_ang):
    """Transfermatkul berisi konversi[] + data[] (identitas anggota)."""
    t = client.get("/nilaiaktmhs/transfermatkul/" + id_ang)
    return t if isinstance(t, dict) else {}


def _konversi_terpasang(client, id_ang):
    return _konversi_detail(client, id_ang).get("konversi") or []


def _konversi_identitas(detail):
    """nipd/nm_pd hanya ada di transfermatkul.data[], bukan transfermatkuladd."""
    rows = detail.get("data")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _konversi_peta(client, acts, cancel=None, workers=8):
    """Peta {nim: (aktivitas, anggota)}; GET anggota paralel agar tidak lambat."""
    def ambil(a):
        if cancel and cancel():
            raise Cancelled()
        det = client.get("/aktmhs/edit/" + a["id_akt_mhs"])
        return a, (det.get("anggota") or [] if isinstance(det, dict) else [])

    peta = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for a, anggota in ex.map(ambil, acts):
            for m in anggota:
                nim = str(m.get("nipd") or "").strip()
                if nim:
                    peta.setdefault(nim, []).append((a, m))
    return peta


def _konversi_payload(anggota, mk, nilai, huruf, indeks, nm_smt):
    id_smt = anggota["id_smt"]
    return {
        "id_akt_mhs": anggota["id_akt_mhs"],
        "id_ang_akt_mhs": anggota["id_ang_akt_mhs"],
        "id_sms": anggota["id_sms"],
        "id_smtt": id_smt, "id_smt": id_smt, "id_smt_masking": nm_smt,
        "filter_periode": {"id_smt": id_smt, "nm_smt": nm_smt},
        "filter_matakuliah": {"id_mk": mk["id_mk"], "nm_mk": mk["nm_mk"]},
        "filter_nilai_huruf": {"nilai_indeks": f"{huruf}#{indeks.replace('.', ',')}",
                               "column": f"{huruf} ({indeks.replace('.', ',')})"},
        "id_mk": mk["id_mk"], "sks_mk": mk["sks_mk"],
        "nilai_angka": nilai, "nilai_huruf": huruf, "nilai_indeks": indeks,
    }


def preview_konversi_isi(client, ctx, form, progress=None, cancel=None):
    sem = semester_of(ctx, form)
    rows = form.get("rows") or []
    default_mk = (form.get("kode_mk") or form.get("mk") or "").strip().upper()
    jenis = (form.get("jenis") or "").strip()
    if not rows:
        raise FeederError("input", "Tidak ada baris untuk diproses")
    acts = fetch_all(client, "/aktmhs/index", sem, prodi_ids=prodi_ids_of(ctx, form),
                     extra_setfilter={"jenisaktivitas": [], "asaldata": []},
                     column_search=["m.judul_akt_mhs", "m.sk_tugas", "angg.nm_pd",
                                    "angg.nipd", "uji.nm_sdm", "bimb.nm_sdm"],
                     sort_by="st.nm_smt", cancel=cancel)
    if jenis:
        acts = [a for a in acts if jenis.lower() in (a.get("nm_jns_akt_mhs") or "").lower()]
    if progress:
        progress(0, len(acts), "Memetakan anggota aktivitas")
    peta = _konversi_peta(client, acts, cancel=cancel)
    bands = _konversi_bands(client)
    mks, items, errors, seen = {}, [], [], set()
    total = len(rows)
    for i, row in enumerate(rows, 1):
        if cancel and cancel():
            raise Cancelled()
        nim = str(row.get("nim") or "").strip()
        kode = str(row.get("kode_mk") or row.get("mk") or default_mk).strip().upper()
        nama = str(row.get("nama") or "").strip()
        nilai_raw = str(row.get("nilai") or "").strip()
        errs = []
        if not nim:
            errs.append("NIM kosong")
        elif (nim, kode) in seen:
            errs.append("NIM + MK duplikat dalam CSV")
        else:
            seen.add((nim, kode))
        if not kode:
            errs.append("kode MK kosong")
        try:
            nilai = strict_dec(nilai_raw)
            if nilai < 0 or nilai > 100:
                raise ValueError
        except ValueError:
            nilai = None
            errs.append("nilai harus angka 0-100")
        matches = peta.get(nim, []) if nim else []
        if nim and not matches:
            errs.append("NIM bukan anggota aktivitas pada semester ini")
        elif len(matches) > 1:
            errs.append("NIM muncul di lebih dari satu aktivitas; pilih jenis/semester lebih spesifik")
        mk = None
        if kode:
            if kode not in mks:
                mks[kode] = _konversi_mk(client, kode)
            mk = mks[kode]
            if not mk:
                errs.append(f"matakuliah {kode} tidak ditemukan")
        huruf = indeks = None
        konv = []
        if not errs:
            a, m = matches[0]
            feeder_nama = str(m.get("nm_pd") or "").strip()
            if nama and _konversi_norm(nama) != _konversi_norm(feeder_nama):
                errs.append("nama tidak cocok dengan NIM di Feeder")
            if not errs:
                nilai_raw = fmt_nilai(nilai)
                try:
                    huruf, indeks = _konversi_band(
                        bands, a.get("id_sms"), strict_dec(nilai_raw))
                except FeederError as e:
                    errs.append("gagal baca band nilai: " + e.message)
                else:
                    if not huruf:
                        errs.append("nilai di luar semua rentang nilai prodi")
            if not errs:
                try:
                    konv = _konversi_terpasang(client, m["id_ang_akt_mhs"])
                except FeederError as e:
                    errs.append("gagal baca nilai konversi: " + e.message)
                else:
                    if _konversi_sudah_ada(konv, mk):
                        errs.append("sudah ada nilai untuk MK ini")
        sks_mk = None
        if not errs:
            try:
                sks_mk = strict_dec(mk.get("sks_mk"))
                if sks_mk <= 0:
                    raise ValueError
            except ValueError:
                errs.append("SKS mata kuliah tidak valid")
        if errs:
            errors.append({"id": i, "target": {"nim": nim, "nama": nama,
                                               "kode_mk": kode, "semester": sem["id_smt"],
                                               "nm_smt": sem["nm_smt"]},
                           "errors": errs})
            if progress:
                progress(i, total, "Memeriksa")
            continue
        a, m = matches[0]
        items.append({
            "id": i,
            "target": {"nim": nim, "nama": nama or m.get("nm_pd", ""), "kode_mk": kode,
                       "matkul": mk.get("nm_mk", ""), "semester": sem["id_smt"],
                       "nm_smt": sem["nm_smt"]},
            "before": {"nilai": "-", "huruf": "-", "indeks": "-"},
            "after": {"nilai": nilai_raw, "huruf": huruf, "indeks": indeks},
            "mk": {"kode": kode, "nm_mk": mk["nm_mk"], "id_mk": mk["id_mk"],
                   "sks_mk": fmt_sks(sks_mk), "id_sms": a.get("id_sms")},
            "snapshot": {"id_ang_akt_mhs": m["id_ang_akt_mhs"],
                          "id_akt_mhs": a["id_akt_mhs"],
                         "nim": nim, "nama": str(m.get("nm_pd") or "").strip(),
                         "konversi_ids": [str(k.get("id_konversi_akt_mhs")) for k in konv]},
            "errors": [],
        })
        if progress:
            progress(i, total, "Memeriksa")
    return {"items": items, "errors": errors,
            "summary": {"siap": len(items), "error": len(errors)}}


def apply_konversi_isi(client, ctx, item):
    if item.get("errors"):
        return {"status": "fail", "message": "; ".join(item["errors"])}
    snap, mk = item["snapshot"], item["mk"]
    try:
        detail = _konversi_detail(client, snap["id_ang_akt_mhs"])
    except FeederError as e:
        return _readback_failure(e)
    konv = detail.get("konversi") or []
    if sorted(str(k.get("id_konversi_akt_mhs")) for k in konv) != sorted(snap["konversi_ids"]):
        return {"status": "konflik", "message": "nilai konversi berubah sejak preview"}
    if _konversi_sudah_ada(konv, mk):
        return {"status": "konflik", "message": "MK ini sudah punya nilai konversi"}
    ident = _konversi_identitas(detail)
    if ident and (str(ident.get("nipd") or "").strip() != snap["nim"]
                  or _konversi_norm(ident.get("nm_pd")) != _konversi_norm(snap["nama"])):
        return {"status": "konflik", "message": "anggota aktivitas berubah sejak preview"}
    try:
        current_mk = _konversi_mk(client, mk["kode"])
        current_bands = _konversi_bands(client)
        current_huruf, current_indeks = _konversi_band(
            current_bands, mk["id_sms"], strict_dec(item["after"]["nilai"]))
    except FeederError as e:
        return {"status": "konflik", "message": "referensi nilai tidak dapat divalidasi: " + e.message}
    try:
        current_sks = strict_dec(current_mk.get("sks_mk")) if current_mk else None
    except ValueError:
        current_sks = None
    if (not current_mk or str(current_mk.get("id_mk")) != str(mk["id_mk"])
            or current_sks is None or current_sks <= 0
            or fmt_sks(current_sks) != mk["sks_mk"]):
        return {"status": "konflik", "message": "mata kuliah berubah sejak preview"}
    if (current_huruf, current_indeks) != (item["after"]["huruf"], item["after"]["indeks"]):
        return {"status": "konflik", "message": "band nilai berubah sejak preview"}
    try:
        tpl = client.get("/nilaiaktmhs/transfermatkuladd/" + snap["id_ang_akt_mhs"])
    except FeederError as e:
        return {"status": "konflik", "message": "form konversi tidak terbaca: " + e.message}
    if not isinstance(tpl, dict) or not isinstance(tpl.get("anggota"), dict):
        return {"status": "fail", "message": "form konversi tidak terbaca"}
    anggota = dict(tpl["anggota"], id_ang_akt_mhs=snap["id_ang_akt_mhs"])
    if (str(anggota.get("id_akt_mhs")) != str(snap["id_akt_mhs"])
            or str(anggota.get("id_sms")) != str(mk["id_sms"])
            or str(anggota.get("id_smt")) != str(item["target"]["semester"])):
        return {"status": "konflik", "message": "target aktivitas berubah sejak preview"}
    nm_smt = (tpl.get("filter_periode") or {}).get("nm_smt") or ctx["active_sem"]["nm_smt"]
    body = _konversi_payload(anggota, mk, item["after"]["nilai"],
                             item["after"]["huruf"], item["after"]["indeks"], nm_smt)
    try:
        res = client.post("/nilaiaktmhs/transfermatkul/add", payload=body)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        back = _konversi_terpasang(client, snap["id_ang_akt_mhs"])
    except FeederError as e:
        return _readback_failure(e)
    baru = [k for k in back
            if str(k.get("id_konversi_akt_mhs")) not in set(snap["konversi_ids"])]
    if not baru:
        return {"status": "unknown", "message": "tersimpan tetapi tidak terbaca ulang"}
    cocok = [k for k in baru if _konversi_sudah_ada([k], mk)]
    if len(cocok) != 1:
        return {"status": "unknown", "message": "tersimpan tetapi target baca-ulang ambigu"}
    if _konversi_cocok(cocok[0], item["after"]["nilai"],
                       item["after"]["huruf"], item["after"]["indeks"]):
        return {"status": "ok",
                "message": f"{item['target']['nim']} {mk['kode']} "
                           f"{cocok[0].get('nilai_angka')} {item['after']['huruf']} terverifikasi"}
    return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}


def check():
    """Self-check offline: aturan TM/SKS, decimal, CSV safety."""
    assert bagi_tm_sks(1, 2) == [(16, 16, "2.00")]
    assert bagi_tm_sks(2, 2) == [(8, 8, "1.00"), (8, 8, "1.00")]
    assert bagi_tm_sks(3, 2) == [(5, 5, "0.67"), (5, 5, "0.67"), (6, 6, "0.66")]
    assert bagi_tm_sks(3, 3) == [(5, 5, "1.00"), (5, 5, "1.00"), (6, 6, "1.00")]
    assert sum(dec(x[2]) for x in bagi_tm_sks(3, 2)) == Decimal("2.00")
    try:
        bagi_tm_sks(4, 2)
        raise AssertionError("4 dosen harus ditolak")
    except ValueError:
        pass
    assert fmt_sks("1.3") == "1.30"
    assert csv_safe("202500001") == "202500001"
    assert csv_safe("=SUM(A1)") == "'=SUM(A1)"
    assert blank(None) and blank("") and not blank("x")
    mk = {"nm_mk": "SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023", "sks_mk": "2.00"}
    assert _konversi_nama_pendek(mk["nm_mk"]) == "Skripsi I"
    assert _konversi_sudah_ada([{"nm_mk": "Skripsi I", "sks_mk": "2.00"}], mk)
    assert not _konversi_sudah_ada([{"nm_mk": "SKRIPSI II", "sks_mk": "4.00"}], mk)
    assert not _konversi_sudah_ada([{"nm_mk": "Skripsi I", "sks_mk": "4.00"}], mk)
    bands = [{"id_sms": "p", "nilai_huruf": "B+", "bobot_nilai_min": "75.0100",
              "bobot_nilai_maks": "80.0000", "nilai_indeks": "3.50"},
             {"id_sms": "p", "nilai_huruf": "A", "bobot_nilai_min": "80.0100",
              "bobot_nilai_maks": "100.0000", "nilai_indeks": "4.00"}]
    assert _konversi_band(bands, "p", Decimal("80.17875")) == ("A", "4.00")
    assert _konversi_band(bands, "p", Decimal("75.01")) == ("B+", "3.50")
    assert _konversi_band(bands, "q", Decimal("80")) == (None, None)
    assert _konversi_cocok({"nilai_angka": "80.18", "nilai_huruf": "A  ",
                            "nilai_indeks": "4.00"}, "80.17875", "A", "4.00")
    assert _konversi_cocok({"nilai_angka": "79.09", "nilai_huruf": "B+",
                            "nilai_indeks": "3.50"}, "79.085", "B+", "3.50")
    assert not _konversi_cocok({"nilai_angka": "79.06", "nilai_huruf": "B+",
                                 "nilai_indeks": "3.50"}, "80.17875", "A", "4.00")
    assert _bobotnilai_tgl("2021-01-31T00:00:00.000Z") == "2021-01-31"
    assert _akm_studi("Aktif") and _akm_studi("Kampus Merdeka") and _akm_studi("")
    assert not _akm_studi("Keluar") and not _akm_studi("Cuti")
    assert not _akm_studi("Non-Aktif                           ")
    assert not _akm_studi("Lulus") and not _akm_studi("Mengajukan pengunduran diri")
    assert not _akm_studi("tidak aktif")
    assert _bobotnilai_sama("bobot_nilai_min", "65.0100", "65.01")
    assert not _bobotnilai_sama("bobot_nilai_min", "65.0100", "65.02")
    assert mhs_normal("jk", "L") == "Laki-laki"
    assert mhs_normal("a_terima_kps", "ya") == "Ya"
    assert mhs_normal("nik", " 3201010101010001 ") == "3201010101010001"
    assert mhs_normal("tgl_lahir", "2000-01-02") == "2000-01-02"
    for bad in [("jk", "X"), ("a_terima_kps", "mungkin"), ("nik", ""),
                ("nik", "123"), ("tgl_lahir", "02-01-2000"),
                ("tgl_lahir_wali", "2026-02-31")]:
        try:
            mhs_normal(*bad)
            raise AssertionError(f"harus ditolak: {bad}")
        except ValueError:
            pass
    assert "id_wil" in MHS_FIELDS and "csf" not in MHS_FIELDS
    assert _dayatampung_sama("target_mhs_baru", "6", "6")
    assert not _dayatampung_sama("target_mhs_baru", "6", "7")
    assert _dayatampung_sama("tgl_awal_kul", "2026-03-01T00:00:00.000Z", "2026-03-01")
    assert _dayatampung_valid("target_mhs_baru", "120") is None
    assert _dayatampung_valid("target_mhs_baru", "abc") is not None
    assert _dayatampung_valid("tgl_awal_kul", "2026-03-01") is None
    assert _dayatampung_valid("tgl_awal_kul", "2026-02-31") is not None
    assert _dayatampung_anomali({"last_update": "2026-04-07",
                                 "last_sync": "2026-01-01",
                                 "target_mhs_baru": "0"}, set()) == \
        ["belum sync", "target 0", "tidak bisa edit"]
    assert _dayatampung_anomali({"last_update": "2026-01-01",
                                 "last_sync": "2026-04-07",
                                 "target_mhs_baru": "6",
                                 "id_smt": "20252", "id_sms": "p1"},
                                {("20252", None)}) == []
    assert mhs_add_row({"nim": "9"})["nipd"] == "9"
    assert mhs_add_row({"nipd": "9"})["nim"] == "9"
    assert mhs_anomali({"nik": "1"}) == [
        "NISN kosong", "email kosong", "HP kosong", "kelurahan kosong",
        "kecamatan kosong", "nama ibu kosong", "tempat lahir kosong",
        "tanggal lahir kosong", "agama kosong", "kewarganegaraan kosong",
        "jenis kelamin kosong", "NIK bukan 16 digit"]
    print("check ok: web_operations aturan TM/SKS + konversi + CSV safety")


# ---------------------------------------------------------------------------
# audit + tulis: bobot nilai (skala nilai per prodi)

BOBOTNILAI_FIELDS = {"bobot_nilai_min", "bobot_nilai_maks", "nilai_indeks",
                     "nilai_huruf", "tgl_mulai_efektif", "tgl_akhir_efektif"}


def _bobotnilai_tgl(v):
    return str(v or "").split("T")[0]


def _bobotnilai_sama(field, a, b):
    if field in ("bobot_nilai_min", "bobot_nilai_maks", "nilai_indeks"):
        try:
            return strict_dec(a) == strict_dec(b)
        except ValueError:
            return False
    if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
        return _bobotnilai_tgl(a) == _bobotnilai_tgl(b)
    return (a or "").strip() == (b or "").strip()


def _bobotnilai_valid(field, nilai):
    if field in ("bobot_nilai_min", "bobot_nilai_maks", "nilai_indeks"):
        try:
            strict_dec(nilai)
        except ValueError:
            return f"{field} harus angka, dapat {nilai!r}"
    if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
        p = str(nilai).split("T")[0].split("-")
        if len(p) != 3 or len(p[0]) != 4 or not all(x.isdigit() for x in p):
            return f"{field} harus tanggal YYYY-MM-DD, dapat {nilai!r}"
    if not str(nilai).strip():
        return f"{field} tidak boleh kosong"
    return None


def _bobotnilai_rows(client, cancel=None):
    rows, _ = page_rows(client, "/bobotnilai/index", {
        "pagination": {"sortBy": "s.id_sms,m.nilai_huruf", "descending": False,
                       "page": 1, "rowsPerPage": 200, "rowsNumber": 0},
        "filter": "", "columnSearch": ["s.nm_lemb"],
        "setFilter": {"prodi": []}, "extendFilter": {"filter_sync": "semua"},
        "sortList": None})
    return rows


def _bobotnilai_tumpang(rows):
    out, grup = [], {}
    for r in rows:
        grup.setdefault(r.get("nm_lemb", ""), []).append(r)
    for prodi, rs in sorted(grup.items()):
        huruf = [r.get("nilai_huruf", "") for r in rs]
        for h in set(huruf):
            if huruf.count(h) > 1:
                out.append({"prodi": prodi, "rentang": f"huruf {h!r} dobel"})
        try:
            urut = sorted(rs, key=lambda r: strict_dec(r.get("bobot_nilai_min") or 0))
        except ValueError:
            out.append({"prodi": prodi, "rentang": "ada min/maks bukan angka"})
            continue
        for a, b in zip(urut, urut[1:]):
            try:
                tumpang = strict_dec(a.get("bobot_nilai_maks") or 0) > \
                    strict_dec(b.get("bobot_nilai_min") or 0)
            except ValueError:
                out.append({"prodi": prodi, "rentang": "ada min/maks bukan angka"})
                break
            if tumpang:
                out.append({"prodi": prodi,
                            "rentang": f"{a.get('nilai_huruf')} tumpang tindih {b.get('nilai_huruf')}"})
    return out


def _bobotnilai_prodi(client):
    d = client.get("/bobotnilai/filter")
    rows = (d.get("list_prodi") or [[]])[0] if isinstance(d, dict) else []
    return {r["nm_lemb"]: r for r in rows if isinstance(r, dict)}


def run_bobotnilai(client, ctx, params, progress, cancel):
    rows = _bobotnilai_rows(client, cancel=cancel)
    if params.get("prodi"):
        ids = {p.get("id_sms") for p in prodi_ids_of(ctx, params)}
        rows = [r for r in rows if r.get("id_sms") in ids]
    out = []
    for r in rows:
        out.append({
            "prodi": r.get("nm_lemb", ""), "huruf": r.get("nilai_huruf", ""),
            "min": r.get("bobot_nilai_min", ""), "maks": r.get("bobot_nilai_maks", ""),
            "indeks": r.get("nilai_indeks", ""),
            "tgl_mulai": _bobotnilai_tgl(r.get("tgl_mulai_efektif")),
            "tgl_akhir": _bobotnilai_tgl(r.get("tgl_akhir_efektif")),
            "anomali": [], "id": r.get("id", ""),
        })
    peringatan = _bobotnilai_tumpang(rows)
    by_prodi = {}
    for w in peringatan:
        by_prodi.setdefault(w["prodi"], []).append(w["rentang"])
    for o in out:
        o["anomali"] = by_prodi.get(o["prodi"], [])
    summary = {"total": len(out), "tumpang_tindih": len(peringatan)}
    return {
        "rows": out, "summary": summary,
        "columns": [("prodi", "Prodi"), ("huruf", "Huruf"),
                    ("min", "Nilai min"), ("maks", "Nilai maks"),
                    ("indeks", "Indeks"), ("tgl_mulai", "Mulai efektif"),
                    ("tgl_akhir", "Akhir efektif"), ("anomali", "Anomali")],
    }


def preview_bobotnilai_edit(client, ctx, form, progress=None, cancel=None):
    rows = form.get("rows") or []
    if not rows:
        raise FeederError("input", "Tidak ada baris untuk diproses")
    prodis = _bobotnilai_prodi(client)
    semua = _bobotnilai_rows(client, cancel=cancel)
    items, errors, seen = [], [], set()
    for i, row in enumerate(rows, 1):
        if cancel and cancel():
            raise Cancelled()
        prodi = str(row.get("prodi") or "").strip()
        huruf = str(row.get("nilai_huruf") or row.get("huruf") or "").strip()
        field = str(row.get("field") or "").strip()
        nilai = str(row.get("nilai") or "").strip()
        errs = []
        key = (prodi, huruf, field)
        if key in seen:
            errs.append("target/field duplikat dalam CSV")
        else:
            seen.add(key)
        if prodi not in prodis:
            errs.append(f"prodi {prodi!r} tidak ada di Feeder")
        if field not in BOBOTNILAI_FIELDS:
            errs.append(f"field {field!r} tidak diizinkan")
        else:
            err = _bobotnilai_valid(field, nilai)
            if err:
                errs.append(err)
        hit = [b for b in semua if b.get("nm_lemb") == prodi
               and (b.get("nilai_huruf") or "").strip() == huruf] if prodi and huruf else []
        if prodi and huruf and len(hit) != 1:
            errs.append(f"band {huruf!r} prodi {prodi!r}: ketemu {len(hit)}, harus 1")
        cur = hit[0] if len(hit) == 1 else {}
        if field in ("tgl_mulai_efektif", "tgl_akhir_efektif") and nilai:
            nilai = nilai.split("T")[0]
        item = {"id": i,
                "target": {"prodi": prodi, "huruf": huruf, "field": field,
                           "id": cur.get("id", "") if len(hit) == 1 else ""},
                "before": {"nilai": cur.get(field, "") if len(hit) == 1 else ""},
                "after": {"nilai": nilai},
                "errors": errs}
        (errors if errs else items).append(item)
        if progress:
            progress(i, len(rows))
    return {"items": items, "errors": errors,
            "summary": {"siap": len(items), "error": len(errors)}}


def apply_bobotnilai_edit(client, ctx, item):
    if item.get("errors"):
        return {"status": "fail", "message": "; ".join(item["errors"])}
    field = item["target"]["field"]
    nilai = item["after"]["nilai"]
    if field not in BOBOTNILAI_FIELDS:
        return {"status": "fail", "message": "field tidak diizinkan"}
    try:
        form = client.get("/bobotnilai/edit/" + item["target"]["id"])
    except FeederError as e:
        return {"status": "konflik", "message": "band tidak dapat dibaca: " + e.message}
    if not isinstance(form, dict) or "id_sms" not in form:
        return {"status": "konflik", "message": "form band tak terbaca"}
    if not _bobotnilai_sama(field, form.get(field), item["before"]["nilai"]):
        return {"status": "konflik", "message": "nilai berubah sejak preview"}
    if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
        nilai = str(nilai).split("T")[0]
    form[field] = nilai
    try:
        res = client.put("/bobotnilai/edit/" + item["target"]["id"], payload=form)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        back = client.get("/bobotnilai/edit/" + item["target"]["id"])
    except FeederError as e:
        return _readback_failure(e)
    back = back if isinstance(back, dict) else {}
    if _bobotnilai_sama(field, back.get(field), nilai):
        t = item["target"]
        return {"status": "ok",
                "message": f"{t['prodi']} {t['huruf']} {field} terverifikasi"}
    return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}


# ---------------------------------------------------------------------------
# audit + tulis: mahasiswa (menu /mahasiswa)

MHS_AUDIT_WAJIB = [
    ("nik", "NIK"), ("nisn", "NISN"), ("email", "email"), ("no_hp", "HP"),
    ("ds_kel", "kelurahan"), ("id_wil", "kecamatan"), ("nm_ibu_kandung", "nama ibu"),
    ("tmpt_lahir", "tempat lahir"), ("tgl_lahir", "tanggal lahir"),
    ("id_agama", "agama"), ("kewarganegaraan", "kewarganegaraan"),
    ("jk", "jenis kelamin"),
]


def mhs_anomali(d):
    """Aturan sama dengan mhs_check.py (audit_anomali)."""
    out = []
    for key, label in MHS_AUDIT_WAJIB:
        if blank(d.get(key)):
            out.append(f"{label} kosong")
    nik = str(d.get("nik") or "").strip()
    if nik and not (nik.isdigit() and len(nik) == 16):
        out.append("NIK bukan 16 digit")
    return out


def fetch_mhs_all(client, cari="", cancel=None, progress=None, ulang=3):
    """Paginasi /mahasiswa/index (sama dengan mhs_check.fetch_mhs).

    Feeder kadang balas {} untuk halaman berikutnya; diulang sebelum menyerah.
    """
    rows, page, total = [], 1, None
    while True:
        if cancel and cancel():
            raise Cancelled()
        params = {
            "pagination": {"sortBy": "P.nm_pd", "descending": False, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": cari,
            "columnSearch": ["P.nm_pd", "r.nipd", "P.jk", "nm_agama",
                             "s.nm_lemb", "r.mulai_smt"],
            "setFilter": {"prodi": [], "angkatan": [], "status_aktif_mahasiswa": [],
                          "jenis_kelamin": [], "agama": [], "jenis_pendaftaran": []},
            "extendFilter": {}, "sortList": None}
        batch = count = None
        for i in range(ulang):
            try:
                batch, count = page_rows(client, "/mahasiswa/index", params)
                break
            except FeederError as e:
                if e.kind != "response" or i == ulang - 1:
                    raise
                time.sleep(1 + i)
        total = count if total is None else total
        if count != total:
            raise FeederError("response", "Jumlah /mahasiswa/index berubah saat paginasi")
        rows += batch
        if progress:
            progress(len(rows), total)
        if len(batch) < 100:
            break
        page += 1
    if len(rows) != total:
        raise FeederError("response", "Paginasi /mahasiswa/index tidak lengkap")
    return rows


def run_mhs(client, ctx, params, progress, cancel):
    cari = (params.get("cari") or "").strip()
    status = (params.get("status") or "").strip()
    audit = params.get("audit")
    prodi_ids = {p["id_sms"] for p in prodi_ids_of(ctx, params)}
    rows = fetch_mhs_all(client, cari=cari, cancel=cancel, progress=progress)
    targets = [r for r in rows
               if (not prodi_ids or r.get("id_sms") in prodi_ids)
               and (not status or status.lower() in (r.get("nm_stat_mhs") or "").lower())]
    details = [None] * len(targets)
    if audit and targets:
        def satu(r):
            try:
                return client.get("/mahasiswa/detail/" + str(r.get("id_pd")))
            except FeederError:
                return None
        with ThreadPoolExecutor(max_workers=8) as ex:
            details = list(ex.map(satu, targets))
    out = []
    for r, d in zip(targets, details):
        anomali = []
        if audit:
            anomali = mhs_anomali(d) if d else ["detail gagal dibaca"]
        out.append({
            "nipd": r.get("nipd", ""), "nm_pd": r.get("nm_pd", ""),
            "jk": "Laki-laki" if r.get("jk") == "L" else "Perempuan",
            "nm_agama": r.get("nm_agama", ""),
            "tgl_lahir": str(r.get("tgl_lahir") or "")[:10],
            "nm_lemb": r.get("nm_lemb", ""), "nm_stat_mhs": r.get("nm_stat_mhs", ""),
            "mulai_smt": str(r.get("mulai_smt") or "")[:4],
            "anomali": anomali,
        })
    summary = {"total": len(out)}
    if audit:
        summary["anomali"] = sum(1 for r in out if r["anomali"])
    return {
        "rows": out, "summary": summary,
        "columns": [("nipd", "NIM"), ("nm_pd", "Nama"), ("jk", "JK"),
                    ("nm_agama", "Agama"), ("tgl_lahir", "Tgl lahir"),
                    ("nm_lemb", "Prodi"), ("nm_stat_mhs", "Status"),
                    ("mulai_smt", "Angkatan"), ("anomali", "Anomali")],
    }


MHS_TEXT_FIELDS = {"nm_pd", "tmpt_lahir", "tgl_lahir", "nik", "nisn", "npwp", "email",
                   "no_hp", "no_tel_rmh", "jln", "nm_dsn", "rt", "rw", "ds_kel",
                   "kode_pos", "nm_ibu_kandung", "nm_ayah", "nm_wali", "nik_ayah",
                   "nik_ibu", "tgl_lahir_ayah", "tgl_lahir_ibu", "tgl_lahir_wali"}
MHS_REF_FIELDS = {
    "id_agama": ("agama", "agama", "id_agama"),
    "id_wil": ("wilayah", "wilayah", "id_wil"),
    "id_alat_transport": ("alat_transportasi", "alat_transportasi", "id_alat_transport"),
    "id_jns_tinggal": ("jenis_tinggal", "jenis_tinggal", "id_jns_tinggal"),
    "id_jenjang_pendidikan_ayah": ("jenjang_pendidikan", "jenjang_pendidikan_ayah", "id_jenj_didik"),
    "id_jenjang_pendidikan_ibu": ("jenjang_pendidikan", "jenjang_pendidikan_ibu", "id_jenj_didik"),
    "id_jenjang_pendidikan_wali": ("jenjang_pendidikan", "jenjang_pendidikan_wali", "id_jenj_didik"),
    "id_pekerjaan_ayah": ("pekerjaan", "pekerjaan_ayah", "id_pekerjaan"),
    "id_pekerjaan_ibu": ("pekerjaan", "pekerjaan_ibu", "id_pekerjaan"),
    "id_pekerjaan_wali": ("pekerjaan", "pekerjaan_wali", "id_pekerjaan"),
    "id_penghasilan_ayah": ("penghasilan", "penghasilan_ayah", "id_penghasilan"),
    "id_penghasilan_ibu": ("penghasilan", "penghasilan_ibu", "id_penghasilan"),
    "id_penghasilan_wali": ("penghasilan", "penghasilan_wali", "id_penghasilan"),
}
MHS_ENUM_FIELDS = {"jk": ("Laki-laki", "Perempuan"), "a_terima_kps": ("Ya", "Tidak")}
MHS_NIK_FIELDS = {"nik", "nik_ayah", "nik_ibu"}
MHS_TGL_FIELDS = {"tgl_lahir", "tgl_lahir_ayah", "tgl_lahir_ibu", "tgl_lahir_wali"}
MHS_FIELDS = set(MHS_TEXT_FIELDS) | set(MHS_REF_FIELDS) | set(MHS_ENUM_FIELDS)


def mhs_normal(field, nilai):
    """Normalisasi nilai; aturan sama dengan mhs_edit.py (normal_nilai)."""
    nilai = (nilai or "").strip()
    if field == "jk":
        peta = {"l": "Laki-laki", "laki-laki": "Laki-laki", "p": "Perempuan",
                "perempuan": "Perempuan"}
        if nilai.lower() not in peta:
            raise ValueError("jk harus L/P")
        return peta[nilai.lower()]
    if field == "a_terima_kps":
        peta = {"ya": "Ya", "y": "Ya", "1": "Ya", "tidak": "Tidak", "t": "Tidak", "0": "Tidak"}
        if nilai.lower() not in peta:
            raise ValueError("a_terima_kps harus Ya/Tidak")
        return peta[nilai.lower()]
    if not nilai:
        raise ValueError("nilai kosong")
    if field in MHS_NIK_FIELDS and not (nilai.isdigit() and len(nilai) == 16):
        raise ValueError(f"{field} harus 16 digit angka")
    if field in MHS_TGL_FIELDS:
        try:
            if date.fromisoformat(nilai).isoformat() != nilai:
                raise ValueError
        except ValueError:
            raise ValueError(f"{field} harus tanggal YYYY-MM-DD")
    return nilai


def _mhs_select_items(client, kind):
    """Referensi /mahasiswa/select/edit, cache per klien (satu job web)."""
    data = getattr(client, "_mhs_select", None)
    if data is None:
        data = client.get("/mahasiswa/select/edit")
        client._mhs_select = data if isinstance(data, dict) else {}
    items = data.get(kind) or []
    return items[0] if items and isinstance(items[0], list) else items


def _mhs_ref_obj(client, field, nilai):
    kind, nested, idkey = MHS_REF_FIELDS[field]
    hit = [x for x in _mhs_select_items(client, kind)
           if str(x.get(idkey, "")).strip() == nilai]
    if len(hit) != 1:
        raise ValueError(f"{nilai} tak unik/tak ada di referensi {kind}")
    return hit[0]


def _mhs_ref_sama(detail, field, nilai):
    obj = detail.get(MHS_REF_FIELDS[field][1]) or {}
    return str(obj.get(MHS_REF_FIELDS[field][2], "")).strip() == nilai


def _mhs_isi_errors(row):
    """Validasi satu baris CSV nim,field,nilai (dipakai preview & apply)."""
    errs = []
    if not str(row.get("nim") or "").strip():
        errs.append("NIM kosong")
    field = str(row.get("field") or "").strip()
    if field not in MHS_FIELDS:
        errs.append(f"field {field!r} tidak diizinkan")
        return errs, field, None
    try:
        nilai = mhs_normal(field, str(row.get("nilai") or "").strip())
    except ValueError as e:
        errs.append(str(e))
        return errs, field, None
    return errs, field, nilai


def preview_mhs_edit(client, ctx, form, progress=None, cancel=None):
    rows = form.get("rows") or []
    index = _mhs_peta_nipd(client, cancel=cancel)
    details, gagal, items, errors, seen = {}, set(), [], [], set()
    for i, row in enumerate(rows, 1):
        if cancel and cancel():
            raise Cancelled()
        nim = str(row.get("nim") or "").strip()
        errs, field, nilai = _mhs_isi_errors(row)
        key = (nim, field)
        if key in seen:
            errs.append("NIM + field duplikat dalam CSV")
        else:
            seen.add(key)
        hit = index.get(nim) if nim else None
        if nim and not hit:
            errs.append("NIM tidak ditemukan")
        if nilai and field in MHS_REF_FIELDS:
            try:
                _mhs_ref_obj(client, field, nilai)
            except ValueError as e:
                errs.append(str(e))
        before = ""
        if hit and not errs:
            if nim in gagal:
                errs.append("detail gagal dibaca")
            else:
                if nim not in details:
                    try:
                        details[nim] = client.get("/mahasiswa/detail/" + str(hit.get("id_pd")))
                    except FeederError:
                        gagal.add(nim)
                        errs.append("detail gagal dibaca")
                d = details.get(nim)
                if d is not None and not errs:
                    before = (d.get(MHS_REF_FIELDS[field][1]) or {}).get(MHS_REF_FIELDS[field][2]) \
                        if field in MHS_REF_FIELDS else d.get(field)
        item = {"id": i,
                "target": {"nim": nim, "nama": (hit or {}).get("nm_pd", ""),
                           "field": field},
                "before": {"nilai": before if before is not None else ""},
                "after": {"nilai": nilai if nilai is not None else ""},
                "errors": errs}
        (errors if errs else items).append(item)
        if progress:
            progress(i, len(rows))
    return {"items": items, "errors": errors,
            "summary": {"siap": len(items), "error": len(errors)}}


def apply_mhs_edit(client, ctx, item):
    if item.get("errors"):
        return {"status": "fail", "message": "; ".join(item["errors"])}
    nim, field = item["target"]["nim"], item["target"]["field"]
    errs, field, nilai = _mhs_isi_errors({"nim": nim, "field": field,
                                          "nilai": item["after"]["nilai"]})
    if errs:
        return {"status": "fail", "message": "; ".join(errs)}
    try:
        hit = _mhs_peta_nipd(client).get(nim)
    except FeederError as e:
        return {"status": "konflik", "message": "daftar mahasiswa tak terbaca: " + e.message}
    if not hit:
        return {"status": "konflik", "message": "baris mahasiswa berubah sejak preview"}
    try:
        detail = client.get("/mahasiswa/detail/" + str(hit.get("id_pd")))
    except FeederError as e:
        return {"status": "konflik", "message": "detail tak terbaca: " + e.message}
    if field in MHS_REF_FIELDS:
        if not _mhs_ref_sama(detail, field, str(item["before"]["nilai"]).strip()):
            return {"status": "konflik", "message": "nilai berubah sejak preview"}
        try:
            nested = _mhs_ref_obj(client, field, nilai)
        except ValueError as e:
            return {"status": "fail", "message": str(e)}
    else:
        if str(detail.get(field) or "") != str(item["before"]["nilai"] or ""):
            return {"status": "konflik", "message": "nilai berubah sejak preview"}
        nested = None
    data = dict(detail)
    data[field] = nilai
    if field in MHS_REF_FIELDS:
        data[MHS_REF_FIELDS[field][1]] = nested
    if field.startswith("tgl_"):
        data[field] = nilai + "T00:00:00.000Z" if "T" not in nilai else nilai
    try:
        res = client.put("/mahasiswa/edit", payload=data)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        back = client.get("/mahasiswa/detail/" + str(hit.get("id_pd")))
    except FeederError as e:
        return _readback_failure(e)
    back = back if isinstance(back, dict) else {}
    if field in MHS_REF_FIELDS:
        ver = _mhs_ref_sama(back, field, nilai)
    elif field.startswith("tgl_"):
        ver = str(back.get(field) or "")[:10] == nilai[:10]
    else:
        ver = str(back.get(field) or "") == nilai
    if ver:
        return {"status": "ok", "message": f"{nim} {field} terverifikasi"}
    return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}


MHS_ADD_WAJIB = ["nipd", "nm_pd", "jk", "tmpt_lahir", "tgl_lahir", "nik", "nisn",
                 "email", "no_hp", "nm_ibu_kandung", "ds_kel", "id_wil", "prodi",
                 "periode", "tgl_masuk"]


def mhs_add_row(row):
    """Template mhs_add memakai kolom nipd; menu lain memakai nim."""
    out = dict(row)
    nipd = str(out.get("nipd") or out.get("nim") or "").strip()
    out["nipd"] = nipd
    out["nim"] = nipd
    return out


def _mhs_peta_nipd(client, cancel=None):
    """Peta NIM->baris, cache per klien (satu job web) seperti cache CLI."""
    peta = getattr(client, "_mhs_peta_nipd", None)
    if peta is None:
        peta = {str(r.get("nipd")): r
                for r in fetch_mhs_all(client, cancel=cancel)}
        client._mhs_peta_nipd = peta
    return peta


def _mhs_hist(client):
    """Referensi /history_pendidikan/get/select, cache per klien."""
    data = getattr(client, "_mhs_hist", None)
    if data is None:
        data = client.get("/history_pendidikan/get/select")
        client._mhs_hist = data if isinstance(data, dict) else {}
    return data


def _mhs_periode_terbuka(client):
    terbuka = getattr(client, "_mhs_periode", None)
    if terbuka is None:
        terbuka = {s.get("id_smt") for s in
                   client.get("/history_pendidikan/periode_aktif_all")}
        client._mhs_periode = terbuka
    return terbuka


def _mhs_add_validasi(client, row):
    """Aturan sama dengan mhs_add.py (validasi). Kembalikan (biodata, history, errs).

    Urutan error: wajib/NIK/jk -> prodi+periode (tanpa baca lain) -> NIM ada
    -> biaya+referensi; baris buruk tidak memicu permintaan jaringan ekstra.
    """
    errs = []
    for k in MHS_ADD_WAJIB:
        if blank(row.get(k)):
            errs.append(f"{k} kosong")
    nik = str(row.get("nik") or "").strip()
    if nik and not (nik.isdigit() and len(nik) == 16):
        errs.append("NIK harus 16 digit")
    jk = str(row.get("jk") or "").strip().upper()
    if jk and jk not in ("L", "P"):
        errs.append("jk harus L/P")
    if errs:
        return None, None, errs
    hist = _mhs_hist(client)
    prodi_raw = str(row.get("prodi") or "").strip()
    prodi_hit = [p for p in (hist.get("list_prodi") or [[]])[0]
                 if p.get("id_sms") == prodi_raw
                 or prodi_raw.lower() in (p.get("nm_lemb") or "").lower()]
    if len(prodi_hit) != 1:
        errs.append(f"prodi {prodi_raw!r} tak unik/tak ada")
    periode = str(row.get("periode") or "").strip()
    if periode and periode not in _mhs_periode_terbuka(client):
        errs.append(f"periode {periode!r} tak ada di periode_aktif_all")
    if errs:
        return None, None, errs
    if str(row.get("nipd") or "").strip() in _mhs_peta_nipd(client):
        errs.append(f"NIM {row['nipd']} sudah ada di Feeder")
    if errs:
        return None, None, errs
    biaya = str(row.get("biaya_masuk") or "0").strip()
    if not biaya.isdigit():
        return None, None, ["biaya_masuk harus angka bulat"]
    try:
        id_agama = str(row.get("id_agama") or "1").strip()
        agama = [x for x in _mhs_select_items(client, "agama")
                 if str(x.get("id_agama")) == id_agama]
        if len(agama) != 1:
            raise ValueError(f"agama {id_agama!r} tak ada")
        id_wil = str(row["id_wil"]).strip()
        wilayah = [x for x in _mhs_select_items(client, "wilayah")
                   if str(x.get("id_wil")).strip() == id_wil]
        if len(wilayah) != 1:
            raise ValueError(f"wilayah {id_wil!r} tak ada")
        id_pembiayaan = str(row.get("pembiayaan") or "1").strip()
        pembiayaan = [x for x in (hist.get("pembiayaan") or [[]])[0]
                      if str(x.get("id_pembiayaan")) == id_pembiayaan]
        if len(pembiayaan) != 1:
            raise ValueError(f"pembiayaan {id_pembiayaan!r} tak ada")
        id_jalur = str(row.get("jalur_masuk") or "").strip()
        if not id_jalur:
            raise ValueError("jalur_masuk wajib (lihat opsi /history_pendidikan/get/select)")
        jalur = [x for x in (hist.get("jalur_masuk") or [[]])[0]
                 if str(x.get("id_jalur_masuk")) == id_jalur]
        if len(jalur) != 1:
            raise ValueError(f"jalur_masuk {id_jalur!r} tak ada")
        id_jns = str(row.get("jenis_daftar") or "").strip()
        if not id_jns:
            raise ValueError("jenis_daftar wajib (lihat opsi /history_pendidikan/get/select)")
        jns = [x for x in (hist.get("jenis_daftar") or [[]])[0]
               if str(x.get("id_jns_daftar")) == id_jns]
        if len(jns) != 1:
            raise ValueError(f"jenis_daftar {id_jns!r} tak ada")
    except ValueError as e:
        return None, None, [str(e)]
    biodata = {
        "nm_pd": str(row["nm_pd"]).strip(),
        "jk": "Laki-laki" if jk == "L" else "Perempuan",
        "tmpt_lahir": str(row["tmpt_lahir"]).strip(),
        "tgl_lahir": str(row["tgl_lahir"]).strip() + "T00:00:00.000Z",
        "nik": nik, "nisn": str(row["nisn"]).strip(),
        "npwp": str(row.get("npwp") or "").strip() or None,
        "email": str(row["email"]).strip(), "no_hp": str(row["no_hp"]).strip(),
        "no_tel_rmh": str(row.get("no_tel_rmh") or "").strip() or None,
        "jln": str(row.get("jln") or "").strip() or None,
        "nm_dsn": str(row.get("nm_dsn") or "").strip() or None,
        "rt": str(row.get("rt") or "").strip() or None,
        "rw": str(row.get("rw") or "").strip() or None,
        "ds_kel": str(row["ds_kel"]).strip(),
        "kode_pos": str(row.get("kode_pos") or "").strip() or None,
        "nm_ibu_kandung": str(row["nm_ibu_kandung"]).strip(),
        "id_agama": agama[0]["id_agama"], "agama": agama[0],
        "id_wil": wilayah[0]["id_wil"], "wilayah": wilayah[0],
        "kewarganegaraan": {"id_negara": "ID", "nm_negara": "Indonesia"},
        "a_terima_kps": "Tidak", "kebutuhan_khusus_exist": "Tidak",
        "data_kebutuhan_khusus": [], "stat_pd": "A",
    }
    thn = periode[:4]
    nama_smt = f"{thn}/{int(thn) + 1} " + ("Ganjil" if periode[-1] == "1" else
                                            "Genap" if periode[-1] == "2" else "Pendek")
    history = {
        "nipd": str(row["nipd"]).strip(),
        "jenis_daftar": jns[0], "id_jns_daftar": jns[0]["id_jns_daftar"],
        "jalur_masuk": jalur[0], "id_jalur_masuk": jalur[0]["id_jalur_masuk"],
        "periode_pendaftaran": periode, "periode_pendaftaran_masking": nama_smt,
        "tgl_masuk": str(row["tgl_masuk"]).strip() + "T00:00:00.000Z",
        "pembiayaan": pembiayaan[0], "id_pembiayaan": pembiayaan[0]["id_pembiayaan"],
        "biaya_masuk_kuliah": int(biaya),
        "biaya_masuk_kuliah_masking": "IDR " + f"{int(biaya):,}".replace(",", "."),
        "prodi": prodi_hit[0], "id_sms": prodi_hit[0]["id_sms"],
        "sms": prodi_hit[0], "nm_sp": None,
    }
    return biodata, history, []


def preview_mhs_add(client, ctx, form, progress=None, cancel=None):
    rows = form.get("rows") or []
    items, errors, seen = [], [], set()
    for i, raw in enumerate(rows, 1):
        if cancel and cancel():
            raise Cancelled()
        row = mhs_add_row(raw)
        nim = row["nipd"]
        if nim in seen:
            errs = ["NIM duplikat dalam CSV"]
        else:
            seen.add(nim)
            errs = []
        if errs:
            history = None
        else:
            try:
                _, history, errs = _mhs_add_validasi(client, row)
            except FeederError as e:
                history, errs = None, ["baca Feeder gagal: " + e.message]
        item = {"id": i, "row": row,
                "target": {"nim": nim, "nama": str(row.get("nm_pd") or "").strip()},
                "after": {"prodi": (history or {}).get("prodi", {}).get("nm_lemb", "")
                                   if history else str(row.get("prodi") or ""),
                          "periode": str(row.get("periode") or ""),
                          "tgl_masuk": str(row.get("tgl_masuk") or ""),
                          "pembiayaan": (history or {}).get("pembiayaan", {}).get("nm_pembiayaan", "")
                                         if history else ""},
                "errors": errs}
        (errors if errs else items).append(item)
        if progress:
            progress(i, len(rows))
    return {"items": items, "errors": errors,
            "summary": {"siap": len(items), "error": len(errors)}}


def apply_mhs_add(client, ctx, item):
    if item.get("errors"):
        return {"status": "fail", "message": "; ".join(item["errors"])}
    row = item.get("row")
    if not isinstance(row, dict):
        return {"status": "fail", "message": "baris CSV tidak tersimpan di preview; buat preview baru"}
    try:
        biodata, history, errs = _mhs_add_validasi(client, row)
    except FeederError as e:
        return _write_failure(e)
    if errs:
        return {"status": "fail", "message": "; ".join(errs)}
    nim = str(row.get("nipd") or "").strip()
    try:
        res = client.post("/mahasiswa/add", payload=biodata)
    except FeederError as e:
        return _write_failure(e)
    if not isinstance(res, dict) or res.get("code") != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    id_pd = (res.get("data") or {}).get("id_pd")
    if not id_pd:
        return {"status": "unknown", "message": "biodata tersimpan tetapi id_pd tidak dikembalikan"}
    history["id_pd"] = id_pd
    try:
        history["detail"] = client.get("/mahasiswa/detail/" + str(id_pd))
    except FeederError as e:
        return {"status": "unknown",
                "message": f"biodata tersimpan (id_pd {id_pd}) tetapi detail belum terbaca: {e.message}"}
    try:
        res2 = client.post("/history_pendidikan/add", payload=history)
    except FeederError as e:
        return {"status": "unknown",
                "message": f"biodata tersimpan (id_pd {id_pd}) tetapi histori belum pasti: {e.message}"}
    if not isinstance(res2, dict) or res2.get("code") != 200:
        msg = res2.get("message") if isinstance(res2, dict) else "respons tidak dikenal"
        return {"status": "unknown",
                "message": f"biodata tersimpan (id_pd {id_pd}) tetapi histori ditolak: {msg}"}
    try:
        back = client.get("/history_pendidikan/index/" + str(id_pd))
    except FeederError as e:
        return _readback_failure(e)
    rows = (back.get("history_mahasiswa") or [[], 0])[0] if isinstance(back, dict) else []
    hit = [r for r in rows if str(r.get("nipd")) == nim]
    if len(hit) == 1:
        return {"status": "ok",
                "message": f"{nim} terverifikasi (id_reg_pd {hit[0].get('id_reg_pd')})"}
    return {"status": "unknown", "message": "tersimpan tetapi belum terbaca ulang"}


# ---------------------------------------------------------------------------
# audit + tulis: daya tampung (/dayatampung)

DAYATAMPUNG_ANGKA = {"target_mhs_baru", "calon_ikut_seleksi",
                     "calon_lulus_seleksi", "daftar_sbg_mhs",
                     "pst_undur_diri", "jml_mgu_kul"}
DAYATAMPUNG_TGL = {"tgl_awal_kul", "tgl_akhir_kul"}
DAYATAMPUNG_FIELDS = DAYATAMPUNG_ANGKA | DAYATAMPUNG_TGL


def _dayatampung_tgl(v):
    return str(v or "").split("T")[0]


def _dayatampung_sama(field, a, b):
    """Aturan sama dengan normal_banding() di dayatampung_edit.py."""
    if field in DAYATAMPUNG_ANGKA:
        try:
            return strict_dec(str(a).strip().replace(",", "")) == \
                strict_dec(str(b).strip().replace(",", ""))
        except ValueError:
            return False
    if field in DAYATAMPUNG_TGL:
        return _dayatampung_tgl(a) == _dayatampung_tgl(b)
    return (a or "").strip() == (b or "").strip()


def _dayatampung_valid(field, nilai):
    """Aturan sama dengan validasi_nilai() di dayatampung_edit.py."""
    if field in DAYATAMPUNG_ANGKA:
        s = str(nilai).strip().replace(",", "")
        if not s.isdigit():
            return f"{field} harus angka bulat, dapat {nilai!r}"
    if field in DAYATAMPUNG_TGL:
        try:
            date.fromisoformat(str(nilai).split("T")[0])
        except ValueError:
            return f"{field} harus tanggal YYYY-MM-DD valid, dapat {nilai!r}"
    if not str(nilai).strip():
        return f"{field} tidak boleh kosong"
    return None


def _dayatampung_ref(client):
    d = client.get("/dayatampung/filter")
    if not isinstance(d, dict):
        raise FeederError("response", "Filter daya tampung tidak terbaca")
    prodi = {r["nm_lemb"]: r for r in (d.get("list_prodi") or [[]])[0]
             if isinstance(r, dict)}
    sem = {s["id_smt"]: s["nm_smt"] for s in (d.get("list_semester") or [[]])[0]
           if isinstance(s, dict)}
    return prodi, sem


def _dayatampung_allow(client):
    try:
        d = client.get("/history_pendidikan/periode_aktif_all")
    except FeederError:
        return None
    if not isinstance(d, list):
        return None
    return {(x.get("id_smt"), x.get("id_sms")) for x in d if isinstance(x, dict)}


def _dayatampung_anomali(r, allow):
    """Aturan sama dengan daya_anomali() di dayatampung_check.py."""
    out = []
    if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
        out.append("belum sync")
    if (r.get("target_mhs_baru") or "0").strip() in ("", "0"):
        out.append("target 0")
    if allow is not None and (r.get("id_smt"), r.get("id_sms")) not in allow \
            and (r.get("id_smt"), None) not in allow:
        out.append("tidak bisa edit")
    return out


def _dayatampung_form(client, id_sms, id_smt):
    form = client.get(f"/dayatampung/edit?id_sms={id_sms}&id_smt={id_smt}")
    cur = form.get("result") if isinstance(form, dict) else None
    if not isinstance(cur, dict) or "target_mhs_baru" not in cur:
        raise FeederError("response", "Form daya tampung tak terbaca")
    return cur


def run_dayatampung(client, ctx, params, progress, cancel):
    sem = semester_of(ctx, params)
    rows = fetch_all(client, "/dayatampung/index", sem,
                     prodi_ids=prodi_ids_of(ctx, params),
                     column_search=["t.nm_smt", "nm_lemb"], sort_by="t.nm_smt",
                     cancel=cancel, progress=progress)
    allow = _dayatampung_allow(client)
    out = []
    for r in rows:
        out.append({
            "nm_smt": r.get("nm_smt", ""), "nm_lemb": r.get("nm_lemb", ""),
            "status_prodi": r.get("status_prodi", ""),
            "target_mhs_baru": r.get("target_mhs_baru", ""),
            "calon_ikut_seleksi": r.get("calon_ikut_seleksi", ""),
            "calon_lulus_seleksi": r.get("calon_lulus_seleksi", ""),
            "daftar_sbg_mhs": r.get("daftar_sbg_mhs", ""),
            "pst_undur_diri": r.get("pst_undur_diri", ""),
            "jml_mgu_kul": r.get("jml_mgu_kul", ""),
            "tgl_awal_kul": _dayatampung_tgl(r.get("tgl_awal_kul")),
            "tgl_akhir_kul": _dayatampung_tgl(r.get("tgl_akhir_kul")),
            "anomali": _dayatampung_anomali(r, allow),
            "id_sms": r.get("id_sms", ""), "id_smt": r.get("id_smt", ""),
        })
    summary = {"total": len(out),
               "belum_sync": sum("belum sync" in o["anomali"] for o in out),
               "target_0": sum("target 0" in o["anomali"] for o in out)}
    return {
        "rows": out, "summary": summary,
        "columns": [("nm_smt", "Semester"), ("nm_lemb", "Program studi"),
                    ("status_prodi", "Status prodi"),
                    ("target_mhs_baru", "Target MHS baru"),
                    ("calon_ikut_seleksi", "Calon ikut seleksi"),
                    ("calon_lulus_seleksi", "Calon lulus seleksi"),
                    ("daftar_sbg_mhs", "Daftar sbg MHS"),
                    ("pst_undur_diri", "Undur diri"),
                    ("jml_mgu_kul", "Jml minggu kuliah"),
                    ("tgl_awal_kul", "Awal perkuliahan"),
                    ("tgl_akhir_kul", "Akhir perkuliahan"),
                    ("anomali", "Anomali")],
    }


def preview_dayatampung_edit(client, ctx, form, progress=None, cancel=None):
    rows = form.get("rows") or []
    if not rows:
        raise FeederError("input", "Tidak ada baris untuk diproses")
    prodi_map, sem_map = _dayatampung_ref(client)
    items, errors, seen = [], [], set()
    for i, row in enumerate(rows, 1):
        if cancel and cancel():
            raise Cancelled()
        prodi = str(row.get("prodi") or "").strip()
        smt = str(row.get("semester") or "").strip()
        field = str(row.get("field") or "").strip()
        nilai = str(row.get("nilai") or "").strip()
        errs = []
        key = (prodi, smt, field)
        if key in seen:
            errs.append("target/field duplikat dalam CSV")
        else:
            seen.add(key)
        if prodi not in prodi_map:
            errs.append(f"prodi {prodi!r} tidak ada di Feeder")
        if smt not in sem_map:
            errs.append(f"semester {smt!r} tidak ada di Feeder")
        if field not in DAYATAMPUNG_FIELDS:
            errs.append(f"field {field!r} tidak diizinkan")
        else:
            err = _dayatampung_valid(field, nilai)
            if err:
                errs.append(err)
        cur = {}
        if not errs:
            if field in DAYATAMPUNG_TGL and nilai:
                nilai = nilai.split("T")[0]
            try:
                cur = _dayatampung_form(client, prodi_map[prodi]["id_sms"], smt)
            except FeederError as e:
                errs.append("form tidak dapat dibaca: " + e.message)
        item = {"id": i,
                "target": {"prodi": prodi, "semester": smt, "field": field,
                           "id_sms": prodi_map.get(prodi, {}).get("id_sms", "")
                           if prodi in prodi_map else ""},
                "before": {"nilai": cur.get(field, "") if cur else ""},
                "after": {"nilai": nilai},
                "errors": errs}
        (errors if errs else items).append(item)
        if progress:
            progress(i, len(rows))
    return {"items": items, "errors": errors,
            "summary": {"siap": len(items), "error": len(errors)}}


def apply_dayatampung_edit(client, ctx, item):
    if item.get("errors"):
        return {"status": "fail", "message": "; ".join(item["errors"])}
    t = item["target"]
    field, nilai = t["field"], item["after"]["nilai"]
    if field not in DAYATAMPUNG_FIELDS:
        return {"status": "fail", "message": "field tidak diizinkan"}
    try:
        form = _dayatampung_form(client, t["id_sms"], t["semester"])
    except FeederError as e:
        return {"status": "konflik", "message": "form tidak dapat dibaca: " + e.message}
    if not _dayatampung_sama(field, form.get(field), item["before"]["nilai"]):
        return {"status": "konflik", "message": "nilai berubah sejak preview"}
    if field in DAYATAMPUNG_TGL:
        nilai = str(nilai).split("T")[0]
    form[field] = nilai
    try:
        res = client.put(f"/dayatampung/edit?id_smt={t['semester']}&id_sms={t['id_sms']}",
                         payload=form)
    except FeederError as e:
        return _write_failure(e)
    code = res.get("code") if isinstance(res, dict) else None
    if code != 200:
        msg = res.get("message") if isinstance(res, dict) else "respons tidak dikenal"
        return {"status": "fail", "message": f"Feeder menolak: {msg}"}
    try:
        back = _dayatampung_form(client, t["id_sms"], t["semester"])
    except FeederError as e:
        return _readback_failure(e)
    if _dayatampung_sama(field, back.get(field), nilai):
        return {"status": "ok",
                "message": f"{t['prodi']} {t['semester']} {field} terverifikasi"}
    return {"status": "unknown", "message": "tersimpan tetapi verifikasi tidak cocok"}


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check()
    else:
        print(__doc__)
