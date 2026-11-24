#!/usr/bin/env python3
"""Edit Daya Tampung (/dayatampung) via API.
Pola: POLA_SCRIPT.md + bobotnilai_edit.py.
Baca: dayatampung_check.py (atau script ini tanpa argumen = tampilkan semua
baris semester aktif).
Tulis CSV (dry-run default, --go untuk tulis):
  prodi,semester,field,nilai
  prodi = nm_lemb persis (mis. S1 Teknik Informatika)
  semester = id_smt (mis. 20252)
  field = target_mhs_baru | calon_ikut_seleksi | calon_lulus_seleksi |
          daftar_sbg_mhs | pst_undur_diri | jml_mgu_kul |
          tgl_awal_kul | tgl_akhir_kul
PUT /ws/dayatampung/edit?id_smt=..&id_sms=.. dengan objek form penuh
(GET /dayatampung/edit?id_sms=..&id_smt=.. dulu). Verifikasi baca-ulang.
Pakai: python3 dayatampung_edit.py [data.csv] [--go]
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse
from datetime import date
from decimal import Decimal, InvalidOperation

BASE = os.environ.get("FEEDER_BASE", "http://localhost:8100/ws")
ANGKA = {"target_mhs_baru", "calon_ikut_seleksi", "calon_lulus_seleksi",
         "daftar_sbg_mhs", "pst_undur_diri", "jml_mgu_kul"}
TGL = {"tgl_awal_kul", "tgl_akhir_kul"}
FIELDS = ANGKA | TGL

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

def api(m, path, payload=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "authorization": "Bearer " + TOKEN},
        method=m)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")

def check_token():
    try:
        return "list_status" in json.dumps(api("GET", "/mhslulus/filter"))
    except Exception:
        return False

def ref_maps():
    d = api("GET", "/dayatampung/filter")
    prodi = {r["nm_lemb"]: r for r in d["list_prodi"][0]}
    sem = {s["id_smt"]: s["nm_smt"] for s in d["list_semester"][0]}
    return prodi, sem

def validasi_nilai(field, nilai):
    if field in ANGKA:
        s = str(nilai).strip().replace(",", "")
        if not s.isdigit():
            return f"{field} harus angka bulat, dapat {nilai!r}"
    if field in TGL:
        try:
            date.fromisoformat(str(nilai).split("T")[0])
        except ValueError:
            return f"{field} harus tanggal YYYY-MM-DD valid, dapat {nilai!r}"
    if not str(nilai).strip():
        return f"{field} tidak boleh kosong"
    return None

def normal_banding(field, a, b):
    if field in ANGKA:
        try:
            return Decimal(str(a).strip().replace(",", "")) == \
                Decimal(str(b).strip().replace(",", ""))
        except InvalidOperation:
            return False
    if field in TGL:
        return str(a or "").split("T")[0] == str(b or "").split("T")[0]
    return (a or "").strip() == (b or "").strip()

def tulis(csv_path, go, delay=1.0):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    prodi_map, sem_map = ref_maps()
    ok, fail = 0, 0
    for i, r in enumerate(rows, 1):
        try:
            prodi, smt = (r.get("prodi") or "").strip(), (r.get("semester") or "").strip()
            field, nilai = (r.get("field") or "").strip(), (r.get("nilai") or "").strip()
            if prodi not in prodi_map:
                raise ValueError(f"prodi {prodi!r} tak dikenal "
                                 f"({', '.join(sorted(prodi_map))})")
            if smt not in sem_map:
                raise ValueError(f"semester {smt!r} tak ada di filter")
            if field not in FIELDS:
                raise ValueError(f"field {field!r} tak dikenal ({', '.join(sorted(FIELDS))})")
            err = validasi_nilai(field, nilai)
            if err:
                raise ValueError(err)
            id_sms = prodi_map[prodi]["id_sms"]
            form = api("GET", f"/dayatampung/edit?id_sms={id_sms}&id_smt={smt}")
            cur = form.get("result") if isinstance(form, dict) else None
            if not isinstance(cur, dict) or "target_mhs_baru" not in cur:
                raise ValueError("form edit tak terbaca")
            if field in TGL:
                nilai = nilai.split("T")[0]
            if normal_banding(field, cur.get(field), nilai):
                print(f"[{i}/{len(rows)}] {prodi} {smt} {field} sudah {nilai}, lewati")
                ok += 1
                continue
            if not go:
                print(f"[dry {i}/{len(rows)}] {prodi} {smt} {field}: "
                      f"{cur.get(field)} -> {nilai}")
                ok += 1
                continue
            cur[field] = nilai
            res = api("PUT", f"/dayatampung/edit?id_smt={smt}&id_sms={id_sms}", cur)
            good = (res.get("code") if isinstance(res, dict) else None) == 200
            back = api("GET", f"/dayatampung/edit?id_sms={id_sms}&id_smt={smt}")
            back = back.get("result") if isinstance(back, dict) else {}
            ver = normal_banding(field, (back or {}).get(field), nilai)
            print(f"[{i}/{len(rows)}] {prodi} {smt} {field} -> "
                  f"{(res.get('code') if isinstance(res, dict) else None)} verifikasi={ver}")
            ok, fail = (ok + 1, fail) if good and ver else (ok, fail + 1)
        except Exception as e:
            print(f"[{i}/{len(rows)}] {r.get('prodi')}-{r.get('semester')} GAGAL: {str(e)[:200]}")
            fail += 1
        time.sleep(delay)  # ponytail: jeda global
    print(f"selesai: ok={ok} fail={fail}")

def check():
    assert validasi_nilai("target_mhs_baru", "abc") is not None
    assert validasi_nilai("target_mhs_baru", "120") is None
    assert validasi_nilai("tgl_awal_kul", "01-03-2026") is not None
    assert validasi_nilai("tgl_awal_kul", "2026-02-31") is not None
    assert validasi_nilai("tgl_awal_kul", "2026-03-01") is None
    assert validasi_nilai("jml_mgu_kul", "") is not None
    assert normal_banding("target_mhs_baru", "6", "6")
    assert normal_banding("tgl_awal_kul", "2026-03-01T00:00:00.000Z", "2026-03-01")
    assert not normal_banding("target_mhs_baru", "6", "7")
    print("check ok: validasi + banding daya tampung")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", nargs="?")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    if not a.csv_path:
        import dayatampung_check as dc
        dc.run()
    else:
        tulis(a.csv_path, a.go)
