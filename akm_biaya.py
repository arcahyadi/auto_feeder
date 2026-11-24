#!/usr/bin/env python3
"""Update biaya_smt AKM via API (satu-per-satu). Pola: POLA_SCRIPT.md.
PUT /ws/kuliahmhs/edit?id_reg_pd=..&id_smt=.. dengan objek form lengkap
(list_mahasiswa, filter_status, filter_pembiayaan + masking).
CSV: nim,biaya  (biaya digit polos, misal 9100000)
Pakai: python3 akm_biaya.py data.csv [--go]   (default dry-run)
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse

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

def ax0(params):
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

def api(m, path, payload=None, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(ax0(params))
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "authorization": "Bearer " + TOKEN},
        method=m)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode() or "{}")

def check_token():
    try:
        return "list_status" in json.dumps(api("GET", "/mhslulus/filter"))
    except Exception:
        return False

def active_ta():
    return api("GET", "/history_pendidikan/periode_aktif")[0]["id_smt"]

def idx(nim, ta, nm_smt):
    d, _ = api("GET", "/kuliahmhs/index", params={
        "pagination": {"sortBy": "st.nm_smt", "descending": True, "page": 1,
                       "rowsPerPage": 5, "rowsNumber": 0},
        "filter": nim, "columnSearch": ["pd.nm_pd", "p.nipd"],
        "setFilter": {"semester": [{"id_smt": ta, "nm_smt": nm_smt}],
                      "prodi": [], "angkatan": [], "status": []},
        "extendFilter": {"filter_sync": "semua"}, "sortList": None})
    return d[0]

def update_biaya(nim, biaya, ta, nm_smt, cache):
    if "flt" not in cache:
        f = api("GET", "/kuliahmhs/filter")
        cache["flt"] = f
        cache["mhs"] = {}
    flt = cache["flt"]
    row = idx(nim, ta, nm_smt)
    if nim not in cache["mhs"]:
        m = api("GET", f"/kuliahmhs/cari_mahasiswa?nm_pd={nim}")["list_mahasiswa"]
        cache["mhs"][nim] = m[0][0] if isinstance(m[0], list) else m[0]
    mhs = cache["mhs"][nim]
    status = next((s for s in flt["list_status"][0]
                   if s["id_stat_mhs"] == row["id_stat_mhs"]), None)
    bia = next((s for s in flt["list_pembiayaan"][0]
                if s["id_pembiayaan"] == row["id_pembiayaan"]), None)
    if not status or not bia:
        return False, {"code": None,
                       "message": f"status '{row.get('nm_stat_mhs')}' / pembiayaan tidak ada di filter AKM (status Lulus harus manual)"}
    data = dict(row)
    data.update({"list_mahasiswa": mhs, "filter_status": status, "filter_pembiayaan": bia,
                 "id_smt_masking": nm_smt, "biaya_smt_masking": str(biaya),
                 "id_sms": mhs["id_sms"], "id_reg_pd": mhs["id_reg_pd"],
                 "id_stat_mhs": row["id_stat_mhs"], "id_pembiayaan": row["id_pembiayaan"],
                 "biaya_smt": str(biaya), "id_smt": ta})
    res = api("PUT", f"/kuliahmhs/edit?id_reg_pd={mhs['id_reg_pd']}&id_smt={ta}", data)
    if res.get("code") != 200:
        return False, res
    row2 = idx(nim, ta, nm_smt)
    return str(row2.get("biaya_smt", "")).startswith(str(biaya)), res

def run(csv_path, go, delay=1.0):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    if go and not TOKEN:
        sys.exit("isi FEEDER_TOKEN di .env dulu")
    log = open("bulk-biaya.log", "a", encoding="utf-8")
    if go:
        print("cek token...", end=" ", flush=True)
        if not check_token():
            sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
        print("valid ✓")
    ta = active_ta()
    sem = {s["id_smt"]: s for s in api("GET", "/kuliahmhs/filter")["list_semester"][0]}
    nm_smt = sem[ta]["nm_smt"]
    print("TA aktif:", ta, "| baris:", len(rows))
    cache, ok, fail = {}, 0, 0
    for i, r in enumerate(rows, 1):
        if not go:
            print(f"[dry {i}/{len(rows)}] {r['nim']} -> {r['biaya']}")
            ok += 1
            continue
        try:
            good, res = update_biaya(r["nim"], r["biaya"], ta, nm_smt, cache)
            msg = f"[{i}/{len(rows)}] {r['nim']} -> {res.get('code')} {res.get('message')} verifikasi={good}"
            print(msg); log.write(msg + "\n"); log.flush()
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        except Exception as e:
            msg = f"[{i}/{len(rows)}] {r['nim']} GAGAL: {str(e)[:200]}"
            print(msg); log.write(msg + "\n"); fail += 1
        time.sleep(delay)  # ponytail: jeda global
    log.close(); print(f"selesai: ok={ok} fail={fail}")

def check():
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(ax0(
        {"pagination": {"page": 1}, "x": None}))))
    assert json.loads(q["pagination"]) == {"page": 1} and "x" not in q
    print("check ok")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, go=a.go)
