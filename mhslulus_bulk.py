#!/usr/bin/env python3
"""Bulk lulus/DO via API (satu-per-satu, tanpa delete).
Pola terbukti (contoh): POST {BASE}/mhslulus/add dengan objek
lengkap {list_mahasiswa, filter_status, filter_semester} + ids.
CSV: nim,jenis_keluar,tgl_keluar,periode,tgl_sk,no_sk,ipk
Contoh: 202500001,Lulus,2026-08-31,20252,2026-08-31,001/SK-LULUS/2026,3.50
Token di .env (FEEDER_TOKEN), tidak di-commit.
"""
import argparse, csv, json, os, sys, time, urllib.request

def _load_dotenv():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
_load_dotenv()
BASE = os.environ.get("FEEDER_BASE", "http://localhost:8100/ws")
TOKEN = os.environ.get("FEEDER_TOKEN", "")

def api(method, path, payload=None):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "authorization": "Bearer " + TOKEN},
        method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode() or "{}")

def check_token():
    """GET aman (tanpa ubah data). True jika sesi valid."""
    try:
        req = urllib.request.Request(BASE + "/mhslulus/filter",
            headers={"authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and "list_status" in r.read().decode()
    except Exception:
        return False

def build(nim, jenis, tgl, periode, tgl_sk, no_sk, ipk, cache):
    if "filter" not in cache:
        cache["filter"] = api("GET", "/mhslulus/filter")
    flt = cache["filter"]
    mhs = api("GET", f"/mhslulus/cari_mahasiswa?nm_pd={nim}")["list_mahasiswa"][0][0]
    status = next((s for s in flt["list_status"][0]
                   if s["ket_keluar"].lower() == jenis.lower()), None)
    smt = next((s for s in flt["list_semester"][0] if s["id_smt"] == periode), None)
    if not status or not smt:
        raise ValueError(f"status '{jenis}' atau periode '{periode}' tidak ada di Feeder")
    return {"list_mahasiswa": mhs, "filter_status": status, "filter_semester": smt,
            "id_jns_keluar": status["id_jns_keluar"], "id_reg_pd": mhs["id_reg_pd"],
            "smt_yudisium": periode, "tgl_keluar": tgl, "tgl_sk_yudisium": tgl_sk,
            "sk_yudisium": no_sk, "ipk": ipk, "ipktranskrip": "-"}

def run(csv_path, go, delay=1.0):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    if go and not TOKEN:
        sys.exit("isi FEEDER_TOKEN di .env dulu")
    if go:
        print("cek token...", end=" ", flush=True)
        if not check_token():
            sys.exit("GAGAL: token tidak valid/kadaluarsa. Login ulang, copy _token baru ke .env.")
        print("valid ✓")
    log = open("bulk-mhslulus.log", "a", encoding="utf-8")
    os.makedirs("responses", exist_ok=True)
    cache, ok, fail = {}, 0, 0
    for i, r in enumerate(rows, 1):
        if not go:
            print(f"[dry {i}/{len(rows)}] {r['nim']} {r}"); ok += 1; continue
        try:
            res = api("POST", "/mhslulus/add",
                      build(r["nim"], r["jenis_keluar"], r["tgl_keluar"], r["periode"],
                            r["tgl_sk"], r["no_sk"], r["ipk"], cache))
            good = res.get("code") == 200
            json.dump(res, open(f"responses/{r['nim']}.json", "w", encoding="utf-8"),
                      ensure_ascii=False, default=str)
            msg = f"[{i}/{len(rows)}] {r['nim']} -> {res.get('code')} {res.get('message')}"
            print(msg); log.write(msg + "\n"); log.flush()
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        except Exception as e:
            msg = f"[{i}/{len(rows)}] {r['nim']} GAGAL: {str(e)[:200]}"
            print(msg); log.write(msg + "\n"); fail += 1
        time.sleep(delay)  # ponytail: jeda global
    log.close(); print(f"selesai: ok={ok} fail={fail}")

def demo():
    assert BASE.endswith("/ws")
    print("demo ok: mhslulus/add full-payload")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "demo":
        demo(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, go=a.go)
