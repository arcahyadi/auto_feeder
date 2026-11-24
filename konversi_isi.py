#!/usr/bin/env python3
"""Isi satu atau batch nilai konversi aktivitas mahasiswa via API (token).
Pola: POLA_SCRIPT.md (cek token -> TA dinamis).
Alur: /aktmhs/edit/:id -> anggota[] (cari NIM) -> id_sms ->
/matakuliah/search/cari_matakuliah (kode -> id_mk, sks) ->
/bobotnilai/index (band huruf/indeks) ->
POST /nilaiaktmhs/transfermatkul/add -> verifikasi baca-ulang.
Default dry-run; --go untuk menulis. Skip jika MK sudah punya nilai.
Pakai: python3 konversi_isi.py --nim 202500001 --mk SF23776 --nilai 80.17875 [--jenis Skripsi] [--go]
"""
import argparse, json, os, sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from feeder_client import FeederClient

BASE = os.environ.get("FEEDER_BASE", "http://localhost:8100/ws")

def _load_dotenv():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
_load_dotenv()
TOKEN = os.environ.get("FEEDER_TOKEN", "")
CLIENT = FeederClient(BASE, TOKEN) if TOKEN else None

def api(m, path, payload=None, params=None):
    if not CLIENT:
        raise ValueError("FEEDER_TOKEN kosong")
    return CLIENT.request(m, path, payload=payload, params=params)

def check_token():
    try:
        return "list_status" in json.dumps(api("GET", "/mhslulus/filter"))
    except Exception:
        return False

def active_ta():
    return api("GET", "/history_pendidikan/periode_aktif")[0]["id_smt"]

def fetch_aktivitas(id_smt, nm_smt):
    rows, page = [], 1
    while True:
        data, _ = api("GET", "/aktmhs/index", params={
            "pagination": {"sortBy": "st.nm_smt", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "",
            "columnSearch": ["m.judul_akt_mhs", "m.sk_tugas", "angg.nm_pd",
                             "angg.nipd", "uji.nm_sdm", "bimb.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}],
                          "prodi": [], "jenisaktivitas": [], "asaldata": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return rows

def normalisasi_nilai(nilai):
    try:
        value = Decimal(str(nilai))
    except (InvalidOperation, ValueError):
        raise ValueError("bukan angka")
    if not value.is_finite():
        raise ValueError("angka tidak terbatas")
    if value < 0 or value > 100:
        raise ValueError("angka harus 0-100")
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"

def cari_band(rows, id_sms, nilai):
    nilai = Decimal(str(nilai))
    matches = []
    for r in rows:
        if r["id_sms"] != id_sms:
            continue
        try:
            minimum = Decimal(str(r["bobot_nilai_min"]))
            maximum = Decimal(str(r["bobot_nilai_maks"]))
            indeks = f"{Decimal(str(r['nilai_indeks'])):.2f}"
        except (InvalidOperation, ValueError):
            raise ValueError("rentang bobot nilai tidak valid")
        if minimum <= nilai <= maximum:
            matches.append(((r.get("nilai_huruf") or "").strip(), indeks))
    if len(matches) > 1:
        raise ValueError("rentang bobot nilai tumpang tindih")
    return matches[0] if matches else (None, None)

def cocok(baca_ulang, nilai, huruf, indeks):
    """Feeder membulatkan nilai_angka ke 2 desimal dan memberi spasi di huruf."""
    try:
        return (normalisasi_nilai(baca_ulang.get("nilai_angka")) == normalisasi_nilai(nilai) and
                (baca_ulang.get("nilai_huruf") or "").strip() == huruf and
                f"{float(baca_ulang.get('nilai_indeks')):.2f}" == indeks)
    except (InvalidOperation, TypeError, ValueError):
        return False

def payload(anggota, mk, huruf, indeks, nilai, nm_smt):
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

def peta_anggota(aktivitas, jenis=None, workers=8):
    """Satu pass paralel: {nim: (aktivitas, anggota)}. 85 GET sekuensial lambat."""
    from concurrent.futures import ThreadPoolExecutor
    acts = [a for a in aktivitas
            if not jenis or jenis.lower() in (a.get("nm_jns_akt_mhs") or "").lower()]

    def ambil(a):
        return a, (api("GET", f"/aktmhs/edit/{a['id_akt_mhs']}").get("anggota") or [])

    peta = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for a, anggota in ex.map(ambil, acts):
            for m in anggota:
                nim = (m.get("nipd") or "").strip()
                if nim:
                    peta.setdefault(nim, []).append(
                        (a, dict(m, id_akt_mhs=a["id_akt_mhs"])))
    return peta

def _norm(s):
    return "".join((s or "").lower().split())

def nama_pendek(nm_mk):
    """'SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023' -> 'Skripsi I'."""
    s = nm_mk.split(" - ", 1)[-1]
    return s.split(" (", 1)[0].strip()

def sudah_ada(konv, mk):
    """Bandingkan nm_mk pendek + sks (record konversi tak menyimpan id_mk)."""
    mau = _norm(nama_pendek(mk["nm_mk"]))
    return any(_norm(k.get("nm_mk")) == mau and
               f"{float(k.get('sks_mk') or 0):.2f}" == f"{float(mk['sks_mk']):.2f}"
               for k in konv)

def cari_mk(kode):
    d = api("GET", "/matakuliah/search/cari_matakuliah", params={"nm_mk": kode})
    rows = (d.get("list_matakuliah") or [[]])[0]
    for r in rows:
        if r["nm_mk"].startswith(kode + " "):
            return r
    return None

def muat_bobot():
    return api("GET", "/bobotnilai/index", params={
        "pagination": {"sortBy": "s.id_sms,m.nilai_huruf", "descending": False,
                       "page": 1, "rowsPerPage": 200, "rowsNumber": 0},
        "filter": "", "columnSearch": ["s.nm_lemb"],
        "setFilter": {"prodi": []}, "extendFilter": {"filter_sync": "semua"},
        "sortList": None})[0]

def siapkan(kode_mk, jenis):
    ta = active_ta()
    sem = {s["id_smt"]: s for s in api("GET", "/kelas/filter")["list_semester"][0]}
    nm_smt = sem[ta]["nm_smt"]
    mk = cari_mk(kode_mk)
    if not mk:
        sys.exit(f"GAGAL: matakuliah {kode_mk} tak ketemu.")
    aktivitas = fetch_aktivitas(ta, nm_smt)
    if jenis:
        aktivitas = [a for a in aktivitas
                     if jenis.lower() in (a.get("nm_jns_akt_mhs") or "").lower()]
    return {"ta": ta, "nm_smt": nm_smt, "mk": mk,
            "peta": peta_anggota(aktivitas, jenis), "bobot": muat_bobot()}

def tulis_satu(nim, nama, nilai, ctx, go):
    """Kembalikan (status, pesan). status: ok/skip/gagal."""
    matches = ctx["peta"].get(nim, [])
    if not matches:
        return "gagal", "bukan anggota aktivitas mana pun"
    if len(matches) > 1:
        return "gagal", "NIM muncul di lebih dari satu aktivitas"
    a, m = matches[0]
    if nama and _norm(nama) != _norm(m.get("nm_pd")):
        return "gagal", "nama tidak cocok dengan NIM di Feeder"
    try:
        nilai = normalisasi_nilai(nilai)
    except ValueError:
        return "gagal", f"nilai {nilai!r} bukan angka"
    huruf, indeks = cari_band(ctx["bobot"], a["id_sms"], nilai)
    if not huruf:
        return "gagal", f"nilai {nilai} di luar semua band prodi"
    tpl = api("GET", f"/nilaiaktmhs/transfermatkuladd/{m['id_ang_akt_mhs']}")
    anggota = dict(tpl["anggota"], id_ang_akt_mhs=m["id_ang_akt_mhs"])
    nm_smt = tpl["filter_periode"]["nm_smt"]
    konv = api("GET", f"/nilaiaktmhs/transfermatkul/{m['id_ang_akt_mhs']}").get("konversi") or []
    if sudah_ada(konv, ctx["mk"]):
        return "skip", "sudah ada nilai MK ini"
    if not go:
        return "ok", f"DRY-RUN -> {huruf} ({indeks})"
    body = payload(anggota, ctx["mk"], huruf, indeks, nilai, nm_smt)
    res = api("POST", "/nilaiaktmhs/transfermatkul/add", body)
    if res.get("code") != 200:
        return "gagal", f"POST code {res.get('code')} {res.get('message')}"
    ulang = api("GET", f"/nilaiaktmhs/transfermatkul/{m['id_ang_akt_mhs']}").get("konversi") or []
    baru = [k for k in ulang if k.get("id_konversi_akt_mhs") not in
            {x.get("id_konversi_akt_mhs") for x in konv}]
    if not baru:
        return "gagal", "sukses tapi tak terbaca ulang"
    cocok_mk = [k for k in baru if sudah_ada([k], ctx["mk"])]
    if len(cocok_mk) != 1:
        return "periksa", "tersimpan tetapi target baca-ulang ambigu"
    b = cocok_mk[0]
    pesan = (f"{b.get('nm_mk')} {b.get('nilai_angka')} {b.get('nilai_huruf').strip()} "
             f"indeks {b.get('nilai_indeks')}")
    return ("ok" if cocok(b, nilai, huruf, indeks) else "periksa"), pesan

def run(nim, kode_mk, nilai, jenis=None, go=False):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ctx = siapkan(kode_mk, jenis)
    print(f"TA {ctx['ta']} ({ctx['nm_smt']}) | {ctx['mk']['nm_mk']} "
          f"sks {ctx['mk']['sks_mk']} | anggota aktivitas: {len(ctx['peta'])}")
    status, pesan = tulis_satu(nim, "", nilai, ctx, go)
    print(f"{nim}: {status} - {pesan}")
    if status == "gagal":
        sys.exit(1)

def run_batch(path, kode_mk, jenis=None, go=False):
    import csv as _csv
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        if not reader.fieldnames or not {"nim", "nilai"}.issubset(reader.fieldnames):
            sys.exit("GAGAL: CSV wajib punya kolom nim dan nilai")
        baris = list(reader)
    if not baris:
        sys.exit("GAGAL: CSV kosong")
    nims = [(r.get("nim") or "").strip() for r in baris]
    seen, duplikat = set(), set()
    for nim in nims:
        if nim in seen:
            duplikat.add(nim)
        elif nim:
            seen.add(nim)
    duplikat = sorted(duplikat)
    if duplikat:
        sys.exit("GAGAL: NIM duplikat dalam CSV: " + ", ".join(duplikat))
    ctx = siapkan(kode_mk, jenis)
    print(f"TA {ctx['ta']} ({ctx['nm_smt']}) | {ctx['mk']['nm_mk']} "
          f"sks {ctx['mk']['sks_mk']} | baris CSV: {len(baris)} | "
          f"anggota aktivitas: {len(ctx['peta'])}")
    print("mode:", "TULIS" if go else "dry-run")
    ringkas = {"ok": 0, "skip": 0, "gagal": 0, "periksa": 0}
    for i, r in enumerate(baris, 1):
        nim = (r.get("nim") or "").strip()
        nama = (r.get("nama") or "").strip()
        try:
            status, pesan = tulis_satu(nim, nama, r.get("nilai"), ctx, go)
        except Exception as e:
            status, pesan = "periksa", f"hasil belum pasti: {type(e).__name__}"
        ringkas[status] = ringkas.get(status, 0) + 1
        tanda = {"ok": "OK", "skip": "SKIP", "gagal": "GAGAL", "periksa": "PERIKSA"}[status]
        print(f"{i:2}/{len(baris)} {nim} {nama[:28]:28} {tanda:7} {pesan}")
    print(f"ringkasan: ok {ringkas['ok']} | skip {ringkas['skip']} | "
          f"gagal {ringkas['gagal']} | periksa {ringkas['periksa']}")
    return 1 if ringkas["gagal"] or ringkas["periksa"] else 0

def check():
    rows = [{"id_sms": "x", "nilai_huruf": "B+", "bobot_nilai_min": "75.0100",
             "bobot_nilai_maks": "80.0000", "nilai_indeks": "3.50"},
            {"id_sms": "x", "nilai_huruf": "A", "bobot_nilai_min": "80.0100",
             "bobot_nilai_maks": "100.0000", "nilai_indeks": "4.00"}]
    assert cari_band(rows, "x", 80.17875) == ("A", "4.00")
    assert cari_band(rows, "x", 75.01) == ("B+", "3.50")
    assert cari_band(rows, "y", 80) == (None, None)
    p = payload({"id_smt": "20252", "id_akt_mhs": "a", "id_ang_akt_mhs": "b",
                 "id_sms": "s"},
                {"id_mk": "m", "nm_mk": "SF1 - X", "sks_mk": "2.00"},
                "A", "4.00", "80.17875", "2025/2026 Genap")
    assert p["id_mk"] == "m" and p["nilai_huruf"] == "A" and p["sks_mk"] == "2.00"
    assert p["filter_nilai_huruf"]["nilai_indeks"] == "A#4,00"
    mk = {"nm_mk": "SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023", "sks_mk": "2.00"}
    assert nama_pendek(mk["nm_mk"]) == "Skripsi I"
    assert sudah_ada([{"nm_mk": "Skripsi I", "sks_mk": "2.00"}], mk)
    assert not sudah_ada([{"nm_mk": "SKRIPSI II", "sks_mk": "4.00"}], mk)
    assert not sudah_ada([{"nm_mk": "Skripsi I", "sks_mk": "4.00"}], mk)
    assert cocok({"nilai_angka": "80.18", "nilai_huruf": "A  ", "nilai_indeks": "4.00"},
                 "80.17875", "A", "4.00")
    assert cocok({"nilai_angka": "79.09", "nilai_huruf": "B+  ", "nilai_indeks": "3.50"},
                 "79.085", "B+", "3.50")
    assert not cocok({"nilai_angka": "79.06", "nilai_huruf": "B+", "nilai_indeks": "3.50"},
                     "80.17875", "A", "4.00")
    assert not cocok({"nilai_angka": "79.09", "nilai_huruf": "A", "nilai_indeks": "4.00"},
                     "79.085", "B+", "3.50")
    assert normalisasi_nilai("80.005") == "80.01"
    print("check ok")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--nim", help="satu NIM (pakai dengan --nilai)")
    ap.add_argument("--csv", help="batch: kolom nim,nama,nilai")
    ap.add_argument("--mk", required=True, help="kode matakuliah, mis. SF23776")
    ap.add_argument("--nilai", help="nilai angka, mis. 80.17875")
    ap.add_argument("--jenis", default=None, help="saring jenis aktivitas, mis. Skripsi")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    if a.csv:
        raise SystemExit(run_batch(a.csv, a.mk, jenis=a.jenis, go=a.go))
    elif a.nim and a.nilai:
        run(a.nim, a.mk, a.nilai, jenis=a.jenis, go=a.go)
    else:
        ap.error("pakai --csv ATAU --nim + --nilai")
