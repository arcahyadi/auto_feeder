#!/usr/bin/env python3
"""Isi nomor ijazah mahasiswa via API (satu-per-satu). Pola: POLA_SCRIPT.md.
PUT /ws/mhslulus/edit/<id_reg_pd> dengan objek form lengkap (hasil GET edit).
CSV: nim,no_seri_ijazah[,tgl_terbit_ijazah]
Pakai: python3 mhslulus_ijazah_edit.py data.csv            (dry-run)
       python3 mhslulus_ijazah_edit.py data.csv --go        (tulis)
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

def fetch_page(page, ulang=5):
    """Server kadang balas {} / filter non-kosong selalu {}; ulangi."""
    for i in range(ulang):
        r = api("GET", "/mhslulus/index", params={
            "pagination": {"sortBy": "id_reg_pd", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "", "columnSearch": ["m.nm_pd", "m.nipd", "p.nm_prodi"],
            "setFilter": {"semester": [], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        if isinstance(r, list) and len(r) == 2 and isinstance(r[0], list):
            return r[0], int(r[1])
        time.sleep(1 + i)
    raise RuntimeError(f"/mhslulus/index halaman {page} balas {json.dumps(r)[:120]}")

def peta_nim(cache):
    """nim -> id_reg_pd (paginasi penuh; filter server tak andal)."""
    if "peta" in cache:
        return cache["peta"]
    semua, page, total = {}, 1, None
    while True:
        batch, n = fetch_page(page)
        total = n if total is None else total
        assert n == total, "jumlah berubah saat paginasi"
        for x in batch:
            semua.setdefault(str(x.get("nipd")), x["id_reg_pd"])
        if len(batch) < 100:
            break
        page += 1
    assert len(semua) > 0
    cache["peta"] = semua
    return semua

def set_ijazah(nim, no_seri, tgl_terbit, go, cache):
    peta = peta_nim(cache)
    if nim not in peta:
        raise ValueError(f"{nim} tak ketemu di /mhslulus/index")
    if nim not in cache:
        cache[nim] = api("GET", "/mhslulus/edit/" + peta[nim])
    rec = cache[nim]
    lama = rec.get("no_seri_ijazah")
    if str(lama or "") == no_seri:
        return "skip", f"{nim} sudah {no_seri}"
    rec["no_seri_ijazah"] = no_seri
    if tgl_terbit:
        rec["tgl_terbit_ijazah"] = tgl_terbit
    if not go:
        nm = rec.get("nm_pd") or (rec.get("list_mahasiswa") or {}).get("nm_pd", "?")
        return "dry", f"{nim} {nm}: no_seri {lama!r} -> {no_seri!r}"
    res = api("PUT", "/mhslulus/edit/" + rec["id_reg_pd"], rec)
    if res.get("code") != 200:
        return "fail", f"{nim} PUT -> {res.get('code')} {res.get('message')}"
    back = api("GET", "/mhslulus/edit/" + rec["id_reg_pd"])
    ver = str(back.get("no_seri_ijazah") or "") == no_seri
    return ("ok" if ver else "fail"), f"{nim} PUT 200 verifikasi={ver}"

def run(csv_path, go, delay=1.0):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    if go and not TOKEN:
        sys.exit("isi FEEDER_TOKEN di .env dulu")
    if go:
        print("cek token...", end=" ", flush=True)
        if not check_token():
            sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
        print("valid ✓")
    print(f"mode={'TULIS' if go else 'DRY-RUN'} | baris={len(rows)}")
    cache = {}
    for i, r in enumerate(rows, 1):
        nim = (r.get("nim") or "").strip()
        no_seri = (r.get("no_seri_ijazah") or "").strip()
        tgl = (r.get("tgl_terbit_ijazah") or "").strip()
        try:
            st, msg = set_ijazah(nim, no_seri, tgl, go, cache)
        except Exception as e:
            st, msg = "fail", f"{nim} GAGAL: {str(e)[:200]}"
        print(f"[{i}/{len(rows)}] {st}: {msg}")
        if go:
            time.sleep(delay)  # ponytail: jeda global
    if not go:
        print("dry-run selesai (tambah --go untuk tulis)")

def check():
    rec = {"no_seri_ijazah": None}
    rec["no_seri_ijazah"] = "123"
    assert rec["no_seri_ijazah"] == "123"
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0({"a": None, "b": ["x"], "c": True}))))
    assert q["b[]"] == "x" and q["c"] == "true" and "a" not in q
    print("check ok: set no_seri + serialisasi")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, go=a.go)
