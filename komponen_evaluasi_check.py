#!/usr/bin/env python3
"""Cek komponen evaluasi (rencana evaluasi) kelas TA aktif 100% via API (token).

Pola: POLA_SCRIPT.md (cek token -> TA aktif dinamis -> tarik index axios0).
Untuk satu kode matkul: daftar semua kelasnya di TA aktif, bandingkan struktur
komponen evaluasi kelas dengan struktur baku (dan master MK bila ada), tandai
anomali: total bobot != 100, komponen "asing" (tak punya slot UI, memblokir
tulis nilai), komponen hilang, duplikat nama, beda dari master.

Read-only. Bila ada anomali, perbaikan dilakukan terpisah (lihat
KOMPONEN_EVALUASI.md).

Pakai: python3 komponen_evaluasi_check.py --mk SF23667
       python3 komponen_evaluasi_check.py --mk SF23667 --semester 20252
"""
import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request

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


def semesters():
    return {s["id_smt"]: s for s in api_get("/kelas/filter")["list_semester"][0]}


def fetch_kelas(id_smt, nm_smt, kode):
    rows, page, total = [], 1, None
    while True:
        data, n = api_get("/kelas/index", {
            "pagination": {"sortBy": "id_sms", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": kode, "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": id_smt, "nm_smt": nm_smt}], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        total = total or n
        if not data:
            break
        rows += data
        if len(data) < 100:
            break
        page += 1
    return [r for r in rows if r.get("kode_mk", "").upper() == kode.upper()], total


def komponen_kelas(id_kls):
    return api_get("/kelas/editrencanaevaluasi/" + id_kls)


def id_mk_kelas(id_kls):
    try:
        return api_get("/nilai/detail/" + id_kls)["data"]["detail"]["id_mk"]
    except Exception:
        return None


def master_mk(id_mk):
    try:
        return api_get("/matakuliah/editrencanaeval/" + id_mk)
    except Exception:
        return []


# Slot komponen yang dikenali UI. UI memetakan jns2/jns3 tanpa melihat `nama`;
# jns4 baru dipetakan bila `nama` = TGS/QIZ/UTS/UAS.
FIELD_SLOT = {
    "nilai_aktivitas_partisipatif": (2, None),
    "nilai_hasil_proyek": (3, None),
    "nilai_tugas": (4, "TGS"),
    "nilai_quiz": (4, "QIZ"),
    "nilai_ujian_tengah_semester": (4, "UTS"),
    "nilai_ujian_akhir_semester": (4, "UAS"),
}
LABEL = {"nilai_aktivitas_partisipatif": "Aktivitas Partisipatif",
         "nilai_hasil_proyek": "Hasil Proyek", "nilai_tugas": "TGS",
         "nilai_quiz": "QIZ", "nilai_ujian_tengah_semester": "UTS",
         "nilai_ujian_akhir_semester": "UAS"}
JNS4_FIELD = {"TGS": "nilai_tugas", "QIZ": "nilai_quiz",
              "UTS": "nilai_ujian_tengah_semester", "UAS": "nilai_ujian_akhir_semester"}


def norm_nama(n):
    s = (n or "").strip()
    return s or None


def maps_ui(a):
    """Field nilai yang dipetakan UI untuk komponen ini, atau None (asing)."""
    jns, nama = a.get("id_jns_eval"), norm_nama(a.get("nama"))
    if jns == 2:
        return "nilai_aktivitas_partisipatif"
    if jns == 3:
        return "nilai_hasil_proyek"
    if jns == 4 and nama in JNS4_FIELD:
        return JNS4_FIELD[nama]
    return None


def komponen_anomali(rows, master=None):
    """Daftar anomali untuk satu kelas (list komponen + master opsional)."""
    out = []
    fields = [maps_ui(a) for a in rows]
    total = sum(float(a.get("bobot") or 0) for a in rows)
    if abs(total - 100) > 0.001:
        out.append(f"total bobot {total:g} != 100")
    asing = [a for a, f in zip(rows, fields) if f is None]
    if asing:
        lab = ", ".join(f"jns{a.get('id_jns_eval')}:{norm_nama(a.get('nama')) or '-'}"
                        for a in asing)
        out.append(f"komponen asing ({lab}) - memblokir tulis nilai")
    hilang = [f for f in FIELD_SLOT if f not in fields]
    if hilang:
        out.append("komponen hilang: " + ", ".join(LABEL[f] for f in hilang))
    dobel = sorted({f for f in fields if f and fields.count(f) > 1})
    if dobel:
        out.append("duplikat komponen: " + ", ".join(LABEL.get(f, f) for f in dobel))
    for a in rows:
        if a.get("id_jns_eval") in (2, 3) and norm_nama(a.get("nama")):
            out.append(f"jns{a.get('id_jns_eval')} nama seharusnya kosong "
                       f"(dapat {norm_nama(a.get('nama'))!r})")
    if master:
        cur = {maps_ui(a): float(a.get("bobot") or 0) for a in rows if maps_ui(a)}
        mst = {maps_ui(a): float(a.get("bobot") or 0) for a in master if maps_ui(a)}
        if mst and cur != mst:
            out.append("struktur/bobot beda dari master MK")
    return out


def run(kode, id_smt=None):
    if not TOKEN:
        sys.exit("GAGAL: FEEDER_TOKEN kosong. Isi .env dulu.")
    print("cek token...", end=" ", flush=True)
    if not check_token():
        sys.exit("GAGAL: token tidak valid/kadaluarsa. Update .env dulu.")
    print("valid ✓")
    ta = id_smt or active_ta()
    sem = semesters()
    if ta not in sem:
        sys.exit(f"GAGAL: {ta} tak ada di list semester.")
    rows, total = fetch_kelas(ta, sem[ta]["nm_smt"], kode)
    if not rows:
        sys.exit(f"GAGAL: kelas {kode} tak ditemukan di TA {ta}.")
    print(f"TA {ta} ({sem[ta]['nm_smt']}) | {kode} | kelas: {len(rows)}")

    out, bermasalah = [], 0
    for r in rows:
        komp = komponen_kelas(r["id_kls"])
        id_mk = id_mk_kelas(r["id_kls"])
        master = master_mk(id_mk) if id_mk else []
        anomali = komponen_anomali(komp, master)
        if anomali:
            bermasalah += 1
        print(f"\n  {r.get('nm_kls')} (id {r['id_kls'][:8]}) | {len(komp)} komponen"
              f" | master {'ada' if master else 'kosong'}")
        for a in sorted(komp, key=lambda x: x.get("urutan") or 0):
            tanda = "" if maps_ui(a) else "  <-- asing"
            print(f"     urut={a.get('urutan')} jns={a.get('id_jns_eval')} "
                  f"{a.get('nama') or '-':>16} bobot={a.get('bobot')}{tanda}")
        if anomali:
            for x in anomali:
                print(f"     ! {x}")
        else:
            print("     ok")
        for a in sorted(komp, key=lambda x: x.get("urutan") or 0):
            out.append({
                "kode_mk": r.get("kode_mk", ""), "nm_kls": r.get("nm_kls", ""),
                "id_kls": r.get("id_kls", ""), "urutan": a.get("urutan", ""),
                "id_jns_eval": a.get("id_jns_eval", ""), "nama": a.get("nama") or "",
                "bobot": a.get("bobot", ""),
                "anomali": "; ".join(anomali),
                "id_komp_eval": a.get("id_komp_eval", ""),
            })

    fname = f"komponen_evaluasi_{kode}_{ta}.csv"
    with open(fname, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["kode_mk", "nm_kls", "id_kls", "urutan",
                                          "id_jns_eval", "nama", "bobot", "anomali",
                                          "id_komp_eval"], lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print(f"\nringkasan {kode}: kelas {len(rows)} | bermasalah {bermasalah} | "
          f"komponen {len(out)}")
    print("tersimpan:", fname)
    return 1 if bermasalah else 0


def check():
    """Self-check offline: normalisasi slot + anomali."""
    ok = [{"id_jns_eval": 2, "nama": None, "bobot": 15},
          {"id_jns_eval": 3, "nama": "", "bobot": 35},
          {"id_jns_eval": 4, "nama": "TGS", "bobot": 10},
          {"id_jns_eval": 4, "nama": "QIZ", "bobot": 10},
          {"id_jns_eval": 4, "nama": "UTS", "bobot": 15},
          {"id_jns_eval": 4, "nama": "UAS", "bobot": 15}]
    assert komponen_anomali(ok) == []
    assert norm_nama("") is None and norm_nama(" TGS ") == "TGS"
    asing = ok + [{"id_jns_eval": 4, "nama": "Hasil Proyek", "bobot": 0}]
    a = komponen_anomali(asing)
    assert any("asing" in x for x in a), a
    kurang = [x for x in ok if x["id_jns_eval"] != 2]
    assert any("hilang" in x and "Aktivitas Partisipatif" in x for x in komponen_anomali(kurang))
    total_salah = [dict(x, bobot=0) for x in ok]
    assert any("total bobot 0" in x for x in komponen_anomali(total_salah))
    dobel = ok + [{"id_jns_eval": 4, "nama": "TGS", "bobot": 0}]
    assert any("duplikat" in x for x in komponen_anomali(dobel))
    assert any("beda dari master" in x for x in
               komponen_anomali(ok, [{"id_jns_eval": 3, "nama": "", "bobot": 100}]))
    print("check ok: komponen evaluasi slot + anomali")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--mk", required=True, help="kode matakuliah, mis. SF23667")
    ap.add_argument("--semester", default=None, help="id_smt (default: TA aktif)")
    a = ap.parse_args()
    raise SystemExit(run(a.mk, a.semester))
