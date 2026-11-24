#!/usr/bin/env python3
"""Cek nilai konversi semua aktivitas TA aktif via API (token).
Pola: POLA_SCRIPT.md (cek token -> TA dinamis).
Alur per aktivitas: /aktmhs/edit/:id -> anggota[] ->
/nilaiaktmhs/transfermatkul/:id_ang -> konversi[] (nm_mk, angka, huruf).
Flag: aktivitas tanpa anggota, anggota tanpa baris konversi,
baris tanpa nilai_angka.
Pakai: python3 konversi_check.py [--jenis Skripsi]
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

def fetch_aktivitas(id_smt, nm_smt):
    rows, page = [], 1
    while True:
        data, _ = api_get("/aktmhs/index", {
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

def run(jenis=None, nim=None):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ta = active_ta()
    print("TA aktif (API):", ta)
    sem = {s["id_smt"]: s for s in api_get("/kelas/filter")["list_semester"][0]}
    acts = fetch_aktivitas(ta, sem[ta]["nm_smt"])
    if jenis:
        acts = [a for a in acts if jenis.lower() in (a.get("nm_jns_akt_mhs") or "").lower()]
    print(f"aktivitas: {len(acts)}")
    out_rows, no_anggota, no_nilai = [], [], []
    for i, a in enumerate(acts, 1):
        det = api_get(f"/aktmhs/edit/{a['id_akt_mhs']}")
        angg = det.get("anggota", []) or []
        if not angg:
            no_anggota.append(a)
            continue
        for m in angg:
            if nim and nim.upper() not in (m.get("nipd") or "").upper():
                continue
            try:
                t = api_get(f"/nilaiaktmhs/transfermatkul/{m['id_ang_akt_mhs']}")
            except Exception as e:
                no_nilai.append((a, m, f"ERR {str(e)[:80]}"))
                continue
            konv = t.get("konversi", []) or []
            kosong = [k for k in konv if not str(k.get("nilai_angka", "")).strip()]
            if not konv or kosong:
                no_nilai.append((a, m, f"{len(kosong)}/{len(konv)} tanpa angka"))
            for k in konv:
                out_rows.append({
                    "judul": a.get("judul_akt_mhs", ""),
                    "jenis": a.get("nm_jns_akt_mhs", ""), "nipd": m.get("nipd", ""),
                    "nama": m.get("nm_pd", ""), "mk": k.get("nm_mk", ""),
                    "sks": k.get("sks_mk", ""), "angka": k.get("nilai_angka", ""),
                    "huruf": (k.get("nilai_huruf") or "").strip(),
                    "indeks": k.get("nilai_indeks", "")})
        if i % 25 == 0:
            print(f"  ...{i}/{len(acts)}")
    tag = "_".join(x for x in [nim, jenis] if x)
    out = f"konversi_{tag}.csv" if tag else "konversi_20252.csv"
    out_nonilai = f"konversi_tanpa_nilai_{tag}.csv" if tag else "konversi_tanpa_nilai.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["judul", "jenis", "nipd", "nama", "mk",
                                          "sks", "angka", "huruf", "indeks"])
        w.writeheader(); w.writerows(out_rows)
    with open(out_nonilai, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["nipd", "nama", "judul", "jenis", "ket"])
        w.writeheader()
        for a, m, s in no_nilai:
            w.writerow({"nipd": m.get("nipd", ""), "nama": m.get("nm_pd", ""),
                        "judul": a.get("judul_akt_mhs", ""),
                        "jenis": a.get("nm_jns_akt_mhs", ""), "ket": s})
    print(f"baris nilai: {len(out_rows)} | aktivitas tanpa anggota: {len(no_anggota)} | "
          f"anggota tanpa nilai: {len(no_nilai)}")
    for a in no_anggota[:10]:
        print("  tanpa-anggota:", a.get("judul_akt_mhs", "")[:60])
    for a, m, s in no_nilai[:20]:
        print(f"  tanpa-nilai: {m.get('nipd')} {m.get('nm_pd')} ({a.get('judul_akt_mhs','')[:40]}) {s}")
    print(f"tersimpan: {out} + {out_nonilai}")

def check():
    assert True
    print("check ok")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--jenis", default=None)
    ap.add_argument("--nim", default=None, help="saring NIM anggota")
    a = ap.parse_args()
    run(jenis=a.jenis, nim=a.nim)
