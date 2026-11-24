#!/usr/bin/env python3
"""Cek Kelas Kuliah TA aktif 100% via API (token).
Alur: cek token -> TA aktif dinamis (/history_pendidikan/periode_aktif) ->
tarik GET /ws/kelas/index per halaman -> lengkapi peserta per kelas ->
CSV + ringkasan (sync, peserta 0, tanpa dosen).
Catatan penting: backend ini ekspektasi serialisasi param axios 0.x:
  objek nested -> JSON string (bukan brackets!), array -> key[] repeat,
  bool -> true/false lowercase. Brackets murni dibalas {}.
Pakai: ./.venv/bin/python kelaskuliah_check.py [--kode SF24664]
  (atau python3 sistem, hanya stdlib; tanpa browser, tanpa login manual)
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
    """Serialisasi param gaya axios 0.x (dipakai api_get)."""
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
    """GET dengan serialisasi axios 0.x."""
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
    pa = api_get("/history_pendidikan/periode_aktif")
    return pa[0]["id_smt"]

def semesters():
    f = api_get("/kelas/filter")
    return {s["id_smt"]: s for s in f["list_semester"][0]}

def fetch_kelas(id_smt, nm_smt, kode=None):
    rows, page, total = [], 1, None
    base = {"sortBy": "id_sms", "descending": True, "rowsPerPage": 100, "rowsNumber": 0}
    while True:
        pg = dict(base, page=page)
        data, n = api_get("/kelas/index", {
            "pagination": pg, "filter": kode or "",
            "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        total = total or n
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    # lengkapi peserta per kelas
    for r in rows:
        try:
            r["jmlmhs"] = int(api_get(f"/kelas/jumlah_jmlmhs/{r['id_kls']}"))
        except Exception:
            r["_jmlmhs_gagal"] = True
    return rows, total

def kelas_anomali(r):
    out = []
    if not (r.get("dosen") or "").strip():
        out.append("tanpa dosen")
    if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
        out.append("belum sync")
    if r.get("_jmlmhs_gagal"):
        out.append("jumlah peserta gagal dibaca")
    elif str(r.get("jmlmhs")) == "0":
        out.append("peserta 0")
    return out

def run(kode=None):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ta = active_ta()
    print("TA aktif (API):", ta)
    sem = semesters()
    if ta not in sem:
        sys.exit(f"GAGAL: {ta} tak ada di list semester.")
    rows, total = fetch_kelas(ta, sem[ta]["nm_smt"], kode)
    if kode:
        rows = [r for r in rows if kode.upper() in r.get("kode_mk", "").upper()]
    out = f"kelaskuliah_{kode}.csv" if kode else f"kelaskuliah_{ta}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["nm_smt", "kode_mk", "nm_mk", "nm_kls",
                                          "sks_mk", "dosen", "jmlmhs", "anomali", "id_kls"],
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in
                   ["nm_smt", "kode_mk", "nm_mk", "nm_kls", "sks_mk",
                    "dosen", "jmlmhs", "id_kls"]}
            row["anomali"] = "; ".join(kelas_anomali(r))
            w.writerow(row)
    print(f"total={len(rows)} (server: {total}) "
          f"belum_sync={sum('belum sync' in kelas_anomali(r) for r in rows)}")
    print("tanpa-dosen:", sum("tanpa dosen" in kelas_anomali(r) for r in rows),
          "| peserta-0:", sum("peserta 0" in kelas_anomali(r) for r in rows),
          "| peserta-gagal-baca:",
          sum("jumlah peserta gagal dibaca" in kelas_anomali(r) for r in rows))
    print("tersimpan:", out)

def demo():
    print("demo: gunakan 'check' untuk self-check offline")

def check():
    """Self-check offline: serialisasi axios 0.x."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0({
        "pagination": {"page": 1}, "columnSearch": ["a"], "flag": True, "x": None,
    }))))
    assert json.loads(q["pagination"]) == {"page": 1}
    assert q["columnSearch[]"] == "a" and q["flag"] == "true" and "x" not in q
    assert kelas_anomali({"dosen": "", "jmlmhs": 0}) == ["tanpa dosen", "peserta 0"]
    assert "jumlah peserta gagal dibaca" in kelas_anomali({"dosen": "X", "_jmlmhs_gagal": True})
    print("check ok: serialisasi axios 0.x + anomali kelas")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "demo":
        demo(); sys.exit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--kode", default=None)
    run(kode=ap.parse_args().kode)
