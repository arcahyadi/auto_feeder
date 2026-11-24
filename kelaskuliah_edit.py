#!/usr/bin/env python3
"""Edit Kelas Kuliah via API (satu-per-satu). Pola: POLA_SCRIPT.md.
PUT /ws/kelas/edit/<id_kls> dengan objek baris penuh (pola form:
id_smt dari id_smt_masking, tanggal split T[0]).
Resolusi: kode_mk + nm_kls -> id_kls via index TA aktif.
CSV: kode_mk,kelas,field,nilai  (satu perubahan per baris; field = nm_kls)
Pakai: python3 kelaskuliah_edit.py data.csv [--go]   (default dry-run)
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
FIELDS = {"nm_kls"}

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

def api(m, path, payload=None, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(axios0(params))
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "authorization": "Bearer " + TOKEN},
        method=m)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")

def check_token():
    try:
        return "list_status" in json.dumps(api("GET", "/mhslulus/filter"))
    except Exception:
        return False

def active_ta():
    return api("GET", "/history_pendidikan/periode_aktif")[0]["id_smt"]

def resolve(kode, kelas, ta, nm_smt):
    d, _ = api("GET", "/kelas/index", params={
        "pagination": {"sortBy": "id_sms", "descending": True, "page": 1,
                       "rowsPerPage": 10, "rowsNumber": 0},
        "filter": kode, "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
        "setFilter": {"semester": [{"id_smt": ta, "nm_smt": nm_smt}], "prodi": []},
        "extendFilter": {"filter_sync": "semua"}, "sortList": None})
    hit = [r for r in d if r.get("kode_mk") == kode and r.get("nm_kls") == kelas]
    if not hit:
        raise ValueError(f"{kode}-{kelas} tak ketemu di {ta}")
    return hit[0]

def run(csv_path, go, delay=1.0):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    if go and not TOKEN:
        sys.exit("isi FEEDER_TOKEN di .env dulu")
    log = open("bulk-kelas.log", "a", encoding="utf-8")
    if go:
        print("cek token...", end=" ", flush=True)
        if not check_token():
            sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
        print("valid ✓")
    ta = active_ta()
    sem = {s["id_smt"]: s for s in api("GET", "/kelas/filter")["list_semester"][0]}
    nm_smt = sem[ta]["nm_smt"]
    print("TA aktif:", ta, "| baris:", len(rows))
    ok, fail = 0, 0
    for i, r in enumerate(rows, 1):
        try:
            if r.get("field") not in FIELDS:
                raise ValueError(f"field {r.get('field')!r} tidak diizinkan")
            cur = resolve(r["kode_mk"], r["kelas"], ta, nm_smt)
            data = dict(cur)
            data[r["field"]] = r["nilai"]
            if not go:
                print(f"[dry {i}/{len(rows)}] {r['kode_mk']}-{r['kelas']} "
                      f"{r['field']}: {cur.get(r['field'])} -> {r['nilai']}")
                ok += 1
                continue
            res = api("PUT", f"/kelas/edit/{cur['id_kls']}", data)
            good = res.get("code") == 200
            back = resolve(r["kode_mk"], r["kelas"], ta, nm_smt)
            ver = str(back.get(r["field"], "")) == r["nilai"]
            msg = (f"[{i}/{len(rows)}] {r['kode_mk']}-{r['kelas']} -> "
                   f"{res.get('code')} verifikasi={ver}")
            print(msg); log.write(msg + "\n"); log.flush()
            ok, fail = (ok + 1, fail) if good and ver else (ok, fail + 1)
        except Exception as e:
            msg = f"[{i}/{len(rows)}] {r.get('kode_mk')}-{r.get('kelas')} GAGAL: {str(e)[:200]}"
            print(msg); log.write(msg + "\n"); fail += 1
        time.sleep(delay)  # ponytail: jeda global
    log.close(); print(f"selesai: ok={ok} fail={fail}")

def check():
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0(
        {"pagination": {"page": 1}, "x": None}))))
    assert json.loads(q["pagination"]) == {"page": 1} and "x" not in q
    assert FIELDS == {"nm_kls"}
    print("check ok: axios0 + allowlist field kelas")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, go=a.go)
