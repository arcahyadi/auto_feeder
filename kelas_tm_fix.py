#!/usr/bin/env python3
"""Audit + perbaiki TM & SKS dosen sesuai aturan distribusi pertemuan & SKS dosen.
Aturan (urutan = urutan feeder): 2 dosen -> TM 8/8, SKS total/2;
3 dosen -> TM 5/5/6, SKS total/3 (0.67+0.67+0.66 utk 2 SKS).
Pakai: python3 kelas_tm_fix.py SF24212 SF24213 ... [--kelas A] [--go]
"""
import argparse, json, os, sys, urllib.request, urllib.parse

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

def bagi_tm_sks(n, total_sks):
    sen = round(float(total_sks) * 100)
    if n == 1:
        return [(16, 16, f"{sen / 100:.2f}")]
    if n == 2:
        s = f"{sen / 200:.2f}"
        return [(8, 8, s), (8, 8, s)]
    if n == 3:
        depan = (sen + 1) // 3
        blk = sen - 2 * depan
        return [(5, 5, f"{depan / 100:.2f}"), (5, 5, f"{depan / 100:.2f}"),
                (6, 6, f"{blk / 100:.2f}")]
    raise ValueError("aturan hanya 1/2/3 dosen")

def kelas_id(kode, kelas, ta, nm_smt):
    d, _ = api("GET", "/kelas/index", params={
        "pagination": {"sortBy": "id_sms", "descending": True, "page": 1,
                       "rowsPerPage": 10, "rowsNumber": 0},
        "filter": kode, "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
        "setFilter": {"semester": [{"id_smt": ta, "nm_smt": nm_smt}], "prodi": []},
        "extendFilter": {"filter_sync": "semua"}, "sortList": None})
    hit = [r for r in d if r.get("kode_mk") == kode and r.get("nm_kls") == kelas]
    if not hit:
        raise ValueError(f"{kode}-{kelas} tak ketemu")
    return hit[0]

def run(kodes, kelas="A", go=False):
    if not TOKEN:
        sys.exit("isi FEEDER_TOKEN di .env dulu")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid. Update .env dulu.")
    print("valid ✓")
    ta = active_ta()
    sem = {s["id_smt"]: s for s in api("GET", "/kelas/filter")["list_semester"][0]}
    nm_smt = sem[ta]["nm_smt"]
    n_fix = 0
    for kode in kodes:
        row = kelas_id(kode, kelas, ta, nm_smt)
        det = api("GET", f"/kelas/detail/{row['id_kls']}")["data"]
        dosen = det["dosen"]
        sks = det["detail"].get("sks_mk", "0")
        try:
            exp = bagi_tm_sks(len(dosen), sks)
        except ValueError as e:
            print(f"{kode}-{kelas}: {len(dosen)} dosen ({e}), SKIP manual")
            continue
        for d, (tr, tr2, sk) in zip(dosen, exp):
            cur = (str(d.get("jml_tm_renc", "")), str(d.get("jml_tm_real", "")),
                   f"{round(float(d.get('sks_subst_tot') or 0), 2):.2f}")
            want = (str(tr), str(tr2), sk)
            if cur == want:
                print(f"  ok: {kode}-{kelas} {d['nm_sdm']} {cur[0]}/{cur[1]} sks {cur[2]}")
                continue
            print(f"  SALAH: {kode}-{kelas} {d['nm_sdm']} {cur[0]}/{cur[1]} sks {cur[2]}"
                  f" -> {want[0]}/{want[1]} sks {want[2]}")
            if not go:
                continue
            cur2 = api("GET", f"/kelas/edit/dosen/{d['id_ajar']}")
            rec = cur2.get("data", cur2) if isinstance(cur2, dict) else cur2
            if not isinstance(rec, dict) or "id_ajar" not in rec:
                print(f"    SKIP: record {d['id_ajar']} tak terbaca")
                continue
            rec["jml_tm_renc"] = str(tr)
            rec["jml_tm_real"] = str(tr2)
            rec["sks_subst_tot"] = sk
            res = api("PUT", f"/kelas/edit/dosen/{d['id_ajar']}", rec)
            ok = res.get("code") == 200
            n_fix += ok
            print(f"    PUT -> {res.get('code')} {'OK' if ok else res}")
    print(f"selesai: {n_fix} diperbaiki" if go else "dry-run selesai (tambah --go untuk perbaiki)")

def check():
    assert bagi_tm_sks(2, 2) == [(8, 8, "1.00"), (8, 8, "1.00")]
    assert bagi_tm_sks(3, 2)[2] == (6, 6, "0.66")
    print("check ok")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("kodes", nargs="+")
    ap.add_argument("--kelas", default="A")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.kodes, kelas=a.kelas, go=a.go)
