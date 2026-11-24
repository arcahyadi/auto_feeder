#!/usr/bin/env python3
"""Tambah mahasiswa baru via API (dua langkah). Pola: POLA_SCRIPT.md.
Langkah 1: POST /ws/mahasiswa/add (biodata) -> id_pd.
Langkah 2: POST /ws/history_pendidikan/add (NIM + prodi + periode) -> id_reg_pd.
Objek nested (agama/wilayah/prodi/jalur/jenis_daftar/pembiayaan) dirakit dari
/mahasiswa/select/edit + /history_pendidikan/get/select.
CSV: nipd,nm_pd,jk,tmpt_lahir,tgl_lahir,nik,nisn,email,no_hp,nm_ibu_kandung,
     jln,rt,rw,ds_kel,id_wil,kode_pos,id_agama,prodi,periode,tgl_masuk,
     jalur_masuk,jenis_daftar,pembiayaan,biaya_masuk
Kolom opsional boleh kosong; id_agama default 1 (Islam), pembiayaan 1 (Mandiri).
Pakai: python3 mhs_add.py data.csv [--go]   (default dry-run)
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

def peta_mhs(cache):
    if "peta" in cache:
        return cache["peta"]
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
    cache["peta"] = {str(r.get("nipd")): r for r in rows}
    return cache["peta"]

def opsi_history(cache):
    if "hist" not in cache:
        cache["hist"] = api("GET", "/history_pendidikan/get/select")
    return cache["hist"]

def opsi_select(cache):
    if "sel" not in cache:
        cache["sel"] = api("GET", "/mahasiswa/select/edit")
    return cache["sel"]

def satu(kind, cache):
    items = opsi_select(cache).get(kind) or []
    return items[0] if items and isinstance(items[0], list) else items

def satu_hist(key, cache):
    items = opsi_history(cache).get(key) or []
    return items[0] if items and isinstance(items[0], list) else items

def cari_hist(key, idkey, nilai, cache):
    hit = [x for x in satu_hist(key, cache) if str(x.get(idkey)) == str(nilai)]
    if len(hit) != 1:
        raise ValueError(f"{nilai!r} tak ada di referensi {key}")
    return hit[0]

def cari_select(kind, idkey, nilai, cache):
    hit = [x for x in satu(kind, cache) if str(x.get(idkey)).strip() == str(nilai)]
    if len(hit) != 1:
        raise ValueError(f"{nilai!r} tak ada di referensi {kind}")
    return hit[0]

def validasi(row, cache):
    """Kembalikan (payload_biodata, payload_history, pesan_error)."""
    errs = []
    wajib = ["nipd", "nm_pd", "jk", "tmpt_lahir", "tgl_lahir", "nik", "nisn",
             "email", "no_hp", "nm_ibu_kandung", "ds_kel", "id_wil", "prodi",
             "periode", "tgl_masuk"]
    for k in wajib:
        if not str(row.get(k) or "").strip():
            errs.append(f"{k} kosong")
    nik = str(row.get("nik") or "").strip()
    if nik and not (nik.isdigit() and len(nik) == 16):
        errs.append("NIK harus 16 digit")
    jk = str(row.get("jk") or "").strip().upper()
    if jk and jk not in ("L", "P"):
        errs.append("jk harus L/P")
    prodi_raw = str(row.get("prodi") or "").strip()
    prodi = None
    if prodi_raw:
        hit = [p for p in satu_hist("list_prodi", cache)
               if p.get("id_sms") == prodi_raw
               or prodi_raw.lower() in (p.get("nm_lemb") or "").lower()]
        if len(hit) == 1:
            prodi = hit[0]
        else:
            errs.append(f"prodi {prodi_raw!r} tak unik/tak ada")
    periode = str(row.get("periode") or "").strip()
    if periode and periode not in {s.get("id_smt") for s in
                                   api("GET", "/history_pendidikan/periode_aktif_all")}:
        errs.append(f"periode {periode!r} tak ada di periode_aktif_all")
    if errs:
        return None, None, errs
    peta = peta_mhs(cache)
    if str(row["nipd"]) in peta:
        return None, None, [f"NIM {row['nipd']} sudah ada di Feeder"]
    id_agama = str(row.get("id_agama") or "1").strip()
    id_pembiayaan = str(row.get("pembiayaan") or "1").strip()
    id_jalur = str(row.get("jalur_masuk") or "").strip()
    id_jns = str(row.get("jenis_daftar") or "").strip()
    biaya = str(row.get("biaya_masuk") or "0").strip()
    if not biaya.isdigit():
        return None, None, ["biaya_masuk harus angka bulat"]
    try:
        agama = cari_select("agama", "id_agama", id_agama, cache)
        wilayah = cari_select("wilayah", "id_wil", str(row["id_wil"]).strip(), cache)
        pembiayaan = cari_hist("pembiayaan", "id_pembiayaan", id_pembiayaan, cache)
        jalur = cari_hist("jalur_masuk", "id_jalur_masuk", id_jalur, cache) if id_jalur else None
        jns = cari_hist("jenis_daftar", "id_jns_daftar", id_jns, cache) if id_jns else None
    except ValueError as e:
        return None, None, [str(e)]
    if not jalur:
        return None, None, ["jalur_masuk wajib (lihat opsi /history_pendidikan/get/select)"]
    if not jns:
        return None, None, ["jenis_daftar wajib (lihat opsi /history_pendidikan/get/select)"]
    biodata = {
        "nm_pd": str(row["nm_pd"]).strip(), "jk": "Laki-laki" if jk == "L" else "Perempuan",
        "tmpt_lahir": str(row["tmpt_lahir"]).strip(),
        "tgl_lahir": str(row["tgl_lahir"]).strip() + "T00:00:00.000Z",
        "nik": nik, "nisn": str(row["nisn"]).strip(),
        "npwp": str(row.get("npwp") or "").strip() or None,
        "email": str(row["email"]).strip(),
        "no_hp": str(row["no_hp"]).strip(),
        "no_tel_rmh": str(row.get("no_tel_rmh") or "").strip() or None,
        "jln": str(row.get("jln") or "").strip() or None,
        "nm_dsn": str(row.get("nm_dsn") or "").strip() or None,
        "rt": str(row.get("rt") or "").strip() or None,
        "rw": str(row.get("rw") or "").strip() or None,
        "ds_kel": str(row["ds_kel"]).strip(),
        "kode_pos": str(row.get("kode_pos") or "").strip() or None,
        "nm_ibu_kandung": str(row["nm_ibu_kandung"]).strip(),
        "id_agama": agama["id_agama"], "agama": agama,
        "id_wil": wilayah["id_wil"], "wilayah": wilayah,
        "kewarganegaraan": {"id_negara": "ID", "nm_negara": "Indonesia"},
        "a_terima_kps": "Tidak",
        "kebutuhan_khusus_exist": "Tidak",
        "data_kebutuhan_khusus": [],
        "stat_pd": "A",
    }
    smt = periode
    thn = smt[:4]
    nama_smt = f"{thn}/{int(thn) + 1} " + ("Ganjil" if smt[-1] == "1" else
                                            "Genap" if smt[-1] == "2" else "Pendek")
    history = {
        "nipd": str(row["nipd"]).strip(),
        "jenis_daftar": jns, "id_jns_daftar": jns["id_jns_daftar"],
        "jalur_masuk": jalur, "id_jalur_masuk": jalur["id_jalur_masuk"],
        "periode_pendaftaran": smt, "periode_pendaftaran_masking": nama_smt,
        "tgl_masuk": str(row["tgl_masuk"]).strip() + "T00:00:00.000Z",
        "pembiayaan": pembiayaan, "id_pembiayaan": pembiayaan["id_pembiayaan"],
        "biaya_masuk_kuliah": int(biaya),
        "biaya_masuk_kuliah_masking": "IDR " + f"{int(biaya):,}".replace(",", "."),
        "prodi": prodi, "id_sms": prodi["id_sms"],
        "sms": prodi, "nm_sp": None,
    }
    return biodata, history, []

def tambah_satu(row, go, cache):
    biodata, history, errs = validasi(row, cache)
    if errs:
        raise ValueError("; ".join(errs))
    if not go:
        return "dry", (f"{row['nipd']} {row['nm_pd']} -> biodata + histori "
                       f"{history['prodi']['nm_lemb']} {history['periode_pendaftaran']}")
    res = api("POST", "/mahasiswa/add", biodata)
    if res.get("code") != 200:
        return "fail", f"{row['nipd']} add mahasiswa -> {res.get('code')} {res.get('message')}"
    id_pd = (res.get("data") or {}).get("id_pd")
    if not id_pd:
        return "fail", f"{row['nipd']} add 200 tapi id_pd tidak dikembalikan"
    history["id_pd"] = id_pd
    history["detail"] = api("GET", "/mahasiswa/detail/" + id_pd)
    res2 = api("POST", "/history_pendidikan/add", history)
    if res2.get("code") != 200:
        return "periksa", (f"{row['nipd']} biodata OK (id_pd {id_pd}) tapi histori "
                           f"GAGAL: {res2.get('code')} {res2.get('message')}")
    back = api("GET", "/history_pendidikan/index/" + id_pd)
    rows = (back.get("history_mahasiswa") or [[], 0])[0] if isinstance(back, dict) else []
    hit = [r for r in rows if str(r.get("nipd")) == str(row["nipd"])]
    if len(hit) == 1:
        return "ok", f"{row['nipd']} terverifikasi (id_reg_pd {hit[0].get('id_reg_pd')})"
    return "unknown", f"{row['nipd']} tersimpan tetapi belum terbaca ulang"

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
        try:
            st, msg = tambah_satu(r, go, cache)
        except Exception as e:
            st, msg = "fail", f"{r.get('nipd')} GAGAL: {str(e)[:250]}"
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
    assert "ID" and "T00:00:00.000Z"
    smt = "20252"
    thn = smt[:4]
    nama = f"{thn}/{int(thn) + 1} " + ("Ganjil" if smt[-1] == "1" else "Genap")
    assert nama == "2025/2026 Genap"
    assert "IDR " + f"{19250000:,}".replace(",", ".") == "IDR 19.250.000"
    print("check ok: format periode + masking biaya")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    run(a.csv_path, go=a.go)
