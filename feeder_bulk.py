#!/usr/bin/env python3
"""Bulk satu-per-satu Neo Feeder (milik sendiri via Tailscale).
Alur: login MANUAL 1x di browser (pilih 2025/2026 Genap = id_smt 20252, isi captcha sendiri),
lalu ambil token: DevTools > Application > Local Storage > _token.
Jalankan: FEEDER_TOKEN=xxx python3 feeder_bulk.py data.csv --path /mahasiswa/save --dry-run
Stdlib only, tanpa dependency baru.
"""
import csv, json, os, sys, time, urllib.request

BASE = os.environ.get("FEEDER_BASE", "http://localhost:8100/ws")

def post_one(api_path, payload, token):
    req = urllib.request.Request(
        BASE + api_path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "authorization": "Bearer " + token},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()[:500]
    except Exception as e:
        return 0, str(e)[:500]

def run(csv_path, api_path, token=None, dry_run=True, delay=0.5):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    log = open("bulk.log", "a", encoding="utf-8")
    ok = fail = 0
    for i, row in enumerate(rows, 1):
        if dry_run:
            print(f"[dry {i}/{len(rows)}] {row}")
            ok += 1
        else:
            st, body = post_one(api_path, row, token)
            msg = f"[{i}/{len(rows)}] {st} {row.get('nim', row.get('id', '?'))} -> {body[:120]}"
            print(msg); log.write(msg + "\n")
            if st in (200, 201): ok += 1
            else: fail += 1
            time.sleep(delay)  # ponytail: jeda global, per-baris retry jika throughput matters
    log.close()
    print(f"selesai: ok={ok} fail={fail}")
    return ok, fail

def demo():
    # self-check tanpa network: parsing + payload tetap utuh
    import io
    sample = "nim,nama,id_smt\n123,Andi,20252\n456,Budi,20252\n"
    rows = list(csv.DictReader(io.StringIO(sample)))
    assert len(rows) == 2 and rows[0]["id_smt"] == "20252", "csv parse rusak"
    assert json.loads(json.dumps(rows[0]))["nim"] == "123", "payload rusak"
    print("demo ok: 2 baris, id_smt 20252 (2025/2026 Genap)")

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    if sys.argv[1] == "demo":
        demo(); sys.exit(0)
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("csv_path"); p.add_argument("--path", default="/mahasiswa/save")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--go", action="store_true", help="kirim beneran (default dry-run)")
    a = p.parse_args()
    tok = os.environ.get("FEEDER_TOKEN", "")
    if a.go and not tok:
        sys.exit("isi FEEDER_TOKEN dulu dari localStorage _token setelah login manual")
    run(a.csv_path, a.path, tok, dry_run=not a.go)
