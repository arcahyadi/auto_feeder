#!/usr/bin/env python3
"""Cek Daya Tampung (/dayatampung) via API.
Alur: cek token -> TA aktif dinamis (/history_pendidikan/periode_aktif,
boleh dioverride --semester) -> GET /ws/dayatampung/index (axios0) ->
CSV + ringkasan.
Kolom halaman: Semester, Program Studi, Status Prodi, Target MHS Baru,
Tanggal Awal/Akhir Perkuliahan (+ field angka lain ikut disimpan di CSV).
Pakai: python3 dayatampung_check.py [--semester 20261]
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
    """Serialisasi param gaya axios 0.x."""
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
    pa = api_get("/history_pendidikan/periode_aktif")
    return pa[0]["id_smt"]

def semester_map():
    f = api_get("/dayatampung/filter")
    return {s["id_smt"]: s["nm_smt"] for s in f["list_semester"][0]}

def allow_map():
    """id_smt/id_sms yang boleh diedit (periode_aktif_all)."""
    try:
        d = api_get("/history_pendidikan/periode_aktif_all")
    except Exception:
        return None
    return {(x.get("id_smt"), x.get("id_sms")) for x in d} if isinstance(d, list) else None

def fetch_daya(id_smt, nm_smt):
    rows, page, total = [], 1, None
    while True:
        data, n = api_get("/dayatampung/index", {
            "pagination": {"sortBy": "t.nm_smt", "descending": False,
                           "page": page, "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "", "columnSearch": ["t.nm_smt", "nm_lemb"],
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
    return rows, total

def daya_anomali(r, allow=None):
    out = []
    if str(r.get("last_update") or "") > str(r.get("last_sync") or ""):
        out.append("belum sync")
    if (r.get("target_mhs_baru") or "0").strip() in ("", "0"):
        out.append("target 0")
    if allow is not None and (r.get("id_smt"), r.get("id_sms")) not in allow \
            and (r.get("id_smt"), None) not in allow:
        out.append("tidak bisa edit")
    return out

COLS = ["nm_smt", "nm_lemb", "status_prodi", "target_mhs_baru",
        "calon_ikut_seleksi", "calon_lulus_seleksi", "daftar_sbg_mhs",
        "pst_undur_diri", "jml_mgu_kul", "tgl_awal_kul", "tgl_akhir_kul",
        "anomali", "id_sms", "id_smt"]

def tgl(s):
    return str(s or "").split("T")[0]

def run(semester=None):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ta = semester or active_ta()
    sem = semester_map()
    if ta not in sem:
        sys.exit(f"GAGAL: semester {ta} tak ada di filter dayatampung.")
    print("semester:", ta, sem[ta])
    rows, total = fetch_daya(ta, sem[ta])
    allow = allow_map()
    out = f"dayatampung_{ta}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in COLS if k != "anomali"}
            row["tgl_awal_kul"] = tgl(r.get("tgl_awal_kul"))
            row["tgl_akhir_kul"] = tgl(r.get("tgl_akhir_kul"))
            row["anomali"] = "; ".join(daya_anomali(r, allow))
            w.writerow(row)
    print(f"total={len(rows)} (server: {total}) "
          f"belum_sync={sum('belum sync' in daya_anomali(r, allow) for r in rows)} "
          f"target_0={sum('target 0' in daya_anomali(r, allow) for r in rows)}")
    print("tersimpan:", out)

def check():
    """Self-check offline."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0({
        "pagination": {"page": 1}, "columnSearch": ["a"], "flag": True, "x": None,
    }))))
    assert json.loads(q["pagination"]) == {"page": 1}
    assert q["columnSearch[]"] == "a" and q["flag"] == "true" and "x" not in q
    assert daya_anomali({"last_update": "2026-04-07", "last_sync": "2026-01-01",
                         "target_mhs_baru": "0"}) == ["belum sync", "target 0"]
    assert daya_anomali({"last_update": "2026-01-01", "last_sync": "2026-04-07",
                         "target_mhs_baru": "6"}) == []
    assert tgl("2026-03-01T00:00:00.000Z") == "2026-03-01"
    print("check ok: serialisasi axios 0.x + anomali daya tampung")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--semester", default=None)
    run(semester=ap.parse_args().semester)
