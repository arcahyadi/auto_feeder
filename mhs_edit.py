#!/usr/bin/env python3
"""Edit biodata mahasiswa via API (satu-per-satu). Pola: POLA_SCRIPT.md.
PUT /ws/mahasiswa/edit dengan objek detail lengkap (hasil GET detail + ubah
satu field). Field referensi (agama/wilayah/transportasi/tinggal) diisi ID,
objek nested-nya dirakit dari /mahasiswa/select/edit.
CSV: nim,field,nilai  (satu perubahan per baris)
Pakai: python3 mhs_edit.py data.csv [--go]   (default dry-run)
Cari ID referensi: python3 mhs_check.py ref wilayah bandung
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse
from datetime import date

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

TEXT_FIELDS = {"nm_pd", "tmpt_lahir", "tgl_lahir", "nik", "nisn", "npwp", "email",
               "no_hp", "no_tel_rmh", "jln", "nm_dsn", "rt", "rw", "ds_kel",
               "kode_pos", "nm_ibu_kandung", "nm_ayah", "nm_wali", "nik_ayah",
               "nik_ibu", "tgl_lahir_ayah", "tgl_lahir_ibu", "tgl_lahir_wali"}
REF_FIELDS = {
    "id_agama": ("agama", "agama", "id_agama"),
    "id_wil": ("wilayah", "wilayah", "id_wil"),
    "id_alat_transport": ("alat_transportasi", "alat_transportasi", "id_alat_transport"),
    "id_jns_tinggal": ("jenis_tinggal", "jenis_tinggal", "id_jns_tinggal"),
    "id_jenjang_pendidikan_ayah": ("jenjang_pendidikan", "jenjang_pendidikan_ayah", "id_jenj_didik"),
    "id_jenjang_pendidikan_ibu": ("jenjang_pendidikan", "jenjang_pendidikan_ibu", "id_jenj_didik"),
    "id_jenjang_pendidikan_wali": ("jenjang_pendidikan", "jenjang_pendidikan_wali", "id_jenj_didik"),
    "id_pekerjaan_ayah": ("pekerjaan", "pekerjaan_ayah", "id_pekerjaan"),
    "id_pekerjaan_ibu": ("pekerjaan", "pekerjaan_ibu", "id_pekerjaan"),
    "id_pekerjaan_wali": ("pekerjaan", "pekerjaan_wali", "id_pekerjaan"),
    "id_penghasilan_ayah": ("penghasilan", "penghasilan_ayah", "id_penghasilan"),
    "id_penghasilan_ibu": ("penghasilan", "penghasilan_ibu", "id_penghasilan"),
    "id_penghasilan_wali": ("penghasilan", "penghasilan_wali", "id_penghasilan"),
}
ENUM_FIELDS = {"jk": ("Laki-laki", "Perempuan"), "a_terima_kps": ("Ya", "Tidak")}
ALL_FIELDS = set(TEXT_FIELDS) | set(REF_FIELDS) | set(ENUM_FIELDS)
NIK_FIELDS = {"nik", "nik_ayah", "nik_ibu"}
TGL_FIELDS = {"tgl_lahir", "tgl_lahir_ayah", "tgl_lahir_ibu", "tgl_lahir_wali"}

def normal_nilai(field, nilai):
    nilai = (nilai or "").strip()
    if field == "jk":
        peta = {"l": "Laki-laki", "laki-laki": "Laki-laki", "p": "Perempuan",
                "perempuan": "Perempuan"}
        if nilai.lower() not in peta:
            raise ValueError("jk harus L/P")
        return peta[nilai.lower()]
    if field == "a_terima_kps":
        peta = {"ya": "Ya", "y": "Ya", "1": "Ya", "tidak": "Tidak", "t": "Tidak", "0": "Tidak"}
        if nilai.lower() not in peta:
            raise ValueError("a_terima_kps harus Ya/Tidak")
        return peta[nilai.lower()]
    if not nilai:
        raise ValueError("nilai kosong")
    if field in NIK_FIELDS and not (nilai.isdigit() and len(nilai) == 16):
        raise ValueError(f"{field} harus 16 digit angka")
    if field in TGL_FIELDS:
        try:
            if date.fromisoformat(nilai).isoformat() != nilai:
                raise ValueError
        except ValueError:
            raise ValueError(f"{field} harus tanggal YYYY-MM-DD")
    return nilai

def cari_mhs(nim, cache):
    if "index" not in cache:
        rows, page = [], 1
        while True:
            d, _ = api("GET", "/mahasiswa/index", params={
                "pagination": {"sortBy": "P.nm_pd", "descending": False, "page": page,
                               "rowsPerPage": 100, "rowsNumber": 0},
                "filter": "", "columnSearch": ["P.nm_pd", "r.nipd", "P.jk", "nm_agama",
                                               "s.nm_lemb", "r.mulai_smt"],
                "setFilter": {"prodi": [], "angkatan": [], "status_aktif_mahasiswa": [],
                              "jenis_kelamin": [], "agama": [], "jenis_pendaftaran": []},
                "extendFilter": {}, "sortList": None})
            rows += d
            if len(d) < 100:
                break
            page += 1
        cache["index"] = {str(r.get("nipd")): r for r in rows}
    hit = cache["index"].get(nim)
    if not hit:
        raise ValueError(f"{nim} tak ketemu di /mahasiswa/index")
    return hit

def select_ref(kind, cache):
    if "select" not in cache:
        cache["select"] = api("GET", "/mahasiswa/select/edit")
    items = cache["select"].get(kind) or []
    return items[0] if items and isinstance(items[0], list) else items

def rakit_nilai(field, nilai, cache):
    """Kembalikan (nilai_field, {field_nested: objek}) untuk payload PUT."""
    nilai = normal_nilai(field, nilai)
    if field in REF_FIELDS:
        kind, nested, idkey = REF_FIELDS[field]
        hit = [x for x in select_ref(kind, cache)
               if str(x.get(idkey, "")).strip() == nilai]
        if len(hit) != 1:
            raise ValueError(f"{nilai} tak unik/tak ada di referensi {kind}")
        return nilai, {nested: hit[0]}
    return nilai, {}

def banding(detail, field, nilai, nested):
    if field in REF_FIELDS:
        obj = detail.get(nested) or {}
        return str(obj.get(REF_FIELDS[field][2], "")).strip() == nilai
    if field.startswith("tgl_"):
        return str(detail.get(field) or "")[:10] == nilai[:10]
    return str(detail.get(field) or "") == nilai

def set_field(nim, field, nilai, go, cache):
    if field not in ALL_FIELDS:
        raise ValueError(f"field {field!r} tidak diizinkan")
    hit = cari_mhs(nim, cache)
    id_pd = hit["id_pd"]
    if nim not in cache:
        cache[nim] = api("GET", "/mahasiswa/detail/" + id_pd)
    rec = cache[nim]
    nilai, nested = rakit_nilai(field, nilai, cache)
    lama = rec.get(field)
    if field in REF_FIELDS:
        lama = (rec.get(REF_FIELDS[field][1]) or {}).get(REF_FIELDS[field][2])
    if not go:
        return "dry", f"{nim} {field}: {lama!r} -> {nilai!r}"
    data = dict(rec)
    data[field] = nilai
    data.update(nested)
    if str(field).startswith("tgl_"):
        data[field] = nilai + "T00:00:00.000Z" if "T" not in nilai else nilai
    res = api("PUT", "/mahasiswa/edit", data)
    if res.get("code") != 200:
        return "fail", f"{nim} PUT -> {res.get('code')} {res.get('message')}"
    back = api("GET", "/mahasiswa/detail/" + id_pd)
    ver = banding(back, field, nilai, REF_FIELDS[field][1] if field in REF_FIELDS else None)
    cache[nim] = back
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
    log = open("bulk-mhs.log", "a", encoding="utf-8")
    cache, ringkas = {}, {}
    for i, r in enumerate(rows, 1):
        nim = (r.get("nim") or "").strip()
        field = (r.get("field") or "").strip()
        nilai = (r.get("nilai") or "").strip()
        try:
            st, msg = set_field(nim, field, nilai, go, cache)
        except Exception as e:
            st, msg = "fail", f"{nim} {field} GAGAL: {str(e)[:200]}"
        print(f"[{i}/{len(rows)}] {st}: {msg}")
        if go:
            log.write(f"[{i}/{len(rows)}] {st}: {msg}\n"); log.flush()
            time.sleep(delay)  # ponytail: jeda global
        ringkas[st] = ringkas.get(st, 0) + 1
    log.close()
    print("ringkas:", ringkas)
    if not go:
        print("dry-run selesai (tambah --go untuk tulis)")

def check():
    assert normal_nilai("jk", "L") == "Laki-laki"
    assert normal_nilai("jk", "perempuan") == "Perempuan"
    assert normal_nilai("a_terima_kps", "ya") == "Ya"
    assert normal_nilai("nik", " 3201010101010001 ") == "3201010101010001"
    assert normal_nilai("tgl_lahir", " 2000-01-02 ") == "2000-01-02"
    for bad in [("jk", "X"), ("a_terima_kps", "mungkin"), ("nik", ""),
                ("nik", "123"), ("nik_ayah", "352924470700000X"),
                ("tgl_lahir", "02-01-2000"), ("tgl_lahir_ibu", "2026-02-31")]:
        try:
            normal_nilai(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"harus ditolak: {bad}")
    assert "id_wil" in ALL_FIELDS and "csf" not in ALL_FIELDS
    print("check ok: allowlist + normalisasi nilai")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, go=a.go)
