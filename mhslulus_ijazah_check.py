#!/usr/bin/env python3
"""Tarik mahasiswa lulus tahun tertentu yang belum punya nomor ijazah.
Alur: cek token -> GET /ws/mhslulus/index (semua semester, axios0) ->
saring tgl_keluar tahun + no_seri_ijazah kosong -> CSV + ringkasan.
Pakai: python3 mhslulus_ijazah_check.py [--tahun 2026]
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse
from collections import Counter

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

def fetch_page(page, ulang=5):
    """Server kadang balas {} (transien); ulangi sebelum menyerah."""
    for i in range(ulang):
        data = api_get("/mhslulus/index", {
            "pagination": {"sortBy": "id_reg_pd", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "", "columnSearch": ["m.nm_pd", "m.nipd", "p.nm_prodi"],
            "setFilter": {"semester": [], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list):
            return data[0], int(data[1])
        time.sleep(1 + i)
    sys.exit(f"GAGAL: /mhslulus/index halaman {page} balas {json.dumps(data)[:200]}")

def fetch_lulus():
    rows, page, total = [], 1, None
    while True:
        batch, n = fetch_page(page)
        total = n if total is None else total
        assert n == total, "jumlah berubah saat paginasi"
        rows += batch
        if len(batch) < 100:
            break
        page += 1
    assert len(rows) == total, "paginasi tidak lengkap"
    return rows

def blank(v):
    return v is None or str(v).strip() == ""

def saring(rows, tahun):
    hit = [r for r in rows
           if str(r.get("tgl_keluar") or "")[:4] == str(tahun)
           and str(r.get("ket_keluar") or "").strip().lower() == "lulus"
           and blank(r.get("no_seri_ijazah"))]
    return sorted(hit, key=lambda r: (str(r.get("tgl_keluar") or ""), r.get("nipd") or ""))

def run(tahun):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    rows = fetch_lulus()
    hit = saring(rows, tahun)
    out = f"mhslulus_{tahun}_tanpa_ijazah.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nipd", "nm_pd", "prodi", "tgl_keluar", "smt_yudisium",
                    "nm_smt_keluar", "sk_yudisium", "ipk", "no_seri_ijazah",
                    "tgl_terbit_ijazah", "id_reg_pd"])
        for x in hit:
            ipk = x.get("ipk")
            w.writerow([x.get("nipd"), x.get("nm_pd"), x.get("nm_lemb"),
                        str(x.get("tgl_keluar") or "")[:10], x.get("smt_yudisium"),
                        x.get("nm_smt_keluar"), x.get("sk_yudisium"),
                        f"{float(ipk):.2f}" if ipk is not None else "",
                        x.get("no_seri_ijazah") or "",
                        str(x.get("tgl_terbit_ijazah") or "")[:10], x.get("id_reg_pd")])
    print(json.dumps({"total_lulusan_semua": len(rows),
                      f"keluar_{tahun}": sum(str(r.get("tgl_keluar") or "")[:4] == str(tahun) for r in rows),
                      f"lulus_{tahun}_tanpa_no_ijazah": len(hit),
                      "prodi": dict(Counter(x.get("nm_lemb") for x in hit)),
                      "smt_keluar": dict(Counter(x.get("nm_smt_keluar") for x in hit)),
                      "file": out}, ensure_ascii=False, indent=1))
    print("tersimpan:", out)

def check():
    rows = [{"tgl_keluar": "2026-08-31T00:00:00.000Z", "no_seri_ijazah": None, "ket_keluar": "Lulus", "nipd": "2"},
            {"tgl_keluar": "2026-08-31T00:00:00.000Z", "no_seri_ijazah": "X", "ket_keluar": "Lulus", "nipd": "1"},
            {"tgl_keluar": "2026-08-31T00:00:00.000Z", "no_seri_ijazah": None, "ket_keluar": "Mengajukan pengunduran diri", "nipd": "4"},
            {"tgl_keluar": "2025-08-31T00:00:00.000Z", "no_seri_ijazah": None, "ket_keluar": "Lulus", "nipd": "3"}]
    hit = saring(rows, 2026)
    assert [r["nipd"] for r in hit] == ["2"], hit
    assert blank("") and blank(None) and not blank(" 9 ")
    print("check ok: filter tahun + Lulus + nomor ijazah kosong")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--tahun", default="2026")
    run(ap.parse_args().tahun)
