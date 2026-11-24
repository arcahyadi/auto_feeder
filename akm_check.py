#!/usr/bin/env python3
"""Cek AKM mahasiswa 100% via API (token). Pola: POLA_SCRIPT.md.
Alur: cek token -> TA aktif dinamis -> GET /ws/kuliahmhs/index
  (axios0, semester aktif saja) -> CSV + ringkasan IPS/IPK/SKS.
Pakai: python3 akm_check.py --nim 202500001
  Tanpa --nim: seluruh AKM semester aktif -> akm_<TA>.csv
"""
import argparse, csv, json, os, sys, urllib.request, urllib.parse
from decimal import Decimal, InvalidOperation

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

COLS = ["nipd", "nm_pd", "nm_lemb", "mulai_smt", "nm_smt", "nm_stat_mhs",
        "ips", "ipk", "sks_smt", "sks_total", "nm_pembiayaan", "biaya_smt",
        "anomali", "id_reg_pd"]

def studi(status):
    """Status sedang studi? Keluar/Cuti/Non-Aktif/Lulus wajar
    ber-IPS 0 dan SKS semester 0 -> bukan anomali."""
    s = " ".join((status or "").strip().lower().replace("-", " ").split())
    return not any(k in s for k in ("keluar", "cuti", "lulus", "non aktif",
                                    "nonaktif", "tidak aktif", "undur"))

def anomali(r):
    out = []
    if (str(r.get("ips") or "0") in ("", "0") and
            str(r.get("sks_smt") or "0") == "0" and studi(r.get("nm_stat_mhs"))):
        out.append("IPS/SKS 0")
    try:
        biaya_kosong = Decimal(str(r.get("biaya_smt") or "0")) == 0
    except InvalidOperation:
        biaya_kosong = True
    if biaya_kosong:
        out.append("biaya kosong")
    return out

def fetch_akm(id_smt, nm_smt, nim=None):
    rows, page, total = [], 1, None
    while True:
        data, n = api_get("/kuliahmhs/index", {
            "pagination": {"sortBy": "st.nm_smt", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": nim or "",
            "columnSearch": ["pd.nm_pd", "p.nipd"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}],
                          "prodi": [], "angkatan": [], "status": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        total = total or n
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return rows, total

def run(nim=None):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ta = active_ta()
    print("TA aktif (API):", ta)
    sem = {s["id_smt"]: s for s in api_get("/kuliahmhs/filter")["list_semester"][0]}
    if ta not in sem:
        sys.exit(f"GAGAL: {ta} tak ada di list semester.")
    rows, total = fetch_akm(ta, sem[ta]["nm_smt"], nim)
    out = f"akm_{nim}.csv" if nim else f"akm_{ta}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            r = dict(r)
            if r.get("mulai_smt"):
                r["mulai_smt"] = r["mulai_smt"][:4]
            r["anomali"] = "; ".join(anomali(r))
            w.writerow({k: r.get(k, "") for k in COLS})
    print(f"baris={len(rows)} (server: {total}) -> {out}")
    nol = [r for r in rows if "IPS/SKS 0" in anomali(r)]
    for r in nol:
        print(f"  WAJAR-DICEK {r.get('nipd')} {r.get('nm_pd')} | "
              f"{r.get('nm_stat_mhs')} | IPS/SKS 0")
    print(f"IPS/SKS 0 pada status studi: {len(nol)} dari {len(rows)}")
    print("biaya kosong:", sum("biaya kosong" in anomali(r) for r in rows))

def check():
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(axios0(
        {"pagination": {"page": 1}, "x": None}))))
    assert json.loads(q["pagination"]) == {"page": 1} and "x" not in q
    assert studi("Aktif") and studi("Kampus Merdeka")
    assert not studi("Keluar") and not studi("Cuti") and not studi("Lulus")
    assert not studi("Non-Aktif                           ")
    assert not studi("Mengajukan pengunduran diri") and not studi("tidak aktif")
    assert anomali({"nm_stat_mhs": "Aktif", "ips": 0, "sks_smt": 0,
                    "biaya_smt": "0.00"}) == ["IPS/SKS 0", "biaya kosong"]
    assert anomali({"nm_stat_mhs": "Lulus", "ips": 0, "sks_smt": 0,
                    "biaya_smt": "4000000"}) == []
    print("check ok: axios0 + anomali AKM")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--nim", default=None, help="cek satu NIM semester aktif")
    run(nim=ap.parse_args().nim)
