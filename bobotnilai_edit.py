#!/usr/bin/env python3
"""Baca + edit Bobot Nilai (band huruf/indeks per prodi) via API.
Pola: POLA_SCRIPT.md + kelaskuliah_edit.py.
Tanpa argumen -> baca semua band, simpan bobotnilai.csv + ringkasan
(tumpang-tindih rentang per prodi ikut dicek, penting untuk konversi).
Dengan CSV -> edit per baris (dry-run default, --go untuk tulis):
  prodi,nilai_huruf,field,nilai
  prodi = nm_lemb persis (mis. S1 Teknik Informatika)
  field = bobot_nilai_min | bobot_nilai_maks | nilai_indeks |
          nilai_huruf | tgl_mulai_efektif | tgl_akhir_efektif
PUT /ws/bobotnilai/edit/<id> dengan objek form penuh (pola form:
tanggal kirim YYYY-MM-DD seperti browser). Verifikasi baca-ulang per baris.
Pakai: python3 bobotnilai_edit.py [--prodi ...] [data.csv] [--go]
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse
from decimal import Decimal, InvalidOperation

BASE = os.environ.get("FEEDER_BASE", "http://localhost:8100/ws")
FIELDS = {"bobot_nilai_min", "bobot_nilai_maks", "nilai_indeks",
          "nilai_huruf", "tgl_mulai_efektif", "tgl_akhir_efektif"}

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

def prodi_list():
    d = api("GET", "/bobotnilai/filter")
    rows = (d.get("list_prodi") or [[]])[0]
    return {r["nm_lemb"]: r for r in rows}

def fetch_bobot():
    rows, page = [], 1
    while True:
        data, _ = api("GET", "/bobotnilai/index", params={
            "pagination": {"sortBy": "s.id_sms,m.nilai_huruf", "descending": False,
                           "page": page, "rowsPerPage": 200, "rowsNumber": 0},
            "filter": "", "columnSearch": ["s.nm_lemb"],
            "setFilter": {"prodi": []}, "extendFilter": {"filter_sync": "semua"},
            "sortList": None})
        if not data:
            break
        rows += data
        if len(data) < 200:
            break
        page += 1
    return rows

def dec(s):
    return Decimal(str(s).strip())

def cek_tumpang(rows):
    """Kembalikan daftar peringatan tumpang-tindih/duplikat huruf per prodi."""
    out, grup = [], {}
    for r in rows:
        grup.setdefault(r.get("nm_lemb", ""), []).append(r)
    for prodi, rs in sorted(grup.items()):
        huruf = [r.get("nilai_huruf", "") for r in rs]
        for h in set(huruf):
            if huruf.count(h) > 1:
                out.append(f"{prodi}: huruf {h!r} dobel")
        try:
            urut = sorted(rs, key=lambda r: dec(r.get("bobot_nilai_min") or 0))
        except InvalidOperation:
            out.append(f"{prodi}: ada min/maks bukan angka")
            continue
        for a, b in zip(urut, urut[1:]):
            try:
                if dec(a.get("bobot_nilai_maks") or 0) > dec(b.get("bobot_nilai_min") or 0):
                    out.append(f"{prodi}: {a.get('nilai_huruf')} tumpang tindih {b.get('nilai_huruf')}")
            except InvalidOperation:
                out.append(f"{prodi}: ada min/maks bukan angka")
                break
    return out

def baca(prodi=None):
    rows = fetch_bobot()
    if prodi:
        rows = [r for r in rows if prodi.lower() in (r.get("nm_lemb") or "").lower()]
    with open("bobotnilai.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["prodi", "nilai_huruf", "bobot_nilai_min",
                                          "bobot_nilai_maks", "nilai_indeks",
                                          "tgl_mulai_efektif", "tgl_akhir_efektif", "id"])
        w.writeheader()
        for r in rows:
            w.writerow({"prodi": r.get("nm_lemb", ""), "nilai_huruf": r.get("nilai_huruf", ""),
                        "bobot_nilai_min": r.get("bobot_nilai_min", ""),
                        "bobot_nilai_maks": r.get("bobot_nilai_maks", ""),
                        "nilai_indeks": r.get("nilai_indeks", ""),
                        "tgl_mulai_efektif": str(r.get("tgl_mulai_efektif") or "").split("T")[0],
                        "tgl_akhir_efektif": str(r.get("tgl_akhir_efektif") or "").split("T")[0],
                        "id": r.get("id", "")})
    print(f"total={len(rows)} tersimpan: bobotnilai.csv")
    for w in cek_tumpang(rows):
        print("PERINGATAN:", w)
    if not cek_tumpang(rows):
        print("rentang: tidak ada tumpang-tindih/duplikat huruf")

def normal_banding(field, a, b):
    if field in ("bobot_nilai_min", "bobot_nilai_maks", "nilai_indeks"):
        try:
            return dec(a) == dec(b)
        except InvalidOperation:
            return False
    if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
        return str(a or "").split("T")[0] == str(b or "").split("T")[0]
    return (a or "").strip() == (b or "").strip()

def validasi_nilai(field, nilai):
    if field in ("bobot_nilai_min", "bobot_nilai_maks", "nilai_indeks"):
        try:
            dec(nilai)
        except InvalidOperation:
            return f"{field} harus angka, dapat {nilai!r}"
    if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
        p = str(nilai).split("T")[0].split("-")
        if len(p) != 3 or len(p[0]) != 4 or not all(x.isdigit() for x in p):
            return f"{field} harus tanggal YYYY-MM-DD, dapat {nilai!r}"
    if not str(nilai).strip():
        return f"{field} tidak boleh kosong"
    return None

def tulis(csv_path, go, delay=1.0):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    prodis = prodi_list()
    semua = fetch_bobot()
    ok, fail = 0, 0
    for i, r in enumerate(rows, 1):
        try:
            prodi, huruf, field, nilai = (r.get("prodi") or "").strip(), \
                (r.get("nilai_huruf") or "").strip(), (r.get("field") or "").strip(), \
                (r.get("nilai") or "").strip()
            if prodi not in prodis:
                raise ValueError(f"prodi {prodi!r} tak dikenal "
                                 f"({', '.join(sorted(prodis))})")
            if field not in FIELDS:
                raise ValueError(f"field {field!r} tak dikenal ({', '.join(sorted(FIELDS))})")
            err = validasi_nilai(field, nilai)
            if err:
                raise ValueError(err)
            hit = [b for b in semua if b.get("nm_lemb") == prodi
                   and (b.get("nilai_huruf") or "").strip() == huruf]
            if len(hit) != 1:
                raise ValueError(f"{prodi} huruf {huruf!r}: ketemu {len(hit)}, harus 1")
            cur = hit[0]
            if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
                nilai = nilai.split("T")[0]
            if normal_banding(field, cur.get(field), nilai):
                print(f"[{i}/{len(rows)}] {prodi} {huruf} {field} sudah {nilai}, lewati")
                ok += 1
                continue
            if not go:
                print(f"[dry {i}/{len(rows)}] {prodi} {huruf} {field}: "
                      f"{cur.get(field)} -> {nilai}")
                ok += 1
                continue
            form = api("GET", f"/bobotnilai/edit/{cur['id']}")
            if not isinstance(form, dict) or "id_sms" not in form:
                raise ValueError("form edit tak terbaca")
            form[field] = nilai
            if field in ("tgl_mulai_efektif", "tgl_akhir_efektif"):
                form[field] = str(form[field]).split("T")[0]
            res = api("PUT", f"/bobotnilai/edit/{cur['id']}", form)
            good = res.get("code") == 200
            back = api("GET", f"/bobotnilai/edit/{cur['id']}")
            back = back if isinstance(back, dict) else {}
            ver = normal_banding(field, back.get(field), nilai)
            print(f"[{i}/{len(rows)}] {prodi} {huruf} {field} -> "
                  f"{res.get('code')} verifikasi={ver}")
            ok, fail = (ok + 1, fail) if good and ver else (ok, fail + 1)
        except Exception as e:
            print(f"[{i}/{len(rows)}] {r.get('prodi')}-{r.get('nilai_huruf')} GAGAL: {str(e)[:200]}")
            fail += 1
        time.sleep(delay)  # ponytail: jeda global
    print(f"selesai: ok={ok} fail={fail}")

def check():
    assert normal_banding("bobot_nilai_min", "65.0100", "65.01")
    assert normal_banding("nilai_huruf", " A ", "A")
    assert normal_banding("tgl_mulai_efektif", "2020-08-22T00:00:00.000Z", "2020-08-22")
    assert validasi_nilai("bobot_nilai_min", "abc") is not None
    assert validasi_nilai("bobot_nilai_min", "80.01") is None
    assert validasi_nilai("tgl_mulai_efektif", "22-08-2020") is not None
    assert len(cek_tumpang([
        {"nm_lemb": "P", "nilai_huruf": "A", "bobot_nilai_min": "80.01", "bobot_nilai_maks": "100"},
        {"nm_lemb": "P", "nilai_huruf": "B", "bobot_nilai_min": "70.01", "bobot_nilai_maks": "85"}])) == 1
    assert cek_tumpang([
        {"nm_lemb": "P", "nilai_huruf": "A", "bobot_nilai_min": "80.01", "bobot_nilai_maks": "100"},
        {"nm_lemb": "P", "nilai_huruf": "B", "bobot_nilai_min": "70.01", "bobot_nilai_maks": "80"}]) == []
    print("check ok: banding, validasi, deteksi tumpang-tindih")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", nargs="?")
    ap.add_argument("--prodi", default=None)
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    if not a.csv_path:
        baca(a.prodi)
    else:
        tulis(a.csv_path, a.go)
