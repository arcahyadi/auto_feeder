#!/usr/bin/env python3
"""Cek daftar mahasiswa 100% via API (token). Pola: POLA_SCRIPT.md.
Alur: cek token -> GET /ws/mahasiswa/index (axios0, paginasi 100) -> CSV.
--audit: cek kelengkapan biodata per mahasiswa via /mahasiswa/detail/:id_pd
(paralel 8) -> kolom anomali. ref: lookup ID referensi (wilayah/agama/dll).
Pakai:
  python3 mhs_check.py
  python3 mhs_check.py --prodi D3 --status Aktif
  python3 mhs_check.py --cari BUDI
  python3 mhs_check.py --audit --limit 50
  python3 mhs_check.py ref wilayah bandung
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

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

def axios0(params):
    q = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            q.append((k, str(v).lower()))
        elif isinstance(v, list):
            for it in v:
                q.append((k + "[]", json.dumps(it) if isinstance(it, dict) else it))
        elif isinstance(v, dict):
            q.append((k, json.dumps(v)))
        else:
            q.append((k, v))
    return q

def api_get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(axios0(params))
    req = urllib.request.Request(url, headers={"authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")

def check_token():
    try:
        return "list_status" in json.dumps(api_get("/mhslulus/filter"))
    except Exception:
        return False

def fetch_mhs(cari="", ulang=5):
    """Semua mahasiswa (paginasi 100). Server kadang balas {} -> ulangi."""
    rows, page, total = [], 1, None
    while True:
        r = None
        for i in range(ulang):
            r = api_get("/mahasiswa/index", {
                "pagination": {"sortBy": "P.nm_pd", "descending": False, "page": page,
                               "rowsPerPage": 100, "rowsNumber": 0},
                "filter": cari,
                "columnSearch": ["P.nm_pd", "r.nipd", "P.jk", "nm_agama",
                                 "s.nm_lemb", "r.mulai_smt"],
                "setFilter": {"prodi": [], "angkatan": [], "status_aktif_mahasiswa": [],
                              "jenis_kelamin": [], "agama": [], "jenis_pendaftaran": []},
                "extendFilter": {}, "sortList": None})
            if isinstance(r, list) and len(r) == 2 and isinstance(r[0], list):
                break
            time.sleep(1 + i)
        else:
            raise RuntimeError(f"/mahasiswa/index hal {page} balas {str(r)[:120]}")
        batch, total = r
        rows += batch
        if len(batch) < 100:
            return rows, int(total)
        page += 1

COLS = ["nipd", "nm_pd", "jk", "nm_agama", "tgl_lahir", "nm_lemb",
        "nm_stat_mhs", "mulai_smt", "anomali", "id_pd", "id_reg_pd"]

AUDIT_WAJIB = [
    ("nik", "NIK"), ("nisn", "NISN"), ("email", "email"), ("no_hp", "HP"),
    ("ds_kel", "kelurahan"), ("id_wil", "kecamatan"), ("nm_ibu_kandung", "nama ibu"),
    ("tmpt_lahir", "tempat lahir"), ("tgl_lahir", "tanggal lahir"),
    ("id_agama", "agama"), ("kewarganegaraan", "kewarganegaraan"),
    ("jk", "jenis kelamin"),
]

def audit_anomali(d):
    """Anomali biodata menurut validasi form mahasiswa Feeder."""
    out = []
    for key, label in AUDIT_WAJIB:
        if not str(d.get(key) or "").strip():
            out.append(f"{label} kosong")
    nik = str(d.get("nik") or "").strip()
    if nik and not (nik.isdigit() and len(nik) == 16):
        out.append("NIK bukan 16 digit")
    return out

def _detail_safe(id_pd):
    if not id_pd:
        return None
    try:
        return api_get("/mahasiswa/detail/" + str(id_pd))
    except Exception:
        return None

def run(prodi="", status="", cari="", audit=False, limit=None):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    rows, total = fetch_mhs(cari)
    print(f"mahasiswa: {len(rows)} (server: {total})")
    if prodi:
        rows = [r for r in rows if prodi.lower() in (r.get("nm_lemb") or "").lower()]
    if status:
        rows = [r for r in rows if status.lower() in (r.get("nm_stat_mhs") or "").lower()]
    if limit:
        rows = rows[:limit]
    if prodi or status or limit:
        print(f"setelah filter: {len(rows)}")
    if audit:
        print("audit biodata...", end=" ", flush=True)
        with ThreadPoolExecutor(max_workers=8) as ex:
            details = list(ex.map(lambda r: _detail_safe(r.get("id_pd")), rows))
        print("selesai")
        for r, d in zip(rows, details):
            r["anomali"] = "; ".join(audit_anomali(d)) if d else "detail gagal dibaca"
    out = "mhs_semua.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["tgl_lahir"] = str(r.get("tgl_lahir") or "")[:10]
            r["mulai_smt"] = str(r.get("mulai_smt") or "")[:4]
            r.setdefault("anomali", "")
            w.writerow({k: r.get(k, "") for k in COLS})
    print(f"tersimpan: {out}")
    st = {}
    for r in rows:
        st[r["nm_stat_mhs"]] = st.get(r["nm_stat_mhs"], 0) + 1
    print("status:", st)
    if audit:
        n = sum(1 for r in rows if r.get("anomali"))
        print(f"ada anomali biodata: {n} dari {len(rows)}")

REF_LIST = {
    "agama": ("agama", "nm_agama"),
    "wilayah": ("wilayah", "kecamatan"),
    "pekerjaan": ("pekerjaan", "nm_pekerjaan"),
    "penghasilan": ("penghasilan", "nm_penghasilan"),
    "transportasi": ("alat_transportasi", "nm_alat_transport"),
    "jenjang": ("jenjang_pendidikan", "nm_jenj_didik"),
    "tinggal": ("jenis_tinggal", "nm_jns_tinggal"),
    "negara": ("kewarganegaraan", "nm_negara"),
}

def ref(kind, kata=""):
    """Lookup ID referensi form mahasiswa (dipakai mhs_edit.py / mhs_add.py)."""
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    if kind not in REF_LIST:
        sys.exit("jenis ref: " + ", ".join(REF_LIST))
    key, label = REF_LIST[kind]
    data = api_get("/mahasiswa/select/edit")
    items = data.get(key) or []
    if items and isinstance(items[0], list):
        items = items[0]
    kata = (kata or "").lower()
    hit = [x for x in items if not kata or kata in json.dumps(x, ensure_ascii=False).lower()]
    print(f"{kind}: {len(hit)} dari {len(items)}")
    for x in hit[:100]:
        print(" ", json.dumps(x, ensure_ascii=False))
    if len(hit) > 100:
        print(f"  ... {len(hit) - 100} lagi (persempit kata kunci)")

def check():
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0(
        {"pagination": {"page": 1}, "x": None}))))
    assert json.loads(q["pagination"]) == {"page": 1} and "x" not in q
    lengkap = {"nik": "3201010101010001", "nisn": "1", "email": "a@b", "no_hp": "08",
               "ds_kel": "X", "id_wil": "1", "nm_ibu_kandung": "Y",
               "tmpt_lahir": "Z", "tgl_lahir": "2000-01-01",
               "id_agama": 1, "kewarganegaraan": {"id_negara": "ID"}, "jk": "L"}
    assert audit_anomali(lengkap) == []
    assert audit_anomali({"nik": "123"}) == [
        "NISN kosong", "email kosong", "HP kosong", "kelurahan kosong",
        "kecamatan kosong", "nama ibu kosong", "tempat lahir kosong",
        "tanggal lahir kosong", "agama kosong", "kewarganegaraan kosong",
        "jenis kelamin kosong", "NIK bukan 16 digit"]
    print("check ok: axios0 + audit biodata")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "ref":
        ref(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--prodi", default="", help="filter nama prodi (substring)")
    ap.add_argument("--status", default="", help="filter status (substring)")
    ap.add_argument("--cari", default="", help="cari nama/NIM di server")
    ap.add_argument("--audit", action="store_true", help="cek kelengkapan biodata")
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah baris")
    a = ap.parse_args()
    run(prodi=a.prodi, status=a.status, cari=a.cari, audit=a.audit, limit=a.limit)
