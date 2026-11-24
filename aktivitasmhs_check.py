#!/usr/bin/env python3
"""Cek Aktivitas Mahasiswa TA aktif 100% via API (token). Pola: POLA_SCRIPT.md.
Alur: cek token -> TA aktif dinamis -> GET /ws/aktmhs/index (axios0,
semester aktif) -> CSV + ringkasan per jenis + belum sync.
Kolom: prodi, semester, jenis, judul, tgl SK, asal data.
Update: tombol create (edit) per baris; API mengikuti pola PUT objek lengkap
  PUT /ws/aktmhs/edit/<id> (lihat akm_biaya.py untuk pola). Anggota/pembimbing/
  penguji via sub-path deleteanggota/deletedosenpembimbing/deletedosenpenguji.
  Untuk update konkret, siapkan id + field-nya dulu lalu konfirmasi.
Pakai: python3 aktivitasmhs_check.py [--cari KKN]
"""
import argparse, csv, json, os, sys, urllib.request, urllib.parse

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

def aktivitas_anomali(r):
    out = []
    if not str(r.get("sk_tugas") or "").strip():
        out.append("SK kosong")
    if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
        out.append("belum sync")
    return out

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

def active_ta():
    return api_get("/history_pendidikan/periode_aktif")[0]["id_smt"]

def fetch_aktmhs(id_smt, nm_smt, cari=""):
    rows, page, total = [], 1, None
    while True:
        data, n = api_get("/aktmhs/index", {
            "pagination": {"sortBy": "st.nm_smt", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": cari,
            "columnSearch": ["m.judul_akt_mhs", "m.sk_tugas", "angg.nm_pd",
                             "angg.nipd", "uji.nm_sdm", "bimb.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}],
                          "prodi": [], "jenisaktivitas": [], "asaldata": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        total = total or n
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return rows, total

def run(cari=""):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ta = active_ta()
    print("TA aktif (API):", ta)
    # /aktmhs/filter mengembalikan error PostgreSQL; daftar semester kelas stabil.
    sem = {s["id_smt"]: s for s in api_get("/kelas/filter")["list_semester"][0]}
    if ta not in sem:
        sys.exit(f"GAGAL: {ta} tak ada di list semester.")
    rows, total = fetch_aktmhs(ta, sem[ta]["nm_smt"], cari)
    out = f"aktivitasmhs_{ta}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["nm_lemb", "nm_smt", "jenis", "judul",
                                          "tgl_sk", "lokasi", "asal_data", "anomali",
                                          "id_akt_mhs"], lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({"nm_lemb": r.get("nm_lemb", ""), "nm_smt": r.get("nm_smt", ""),
                        "jenis": r.get("jenis", r.get("nm_jns_akt_mhs", "")),
                        "judul": r.get("judul_akt_mhs", r.get("judul", "")),
                        "tgl_sk": str(r.get("tgl_sk_tugas") or r.get("tgl_sk") or "")[:10],
                        "lokasi": r.get("lokasi_kegiatan", ""),
                        "asal_data": r.get("asal_data", ""),
                        "anomali": "; ".join(aktivitas_anomali(r)),
                        "id_akt_mhs": r.get("id_akt_mhs", "")})
    from collections import Counter
    usang = lambda r: str(r.get("last_update", "")) > str(r.get("last_sync", ""))
    print(f"total={len(rows)} (server: {total}) belum_sync={sum(1 for r in rows if usang(r))}")
    print("SK kosong:", sum("SK kosong" in aktivitas_anomali(r) for r in rows))
    print(dict(Counter(r.get("jenis", r.get("nm_jns_akt_mhs", "?")) for r in rows)))
    print("tersimpan:", out)

def check():
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0(
        {"pagination": {"page": 1}, "x": None}))))
    assert json.loads(q["pagination"]) == {"page": 1} and "x" not in q
    assert aktivitas_anomali({"sk_tugas": "", "last_update": "2", "last_sync": "1"}) == [
        "SK kosong", "belum sync"]
    print("check ok: axios0 + anomali aktivitas")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--cari", default="", help="saring judul/SK/nama/NIM/dosen")
    run(cari=ap.parse_args().cari)
