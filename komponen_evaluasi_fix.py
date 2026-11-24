#!/usr/bin/env python3
"""Perbaiki komponen evaluasi KELAS + isi nilai komponen per kode MK (TA aktif).

Berdasarkan KOMPONEN_EVALUASI.md (endpoint, aturan, resep, daftar kesalahan).
Default DRY-RUN; `--go` untuk menulis. Default hanya DB **sandbox**; DB
**pddikti** hanya bila `--pddikti` + `--confirm TULIS-PRODUKSI` (keduanya).
Script ini mengurus KELAS perkuliahan + nilai komponen; master MK diurus
`master_evaluasi_fix.py` (jalankan master dulu).

Kebijakan "stop-on-unknown": berhenti & lapor (status perlu-manual) bila
- DB tidak diizinkan gate,
- ada langkah yang tidak konvergen setelah beberapa percobaan,
- jumlah baris/total bobot tidak sesuai,
- nilai akhir mahasiswa berubah dari snapshot.

Alur per kelas:
  1. snapshot (komponen, nilai akhir, nilai komponen) + backup saat --go
  2. kelas KOSONG -> salin struktur master (JANGAN POST; POST menyemai 6
     template bawaan + 1 baris = 7 baris)
  3. kelas berisi -> lepas komponen asing, bersihkan nama jns2/jns3,
     tambah slot hilang, susun bobot = target (total 100)
  4. isi nilai komponen = bobot komponen
  5. verifikasi (tepat 6 baris, tiap sel = bobot, nilai akhir tidak berubah)

Pakai:
  python3 komponen_evaluasi_fix.py --mk SF24220            # dry-run
  python3 komponen_evaluasi_fix.py --mk SF24220 --go       # tulis (sandbox)
  python3 komponen_evaluasi_fix.py --mk SF24220 --kelas A --go
  python3 komponen_evaluasi_fix.py --prefix "SF23,SF24,DF24" --go
  # produksi:
  python3 komponen_evaluasi_fix.py --mk SF24220 --go --pddikti --confirm TULIS-PRODUKSI
"""
import argparse
import json
import os
import sys
import time

import komponen_evaluasi_check as chk
from feeder_client import FeederClient, FeederError, normalize_base

DEFAULT_BASE = "http://localhost:8100"

# Field nilai komponen (slot UI) -> bobot default (struktur master baku).
EXPECTED_FIELDS = ["nilai_aktivitas_partisipatif", "nilai_hasil_proyek",
                   "nilai_tugas", "nilai_quiz",
                   "nilai_ujian_tengah_semester", "nilai_ujian_akhir_semester"]
DEFAULT_TARGET = {"nilai_aktivitas_partisipatif": 15, "nilai_hasil_proyek": 35,
                  "nilai_tugas": 10, "nilai_quiz": 10,
                  "nilai_ujian_tengah_semester": 15,
                  "nilai_ujian_akhir_semester": 15}
# Target khusus SF20/DF20 (Aktivitas Partisipatif 30, Hasil Proyek 20, UTS/UAS 25).
TARGET_SF20 = {"nilai_aktivitas_partisipatif": 30, "nilai_hasil_proyek": 20,
               "nilai_tugas": 0, "nilai_quiz": 0,
               "nilai_ujian_tengah_semester": 25,
               "nilai_ujian_akhir_semester": 25}
# field -> (id_jns_eval, nama, nm_jns_eval) untuk menambah baris yang hilang
FIELD_ROW = {
    "nilai_aktivitas_partisipatif": (2, None, "Aktivitas Partisipatif"),
    "nilai_hasil_proyek": (3, None, "Hasil Proyek"),
    "nilai_tugas": (4, "TGS", "Kognitif/ Pengetahuan"),
    "nilai_quiz": (4, "QIZ", "Kognitif/ Pengetahuan"),
    "nilai_ujian_tengah_semester": (4, "UTS", "Kognitif/ Pengetahuan"),
    "nilai_ujian_akhir_semester": (4, "UAS", "Kognitif/ Pengetahuan"),
}
MAX_TRY = 6
MAX_HAPUS = 40  # cap aman untuk melepas banyak baris asing / bersih nama
PDDIKTI_CONFIRM = "TULIS-PRODUKSI"


def _load_dotenv():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
_load_dotenv()


def client_for_env():
    base = normalize_base(os.environ.get("FEEDER_BASE", "").strip() or DEFAULT_BASE)
    token = os.environ.get("FEEDER_TOKEN", "").strip()
    if not token:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    return FeederClient(base, token, timeout=40)


def cek_token(client):
    """True bila token masih valid (respons /mhslulus/filter punya list_status)."""
    try:
        return "list_status" in json.dumps(client.get("/mhslulus/filter"))
    except Exception:
        return False


def gate_db(database, pddikti=False, confirm=None):
    """Pesan error bila DB tak boleh dipakai, atau None bila lolos."""
    if database == "sandbox":
        return None
    if database == "pddikti":
        if not pddikti:
            return "STOP: DB pddikti tanpa flag --pddikti."
        if confirm != PDDIKTI_CONFIRM:
            return f"STOP: --confirm salah. Pakai: --confirm {PDDIKTI_CONFIRM}"
        return None
    return f"STOP: DB tak dikenal ({database!r})."


def client_gated(pddikti=False, confirm=None):
    client = client_for_env()
    if not cek_token(client):
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Perbarui .env (FEEDER_TOKEN).")
    db = client.get("/pengaturan/sandbox")
    database = db.get("database") if isinstance(db, dict) else "?"
    print(f"DB aktif: {database}")
    err = gate_db(database, pddikti, confirm)
    if err:
        sys.exit(err)
    if database == "pddikti":
        print("  !!! DB PRODUKSI (pddikti) - tulis disengaja !!!")
    return client


def active_ta(client):
    return client.get("/history_pendidikan/periode_aktif")[0]["id_smt"]


def semesters(client):
    return {s["id_smt"]: s for s in client.get("/kelas/filter")["list_semester"][0]}


def fetch_kelas(client, id_smt, nm_smt, kode):
    rows, page, total = [], 1, None
    while True:
        data, n = client.get("/kelas/index", params={
            "pagination": {"sortBy": "id_sms", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": kode,
            "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}],
                          "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        total = total or n
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return [r for r in rows if r.get("kode_mk", "").upper() == kode.upper()]


def semua_kelas(client, id_smt, nm_smt):
    """Semua kelas TA (tanpa filter kode), via paginasi."""
    rows, page = [], 1
    while True:
        data, n = client.get("/kelas/index", params={
            "pagination": {"sortBy": "id_sms", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "",
            "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}],
                          "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return rows


def mk_dari_prefix(rows, prefix):
    pre = tuple(p.strip().upper() for p in prefix if p.strip())
    kodes = sorted({(r.get("kode_mk") or "").upper() for r in rows})
    return [k for k in kodes if k.startswith(pre)]


def id_mk_kelas(client, id_kls):
    try:
        return client.get("/nilai/detail/" + id_kls)["data"]["detail"]["id_mk"]
    except (FeederError, KeyError, TypeError):
        return None


def master_mk(client, id_mk):
    try:
        m = client.get("/matakuliah/editrencanaeval/" + id_mk)
        return m if isinstance(m, list) else None
    except FeederError:
        return None


# ---------------------------------------------------------------------------
# baca (dengan guard: respons bukan list -> None, jangan crash)


def baca_komponen(client, id_kls):
    d = client.get("/kelas/editrencanaevaluasi/" + id_kls)
    return d if isinstance(d, list) else None


def baca_mhs(client, id_kls):
    d = client.get("/nilai/detail/" + id_kls)
    try:
        return d["data"]["mahasiswa"]
    except (KeyError, TypeError):
        return None


def baca_nilai_komponen(client, id_komp_eval, id_reg_pd):
    d = client.get(f"/kelas/nilaikomeval/{id_komp_eval}/{id_reg_pd}")
    return d[0].get("nilai_komp_eval") if isinstance(d, list) and d else ""


def snapshot(client, id_kls, mhs):
    komp = baca_komponen(client, id_kls) or []
    akhir = {m["nipd"]: (str(m.get("nilai_angka") or ""), m.get("nilai_huruf") or "")
             for m in mhs}
    nilai = {}
    for m in mhs:
        for a in komp:
            nilai[(m["nipd"], a["id_komp_eval"])] = baca_nilai_komponen(
                client, a["id_komp_eval"], m["id_reg_pd"])
    return {"komponen": komp, "nilai_akhir": akhir, "nilai_komponen": nilai}


def simpan_backup(kode, kelas, snap):
    os.makedirs("responses", exist_ok=True)
    fn = f"responses/rencana_eval_{kode}_{kelas}_sebelum.json"
    data = dict(snap)
    data["nilai_komponen"] = {f"{k[0]}|{k[1]}": v
                              for k, v in snap["nilai_komponen"].items()}
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return fn


def target_struktur(master):
    """{field: bobot} dari master MK; default bila master kosong/tak lengkap."""
    if master:
        t = {chk.maps_ui(a): float(a.get("bobot") or 0)
             for a in master if chk.maps_ui(a)}
        if set(t) == set(EXPECTED_FIELDS):
            return t, "master"
    return dict(DEFAULT_TARGET), "default"


# ---------------------------------------------------------------------------
# tulis (dengan verifikasi tiap langkah)


def put_bobot(client, id_kls, target):
    """Set bobot per slot; foreign -> 0. Ulangi sampai total 100 & slot pas."""
    for _ in range(MAX_TRY):
        rows = baca_komponen(client, id_kls) or []
        payload = []
        for a in rows:
            a = dict(a)
            f = chk.maps_ui(a)
            a["bobot"] = target[f] if f in target else 0
            payload.append(a)
        if abs(sum(p["bobot"] for p in payload) - 100) > 0.001:
            return False, "payload total != 100"
        try:
            client.put("/kelas/editrencanaevaluasi", payload=payload)
        except FeederError as e:
            return False, "PUT gagal: " + e.message
        back = baca_komponen(client, id_kls) or []
        got = {chk.maps_ui(a): float(a.get("bobot") or 0)
               for a in back if chk.maps_ui(a)}
        if set(got) == set(EXPECTED_FIELDS) and \
                abs(sum(got.values()) - 100) < 0.001 and \
                all(abs(got[f] - float(target[f])) < 0.001 for f in EXPECTED_FIELDS):
            return True, "ok"
    return False, "PUT tidak konvergen"


def naikkan_total_100(client, id_kls, target):
    """PUT agar total bobot = 100 (syarat POST add). Slot dikenali -> target;
    sisa (100 - jumlah slot) dialokasikan ke baris asing bila ada."""
    rows = baca_komponen(client, id_kls) or []
    known = sum(target[chk.maps_ui(a)] for a in rows if chk.maps_ui(a) in target)
    sisa = 100 - known
    if sisa < 0:
        return False, f"slot dikenali sudah melebihi 100 ({known})"
    foreign = [a for a in rows if chk.maps_ui(a) is None]
    if not foreign and abs(sisa) > 0.001:
        return True, "ok"  # tak ada baris asing; biar tahap lain menangani
    foreign_id = foreign[0]["id_komp_eval"] if foreign else None
    payload = []
    for a in rows:
        a = dict(a)
        f = chk.maps_ui(a)
        if f in target:
            a["bobot"] = target[f]
        elif f is None:
            a["bobot"] = sisa if a["id_komp_eval"] == foreign_id else 0
        payload.append(a)
    if abs(sum(p["bobot"] for p in payload) - 100) > 0.001:
        return True, "ok"  # tak bisa pas 100; lanjut (hapus asing akan gagal & dilaporkan)
    try:
        client.put("/kelas/editrencanaevaluasi", payload=payload)
    except FeederError as e:
        return False, "PUT naikkan total gagal: " + e.message
    return True, "ok"


def baris_ada_nilai(client, id_kls, a):
    """True bila ada minimal satu mahasiswa yang nilai komponen baris ini terisi."""
    mhs = baca_mhs(client, id_kls) or []
    for m in mhs:
        v = baca_nilai_komponen(client, a["id_komp_eval"], m["id_reg_pd"])
        if str(v or "").strip() not in ("", "0", "0.0000"):
            return True
    return False


def hapus_baris_id(client, id_kls, a):
    """Hapus SATU baris komponen via DELETE (params lengkap) + polling.

    Hanya bekerja untuk baris yang **nilainya kosong**; baris berisi nilai
    dibalas 402 ("... masih terisi"). Server **eventual-consistent**: balas 200
    tapi baris baru lepas beberapa saat kemudian -> polling bertahap
    (0.3, 0.6, 1.2, 2.0, 2.0 s) sampai baris `a` benar-benar hilang.
    """
    target = a["id_komp_eval"]
    jeda = [0.3, 0.6, 1.2, 2.0, 2.0, 2.0]
    for t in jeda:
        try:
            client.delete("/kelas/deletekomponenevaluasi", params={
                "id_kls": id_kls, "id_jns_eval": a.get("id_jns_eval"),
                "nama": a.get("nama") or "", "id_komp_eval": target})
        except FeederError as e:
            return False, "DELETE gagal: " + e.message
        time.sleep(t)
        ids = {x["id_komp_eval"] for x in (baca_komponen(client, id_kls) or [])}
        if target not in ids:
            return True, "ok"
    return False, "DELETE tidak melepas baris"


def _kembar_dan_hapus(client, id_kls, a):
    """Fallback: tambah baris kembar (by nama) lalu DELETE by-nama."""
    nama = chk.norm_nama(a.get("nama"))
    if not nama:
        return False, "baris tanpa nama tak bisa lewat trik kembar"
    new = {"urutan": 99, "id_jns_eval": a.get("id_jns_eval"), "id_kls": id_kls,
           "nama": nama, "nama_inggris": "", "bobot": 100,
           "nm_jns_eval": a.get("nm_jns_eval") or "", "deskripsi": "-",
           "deskripsi_en": "", "tambah": "oke"}
    original_ids = {x["id_komp_eval"] for x in (baca_komponen(client, id_kls) or [])}
    before = len(original_ids)
    try:
        client.post("/kelas/addrencanaevaluasi", payload=[new])
    except FeederError as e:
        return False, "POST kembar gagal: " + e.message
    rows = baca_komponen(client, id_kls) or []
    kembar = [x for x in rows if x["id_komp_eval"] not in original_ids]
    if not kembar or len(rows) <= before:
        return False, "baris kembar belum terbentuk"
    for _ in range(MAX_TRY):
        n_before = len(baca_komponen(client, id_kls) or [])
        try:
            client.delete("/kelas/deletekomponenevaluasi", params={
                "id_kls": kembar[0]["id_kls"], "id_jns_eval": kembar[0]["id_jns_eval"],
                "nama": nama, "id_komp_eval": kembar[0]["id_komp_eval"]})
        except FeederError as e:
            return False, "DELETE gagal: " + e.message
        time.sleep(0.6)
        if len(baca_komponen(client, id_kls) or []) < n_before:
            return True, "ok"
    return False, "DELETE tidak melepas baris kembar"


def hapus_baris(client, id_kls, a):
    """Hapus satu baris dengan metode paling murah sesuai kondisinya.

    - baris **berisi nilai** -> langsung trik kembar (by-id pasti 402).
    - baris **kosong & bernama** -> by-id dulu, fallback trik kembar.
    - baris **kosong & tanpa nama** (jns2/jns3) -> hanya by-id.
    """
    if baris_ada_nilai(client, id_kls, a):
        if chk.norm_nama(a.get("nama")):
            return _kembar_dan_hapus(client, id_kls, a)
        return False, "baris berisi nilai & tanpa nama: tak bisa dihapus via API"
    ok, msg = hapus_baris_id(client, id_kls, a)
    if ok:
        return True, "ok"
    if chk.norm_nama(a.get("nama")):
        return _kembar_dan_hapus(client, id_kls, a)
    return False, msg


def hapus_asing(client, id_kls):
    """Lepas komponen asing (maps_ui None), sampai habis."""
    for _ in range(MAX_HAPUS):
        rows = baca_komponen(client, id_kls) or []
        asing = [a for a in rows if chk.maps_ui(a) is None]
        if not asing:
            return True, "ok"
        n_asing = len(asing)
        ok, msg = hapus_baris(client, id_kls, asing[0])
        if not ok:
            return False, msg
        sisa = [x for x in (baca_komponen(client, id_kls) or [])
                if chk.maps_ui(x) is None]
        if len(sisa) < n_asing:
            continue
        return False, "komponen asing tidak terlepas"
    return False, f"lepas asing tak selesai setelah {MAX_HAPUS} iterasi"


def bersihkan_nama_salah(client, id_kls):
    """Hapus baris jns2/jns3 yang `nama`-nya terisi (seharusnya kosong),
    lalu slot itu ditambahkan ulang bersih oleh tambah_hilang.

    Prioritas: baris yang **nilainya kosong** dulu (bisa by-id, cepat); baru
    baris berisi nilai (butuh trik kembar)."""
    for _ in range(MAX_HAPUS):
        rows = baca_komponen(client, id_kls) or []
        salah = [a for a in rows
                 if a.get("id_jns_eval") in (2, 3) and chk.norm_nama(a.get("nama"))]
        if not salah:
            return True, "ok"
        kosong = [a for a in salah if not baris_ada_nilai(client, id_kls, a)]
        a = kosong[0] if kosong else salah[0]
        ok, msg = hapus_baris(client, id_kls, a)
        if not ok:
            return False, msg
    return False, f"bersih nama tak selesai setelah {MAX_HAPUS} iterasi"


def _pilih_sisa(lst):
    """Dari beberapa baris slot yang sama, pilih satu yang paling 'benar':
    jns2/jns3 -> nama kosong; jns4 -> nama sesuai; sisanya yang pertama."""
    def skor(a):
        jns = a.get("id_jns_eval"); nama = chk.norm_nama(a.get("nama"))
        if jns in (2, 3):
            return 0 if nama is None else 1   # kosong lebih benar
        f = chk.maps_ui(a)
        if f and f in FIELD_ROW:
            return 0 if nama == FIELD_ROW[f][1] else 1
        return 2
    return sorted(lst, key=skor)[0]


def slot_ganda(rows):
    """Baris slot yang berlebih (sisakan satu 'paling benar' per slot UI)."""
    per_slot = {}
    for a in rows:
        f = chk.maps_ui(a)
        if f is None:
            continue
        per_slot.setdefault(f, []).append(a)
    out = []
    for lst in per_slot.values():
        if len(lst) > 1:
            sisa = _pilih_sisa(lst)
            out += [a for a in lst if a is not sisa]
    return out


def hapus_slot_ganda(client, id_kls):
    """Buang duplikat slot UI: sisakan SATU baris per slot.

    Berguna usai "salin master" dari master duplikat (jns2/jns3 berulang).
    Tiap slot berlebih dihapus via `hapus_baris` (by-id; baris ganda jns2/jns3
    nama kosong terbukti bisa terlepas lewat by-id).
    """
    for _ in range(MAX_HAPUS):
        rows = baca_komponen(client, id_kls) or []
        ganda = slot_ganda(rows)
        if not ganda:
            return True, "ok"
        before = len(rows)
        ok, msg = hapus_baris(client, id_kls, ganda[0])
        if not ok:
            return False, msg
        if len(baca_komponen(client, id_kls) or []) >= before:
            return False, "slot ganda tidak terlepas"
    return False, f"buang slot ganda tak selesai setelah {MAX_HAPUS} iterasi"


def tambah_hilang(client, id_kls, target):
    """Tambah slot yang belum ada (POST hanya baris baru). TIDAK untuk kelas
    kosong (kelas kosong disalin dari master)."""
    for _ in range(MAX_TRY):
        rows = baca_komponen(client, id_kls) or []
        ada = {chk.maps_ui(a) for a in rows if chk.maps_ui(a)}
        kurang = [f for f in EXPECTED_FIELDS if f not in ada]
        if not kurang:
            return True, "ok"
        f = kurang[0]
        jns, nama, nm_jns = FIELD_ROW[f]
        new = {"urutan": 99, "id_jns_eval": jns, "id_kls": id_kls, "nama": nama,
               "nama_inggris": "", "bobot": 100,
               "nm_jns_eval": nm_jns, "deskripsi": "-", "deskripsi_en": "",
               "tambah": "oke"}
        before = len(baca_komponen(client, id_kls) or [])
        try:
            client.post("/kelas/addrencanaevaluasi", payload=[new])
        except FeederError as e:
            return False, "POST tambah gagal: " + e.message
        if len(baca_komponen(client, id_kls) or []) <= before:
            continue
    return False, "tambah slot tidak konvergen"


def salin_master(client, id_kls, id_mk):
    """Salin struktur master ke kelas (hanya bila kelas kosong). Hasil = 6 baris."""
    try:
        client.get(f"/kelas/salinrencanaevaluasi/{id_kls}/{id_mk}")
    except FeederError as e:
        return False, "salin master gagal: " + e.message
    time.sleep(0.5)
    return True, "ok"


def isi_nilai(client, id_kls, komp, target):
    """Isi nilai tiap komponen = bobotnya (POST /nilai/update)."""
    mhs = baca_mhs(client, id_kls) or []
    arr = []
    for m in mhs:
        row = dict(m)
        row["canUpdateevaluasi"] = True
        for a in komp:
            f = chk.maps_ui(a)
            if not f:
                continue
            row["id_komp_eval_" + f] = a["id_komp_eval"]
            row[f] = str(int(float(target[f])))
        arr.append(row)
    try:
        res = client.post("/nilai/update", payload=arr)
    except FeederError as e:
        return False, "POST nilai gagal: " + e.message
    if not isinstance(res, dict) or res.get("code") != 200:
        return False, "Feeder menolak nilai: " + str(res)
    return True, "ok"


# ---------------------------------------------------------------------------
# orkestrasi


def _beda_nilai_akhir(snap, akhir2, tol=0.01):
    """NIPD yang nilai akhirnya berubah (di luar toleransi pembulatan server),
    ATAU daftar mahasiswa berubah (ada hilang/baru).

    Toleransi 0.01: kelas yang awalnya bobot-0 lalu diisi 15/35/10/10/15/15
    bisa bergeser <= 0.01 akibat pembulatan server (mis. 90.0050 -> 90.0000).
    """
    lama = snap["nilai_akhir"]
    if set(lama) != set(akhir2):
        return ["(daftar mhs berubah)"]
    return [n for n in lama
            if abs(float(lama[n][0] or 0) - float(akhir2[n][0] or 0)) > tol
            or lama[n][1] != akhir2[n][1]]


def fix_kelas(client, kode, r, id_mk, target, go, log):
    id_kls = r["id_kls"]
    mhs = baca_mhs(client, id_kls)
    if mhs is None:
        return _gagal("baca mhs", "respons bukan list")
    snap = snapshot(client, id_kls, mhs)
    komp = snap["komponen"]
    anomali = chk.komponen_anomali(komp)
    cur = {chk.maps_ui(a): float(a.get("bobot") or 0) for a in komp if chk.maps_ui(a)}
    if cur != target:
        beda = [f"{chk.LABEL.get(f, f)}={cur.get(f)}->{target[f]}"
                for f in EXPECTED_FIELDS if cur.get(f) != target[f]]
        anomali.append("bobot beda dari target: " + ", ".join(beda))
    print(f"\n  {kode}-{r.get('nm_kls')} (id {id_kls[:8]}) | mhs {len(mhs)}")
    for x in anomali:
        print(f"     ! {x}")
    if not anomali:
        print("     sudah benar, dilewati")
        return "ok"
    if not go:
        print(f"     [dry-run] akan: struktur = {target} -> isi nilai = bobot")
        return "dry-run"

    fn = simpan_backup(kode, r.get("nm_kls") or "?", snap)
    print(f"     backup -> {fn}")

    # 2) kelas KOSONG -> salin master (JANGAN POST; POST menyemai 6 template)
    if len(komp) == 0:
        if not id_mk:
            return _gagal("salin master", "id_mk tak diketahui")
        ok, msg = salin_master(client, id_kls, id_mk)
        if not ok:
            return _gagal("salin master", msg)
        komp = baca_komponen(client, id_kls) or []
        # salin dari master duplikat bisa >6 baris / slot ganda -> rapikan di bawah
    # 3) urutan: buang slot ganda bisa gagal bila ada baris jns2/jns3 bernama
    #    (dianggap slot yang sama). Karena itu: lepas asing -> bersih nama
    #    jns2/jns3 bernama -> buang slot ganda -> naikkan total -> tambah hilang.
    ok, msg = hapus_asing(client, id_kls)
    if not ok:
        return _gagal("lepas asing", msg)
    ok, msg = bersihkan_nama_salah(client, id_kls)
    if not ok:
        return _gagal("bersih nama jns2/jns3", msg)
    ok, msg = hapus_slot_ganda(client, id_kls)
    if not ok:
        return _gagal("buang slot ganda", msg)
    if len(komp) > 0:
        ok, msg = naikkan_total_100(client, id_kls, target)
        if not ok:
            return _gagal("naikkan total 100", msg)
    ok, msg = tambah_hilang(client, id_kls, target)
    if not ok:
        return _gagal("tambah slot", msg)

    ok, msg = put_bobot(client, id_kls, target)
    if not ok:
        return _gagal("susun bobot", msg)
    komp = baca_komponen(client, id_kls) or []
    if len(komp) != 6:
        return _gagal("struktur", f"jumlah baris {len(komp)} != 6")

    # 4) isi nilai komponen
    ok, msg = isi_nilai(client, id_kls, komp, target)
    if not ok:
        return _gagal("isi nilai", msg)

    # 5) verifikasi
    mhs2 = baca_mhs(client, id_kls) or []
    akhir2 = {m["nipd"]: (str(m.get("nilai_angka") or ""), m.get("nilai_huruf") or "")
              for m in mhs2}
    beda = _beda_nilai_akhir(snap, akhir2)
    if beda:
        return _gagal("nilai akhir berubah", ", ".join(beda[:5]))
    for m in mhs2:
        for a in komp:
            f = chk.maps_ui(a)
            if not f:
                continue
            got = baca_nilai_komponen(client, a["id_komp_eval"], m["id_reg_pd"])
            if abs(float(got or 0) - target[f]) > 0.001:
                return _gagal("verifikasi nilai komponen", f"{m['nipd']} {f}={got!r}")
    print(f"     OK: struktur = target, nilai komponen = bobot, "
          f"nilai akhir {len(mhs2)} mhs tidak berubah")
    return "ok"


def _gagal(tahap, msg):
    print(f"     PERLU-MANUAL ({tahap}): {msg}")
    return "perlu-manual"


def _tally(status):
    return {"ok": 1 if status == "ok" else 0,
            "dry-run": 1 if status == "dry-run" else 0,
            "perlu-manual": 1 if status == "perlu-manual" else 0}


def run_mk(client, kode, ta, nm_smt, kelas=None, go=False, umum=False, target_fix=None):
    rows = fetch_kelas(client, ta, nm_smt, kode)
    if not rows:
        print(f"\n=== {kode}: tak ada kelas di TA aktif -> dilewati")
        return _tally("skip")
    if kelas:
        rows = [r for r in rows if (r.get("nm_kls") or "").upper() == kelas.upper()]
        if not rows:
            print(f"\n=== {kode}: kelas {kelas} tak ditemukan")
            return _tally("skip")
    id_mk = id_mk_kelas(client, rows[0]["id_kls"])
    master = master_mk(client, id_mk) if id_mk else None
    if target_fix:
        target, sumber = dict(target_fix), f"target tetap {list(target_fix.values())}"
    elif umum:
        target, sumber = dict(DEFAULT_TARGET), "umum (paksa 15/35/10/10/15/15)"
    else:
        target, sumber = target_struktur(master)
    print(f"\n=== {kode} | kelas {len(rows)} | target dari {sumber}: {target}")
    ringkas = {"ok": 0, "dry-run": 0, "perlu-manual": 0}
    for r in rows:
        st = fix_kelas(client, kode, r, id_mk, target, go, print)
        ringkas[st] = ringkas.get(st, 0) + 1
        if st == "perlu-manual":
            return ringkas  # stop MK ini
    return ringkas


def run(kode, kelas=None, go=False, pddikti=False, confirm=None, umum=False, target_fix=None):
    client = client_gated(pddikti, confirm)
    ta = active_ta(client)
    nm = semesters(client)[ta]["nm_smt"]
    ringkas = run_mk(client, kode, ta, nm, kelas, go, umum, target_fix)
    print(f"\nringkasan {kode}: ok {ringkas['ok']} | dry-run {ringkas['dry-run']} | "
          f"perlu-manual {ringkas['perlu-manual']}")
    return 1 if ringkas["perlu-manual"] else 0


def run_prefix(prefix, go=False, pddikti=False, confirm=None, umum=False, target_fix=None):
    client = client_gated(pddikti, confirm)
    ta = active_ta(client)
    nm = semesters(client)[ta]["nm_smt"]
    kodes = mk_dari_prefix(semua_kelas(client, ta, nm), prefix)
    print(f"prefix {prefix} -> {len(kodes)} MK: {', '.join(kodes)}")
    total = {"ok": 0, "dry-run": 0, "perlu-manual": 0}
    for k in kodes:
        r = run_mk(client, k, ta, nm, None, go, umum, target_fix)
        for key in total:
            total[key] += r.get(key, 0)
        if r.get("perlu-manual"):
            print(f"\nSTOP BATCH: {k} perlu-manual.")
            break
    print(f"\nringkasan batch: ok {total['ok']} | dry-run {total['dry-run']} | "
          f"perlu-manual {total['perlu-manual']}")
    return 1 if total["perlu-manual"] else 0


def check():
    """Self-check offline: target dari master + pemetaan field + gate + prefix."""
    master = [{"id_jns_eval": 2, "nama": None, "bobot": 15},
              {"id_jns_eval": 3, "nama": "", "bobot": 35},
              {"id_jns_eval": 4, "nama": "TGS", "bobot": 10},
              {"id_jns_eval": 4, "nama": "QIZ", "bobot": 10},
              {"id_jns_eval": 4, "nama": "UTS", "bobot": 15},
              {"id_jns_eval": 4, "nama": "UAS", "bobot": 15}]
    t, s = target_struktur(master)
    assert s == "master" and t == DEFAULT_TARGET, (t, s)
    t2, s2 = target_struktur([])
    assert s2 == "default" and t2 == DEFAULT_TARGET
    t3, s3 = target_struktur([{"id_jns_eval": 2, "nama": "", "bobot": 100}])
    assert s3 == "default" and t3 == DEFAULT_TARGET
    assert FIELD_ROW["nilai_aktivitas_partisipatif"][0] == 2
    assert FIELD_ROW["nilai_tugas"][1] == "TGS"
    assert sum(DEFAULT_TARGET.values()) == 100
    assert sum(TARGET_SF20.values()) == 100
    assert TARGET_SF20["nilai_aktivitas_partisipatif"] == 30
    assert TARGET_SF20["nilai_ujian_akhir_semester"] == 25
    # gate DB
    assert gate_db("sandbox") is None
    assert gate_db("sandbox", pddikti=True) is None
    assert gate_db("pddikti") is not None
    assert gate_db("pddikti", pddikti=True) is not None
    assert gate_db("pddikti", pddikti=True, confirm="salah") is not None
    assert gate_db("pddikti", pddikti=True, confirm=PDDIKTI_CONFIRM) is None
    assert gate_db("lain") is not None
    # prefix
    rows = [{"kode_mk": "SF201202"}, {"kode_mk": "SF23667"}, {"kode_mk": "SF24220"},
            {"kode_mk": "DF244219"}, {"kode_mk": "PA25101"}]
    assert mk_dari_prefix(rows, ["SF23", "SF24", "DF24"]) == ["DF244219", "SF23667", "SF24220"]
    # nilai akhir: deteksi daftar mhs berubah + toleransi 0.01
    snap = {"nilai_akhir": {"1": ("80", "A")}}
    assert _beda_nilai_akhir(snap, {"1": ("80", "A")}) == []
    assert _beda_nilai_akhir(snap, {"1": ("80.0050", "A")}) == []      # <= 0.01
    assert _beda_nilai_akhir(snap, {"1": ("80.02", "A")}) != []        # > 0.01
    assert _beda_nilai_akhir(snap, {"1": ("80", "B")}) != []           # huruf beda
    assert _beda_nilai_akhir(snap, {"1": ("80.0", "A"), "2": ("70", "B")}) != []
    # deteksi slot ganda (murni, offline)
    dup_rows = [{"id_jns_eval": 2, "nama": "", "bobot": 15},
                {"id_jns_eval": 2, "nama": "", "bobot": 15},
                {"id_jns_eval": 3, "nama": "", "bobot": 35},
                {"id_jns_eval": 4, "nama": "TGS", "bobot": 10},
                {"id_jns_eval": 4, "nama": "TGS", "bobot": 10},
                {"id_jns_eval": 4, "nama": "UAS", "bobot": 15}]
    g = slot_ganda(dup_rows)
    assert len(g) == 2, g  # 1 jns2 ganda + 1 TGS ganda
    assert slot_ganda(master) == []
    print("check ok: target struktur + pemetaan field + gate + prefix")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--mk", default=None, help="kode matakuliah, mis. SF24220")
    ap.add_argument("--prefix", default=None,
                    help="daftar prefix MK, mis. 'SF23,SF24,DF24' (batch)")
    ap.add_argument("--kelas", default=None, help="batasi satu kelas, mis. A")
    ap.add_argument("--go", action="store_true", help="tulis (default dry-run)")
    ap.add_argument("--pddikti", action="store_true",
                    help="izinkan DB produksi (wajib + --confirm)")
    ap.add_argument("--confirm", default=None,
                    help=f"konfirmasi produksi: {PDDIKTI_CONFIRM}")
    ap.add_argument("--umum", action="store_true",
                    help="paksa target 15/35/10/10/15/15 (abaikan master)")
    ap.add_argument("--sf20", action="store_true",
                    help="paksa target SF20/DF20: 30/20/0/0/25/25 (abaikan master)")
    a = ap.parse_args()
    tf = dict(TARGET_SF20) if a.sf20 else None
    if a.prefix:
        prefix = [p for p in a.prefix.split(",") if p.strip()]
        raise SystemExit(run_prefix(prefix, a.go, a.pddikti, a.confirm, a.umum, tf))
    if not a.mk:
        ap.error("butuh --mk ATAU --prefix")
    raise SystemExit(run(a.mk, a.kelas, a.go, a.pddikti, a.confirm, a.umum, tf))
