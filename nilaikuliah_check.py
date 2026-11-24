#!/usr/bin/env python3
"""Cek Nilai Perkuliahan TA aktif 100% via API (token).
Pola: POLA_SCRIPT.md (cek token -> TA dinamis -> tarik index axios0).
Kolom: status sync, semester, kode/nama MK, kelas, SKS, peserta,
peserta sudah dinilai. Anomali: belum sync, sudah-dinilai < peserta.
Pakai: python3 nilaikuliah_check.py [--kode SF24664]
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

def fetch_nilai(id_smt, nm_smt):
    rows, page, total = [], 1, None
    while True:
        data, n = api_get("/nilai/index", {
            "pagination": {"sortBy": "kode_mk", "descending": False, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "", "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        total = total or n
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return rows, total

def nilai_anomali(r):
    out = []
    if r.get("_jmlmhs_gagal"):
        out.append("jumlah peserta gagal dibaca")
    if r.get("_dinilai_gagal"):
        out.append("jumlah dinilai gagal dibaca")
    if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
        out.append("belum sync")
    if not r.get("_jmlmhs_gagal") and not r.get("_dinilai_gagal"):
        try:
            if int(r.get("jml_sudahdinilai")) < int(r.get("jmlmhs")):
                out.append(f"dinilai {r.get('jml_sudahdinilai')} dari {r.get('jmlmhs')} peserta")
        except (TypeError, ValueError):
            out.append("jumlah peserta/dinilai tidak valid")
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
    sem = {s["id_smt"]: s for s in api_get("/nilai/filter")["list_semester"][0]}
    if ta not in sem:
        sys.exit(f"GAGAL: {ta} tak ada di list semester.")
    rows, total = fetch_nilai(ta, sem[ta]["nm_smt"])
    if kode:
        rows = [r for r in rows if kode.upper() in r.get("kode_mk", "").upper()]
    for r in rows:  # jml per baris (index kasih 0)
        try:
            r["jmlmhs"] = int(api_get(f"/kelas/jumlah_jmlmhs/{r['id_kls']}"))
        except Exception:
            r["_jmlmhs_gagal"] = True
        try:
            r["jml_sudahdinilai"] = int(
                api_get(f"/kelas/jumlah_jmlsudahdinilai/{r['id_kls']}"))
        except Exception:
            r["_dinilai_gagal"] = True
    out = f"nilaikuliah_{kode}.csv" if kode else f"nilaikuliah_{ta}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["nm_smt", "kode_mk", "nm_mk", "nm_kls",
                                          "sks_mk", "jmlmhs", "jml_sudahdinilai",
                                          "anomali", "id_kls"], lineterminator="\n")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in
                   ["nm_smt", "kode_mk", "nm_mk", "nm_kls", "sks_mk",
                    "jmlmhs", "jml_sudahdinilai", "id_kls"]}
            row["anomali"] = "; ".join(nilai_anomali(r))
            w.writerow(row)
    kurang = [r for r in rows if any(a.startswith("dinilai ") for a in nilai_anomali(r))]
    print(f"total={len(rows)} (server: {total}) "
          f"belum_sync={sum('belum sync' in nilai_anomali(r) for r in rows)} "
          f"gagal_baca={sum(any('gagal dibaca' in a for a in nilai_anomali(r)) for r in rows)}")
    print(f"dinilai-kurang-dari-peserta: {len(kurang)}")
    for r in kurang[:20]:
        print(f"  {r.get('kode_mk')} kls {r.get('nm_kls')}: "
              f"{r.get('jml_sudahdinilai')}/{r.get('jmlmhs')}")
    print("tersimpan:", out)

def check():
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0(
        {"pagination": {"page": 1}, "flag": True, "x": None}))))
    assert json.loads(q["pagination"]) == {"page": 1}
    assert q["flag"] == "true" and "x" not in q
    assert nilai_anomali({"jmlmhs": 10, "jml_sudahdinilai": 8}) == [
        "dinilai 8 dari 10 peserta"]
    assert "jumlah peserta gagal dibaca" in nilai_anomali({"_jmlmhs_gagal": True})
    print("check ok: axios0 + anomali nilai")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--kode", default=None)
    run(kode=ap.parse_args().kode)
