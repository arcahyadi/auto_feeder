#!/usr/bin/env python3
"""Perbaiki MASTER rencana evaluasi MK (bukan kelas), paksa 15/35/10/10/15/15.

Berbeda dari komponen_evaluasi_fix.py (yang mengurus KELAS + nilai komponen),
script ini HANYA menyentuh master MK di `/matakuliah/editrencanaeval/{id_mk}`.

Berdasarkan KOMPONEN_EVALUASI.md. Default DRY-RUN; `--go` untuk menulis.
Default hanya DB **sandbox**; DB **pddikti** hanya bila `--pddikti` +
`--confirm TULIS-PRODUKSI` (keduanya).

PENGAMAN KERAS (baris master TIDAK BISA DIHAPUS lewat API):
- bentuk awal master WAJIB **0 baris** atau **tepat 6 slot** tanpa baris asing /
  slot duplikat; bentuk lain -> perlu-manual, TIDAK menulis apa pun;
- master kosong -> `POST /matakuliah/addrencanaeval` 6 baris sekaligus
  (`tambah:"oke"`, total 100) — server menambah persis yang dikirim;
- master 6 slot -> `PUT /matakuliah/editrencanaeval/{id_re_mk}` (array semua
  baris) untuk set bobot; PUT tidak menambah baris; PUT bisa parsial walau
  balas 200 -> diulang sampai benar atau perlu-manual;
- verifikasi baca-ulang: hasil WAJIB tepat 6 baris & bobot = target.

Pakai:
  python3 master_evaluasi_fix.py --mk SF24220              # dry-run
  python3 master_evaluasi_fix.py --mk SF24220 --go         # tulis (sandbox)
  python3 master_evaluasi_fix.py --prefix "SF23,SF24,DF24" # batch (dry-run)
  python3 master_evaluasi_fix.py --prefix "SF23,SF24,DF24" --go
  python3 master_evaluasi_fix.py check                     # self-check offline
"""
import argparse
import json
import os
import sys
import time

import komponen_evaluasi_check as chk
import komponen_evaluasi_fix as base
from feeder_client import FeederError

EXPECTED_FIELDS = base.EXPECTED_FIELDS
DEFAULT_TARGET = base.DEFAULT_TARGET
FIELD_ROW = base.FIELD_ROW
PDDIKTI_CONFIRM = base.PDDIKTI_CONFIRM
MAX_TRY = 4
# Prefix MK target aturan umum; di luar ini hanya diberi peringatan (tak
# diblokir) supaya MK di luar target tetap bisa diproses.
TARGET_PREFIXES = ("SF23", "SF24", "DF24")


# ---------------------------------------------------------------------------
# master


def baca_master(client, id_mk):
    """master_mk dengan guard: None bila respons bukan list (jangan crash)."""
    m = base.master_mk(client, id_mk)
    return m if isinstance(m, list) else None


def fmt_master(master):
    return [(x.get("id_jns_eval"), (x.get("nama") or "").strip(),
             float(x.get("bobot") or 0)) for x in master]


def bobot_per_slot(rows):
    """{field_slot: bobot} hanya baris yang punya slot UI."""
    return {chk.maps_ui(a): float(a.get("bobot") or 0)
            for a in rows if chk.maps_ui(a) is not None}


def master_asing(master):
    return [x for x in master if chk.maps_ui(x) is None]


def slot_master(master):
    return {chk.maps_ui(a): a for a in master if chk.maps_ui(a) is not None}


def master_bentuk_aman(master):
    """(ok, alasan). Master hanya aman disentuh bila 0 baris atau tepat 6 slot."""
    asing = master_asing(master)
    if asing:
        return False, f"ada {len(asing)} baris asing"
    slot = slot_master(master)
    if len(master) != len(slot):
        return False, f"ada slot duplikat ({len(master)} baris, {len(slot)} slot)"
    if len(master) == 0:
        return True, "kosong"
    if len(master) == 6 and set(slot) == set(EXPECTED_FIELDS):
        return True, "6 slot"
    return False, f"{len(master)} baris tak sesuai (harus 0 atau tepat 6)"


def master_benar(master):
    """True bila master tepat 6 slot, total 100, bobot = DEFAULT_TARGET."""
    if master_asing(master) or len(master) != 6:
        return False
    got = bobot_per_slot(master)
    if set(got) != set(EXPECTED_FIELDS) or abs(sum(got.values()) - 100) > 0.001:
        return False
    return all(abs(got[f] - DEFAULT_TARGET[f]) < 0.001 for f in EXPECTED_FIELDS)


def _baris_master(id_mk, field, urutan, bobot):
    """Baris baru master persis pola browser (wajib `tambah:"oke"`)."""
    jns, nama, nm = FIELD_ROW[field]
    return {"urutan": urutan, "id_jns_eval": jns, "id_mk": id_mk, "nama": nama,
            "deskripsi": "", "deskripsi_en": "", "bobot": str(bobot),
            "nm_jns_eval": nm, "tambah": "oke"}


def backup_master(master, kode, id_mk):
    """Simpan master sebelum tulis ke responses/ (read-only, aman)."""
    os.makedirs("responses", exist_ok=True)
    fn = f"responses/master_{kode}_{id_mk[:8]}_sebelum.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=1)
    return fn


def rapikan_master(client, id_mk, kode="", go=False, log=print):
    """Paksa master MK = DEFAULT_TARGET. Balikkan (status, pesan)."""
    master = baca_master(client, id_mk)
    if master is None:
        return "perlu-manual", "respons master bukan list (endpoint aneh)"
    aman, alasan = master_bentuk_aman(master)
    if not aman:
        log(f"     ! master TIDAK berbentuk aman: {alasan} "
            f"(baris tak bisa dihapus -> stop, tidak menulis)")
        return "perlu-manual", f"master tak berbentuk aman: {alasan}"

    if master_benar(master):
        return "ok", "master sudah 15/35/10/10/15/15, dilewati"

    if not go:
        return "dry-run", f"akan set master -> {DEFAULT_TARGET}"
    # backup di SEMUA jalur tulis (juga jalur PUT 6-slot, bukan hanya kosong)
    fn = backup_master(master, kode, id_mk)
    log(f"     backup -> {fn}")

    if len(master) == 0:
        payload_add = [_baris_master(id_mk, f, i + 1, DEFAULT_TARGET[f])
                       for i, f in enumerate(EXPECTED_FIELDS)]
        try:
            client.post("/matakuliah/addrencanaeval", payload=payload_add)
        except FeederError as e:
            return "perlu-manual", "POST master gagal: " + e.message
        time.sleep(0.5)
        master = baca_master(client, id_mk) or []
        if len(master) != 6 or master_asing(master) or \
                set(bobot_per_slot(master)) != set(EXPECTED_FIELDS):
            return "perlu-manual", f"master tak 6 slot standar setelah add: {fmt_master(master)}"

    payload = []
    for x in master:
        y = dict(x)
        f = chk.maps_ui(y)
        if f not in DEFAULT_TARGET:
            return "perlu-manual", f"baris master tanpa slot dikenal: {fmt_master([x])}"
        y["bobot"] = DEFAULT_TARGET[f]
        payload.append(y)
    if len(master) != 6 or len(bobot_per_slot(payload)) != len(EXPECTED_FIELDS):
        return "perlu-manual", f"master tak punya tepat 6 slot: {fmt_master(payload)}"
    if abs(sum(float(x["bobot"]) for x in payload) - 100) > 0.001:
        return "perlu-manual", "total master tak bisa pas 100"
    if not any(x.get("id_re_mk") for x in master):
        return "perlu-manual", "master tanpa id_re_mk"
    # PUT bisa parsial walau balas 200 -> ulangi sampai benar (jeda naik)
    after = []
    for i in range(MAX_TRY):
        try:
            client.put(f"/matakuliah/editrencanaeval/{master[0]['id_re_mk']}", payload=payload)
        except FeederError as e:
            return "perlu-manual", "PUT master gagal: " + e.message
        time.sleep(0.4 * (i + 1))
        after = baca_master(client, id_mk) or []
        if len(after) == 6 and master_benar(after):
            return "ok", "master = 15/35/10/10/15/15"
    return "perlu-manual", f"master tak sesuai setelah PUT: {fmt_master(after)}"


# ---------------------------------------------------------------------------
# daftar MK


def semua_kelas(client, ta, nm_smt):
    """Semua kelas TA aktif (tanpa filter kode), via paginasi."""
    semua, page = [], 1
    while True:
        data, n = client.get("/kelas/index", params={
            "pagination": {"sortBy": "id_sms", "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "",
            "columnSearch": ["k.nm_kls", "m.kode_mk", "m.nm_mk", "pt.nm_sdm"],
            "setFilter": {"semester": [{"id_smt": ta, "nm_smt": nm_smt}], "prodi": []},
            "extendFilter": {"filter_sync": "semua"}, "sortList": None})
        if not data:
            break
        semua += data
        if len(data) < 100:
            break
        page += 1
    return semua


def mk_dari_prefix(rows, prefix):
    pre = tuple(p.strip().upper() for p in prefix if p.strip())
    kodes = sorted({(r.get("kode_mk") or "").upper() for r in rows})
    return [k for k in kodes if k.startswith(pre)]


def _tally(status):
    return {"ok": 1 if status == "ok" else 0,
            "dry-run": 1 if status == "dry-run" else 0,
            "perlu-manual": 1 if status == "perlu-manual" else 0}


def run_mk(client, kode, ta, nm_smt, go=False):
    rows = base.fetch_kelas(client, ta, nm_smt, kode)
    if not rows:
        print(f"\n=== {kode}: tak ada kelas di TA aktif -> dilewati")
        return _tally("skip")
    # petakan kelas -> id_mk (tiap id_mk = satu master MK)
    per_id_mk = {}
    for r in rows:
        per_id_mk.setdefault(base.id_mk_kelas(client, r["id_kls"]), []).append(r)
    print(f"\n=== {kode} | kelas {len(rows)} | id_mk {len(per_id_mk)}")
    ringkas = {"ok": 0, "dry-run": 0, "perlu-manual": 0}
    for id_mk, rs in per_id_mk.items():
        kls = ",".join(r.get("nm_kls") or "?" for r in rs)
        if not id_mk:
            print(f"  ! kelas {kls}: id_mk tak ditemukan -> perlu-manual")
            ringkas["perlu-manual"] += 1
            continue
        print(f"\n  -- id_mk {id_mk[:8]} (kelas {kls})")
        print(f"     master sebelum: {fmt_master(baca_master(client, id_mk) or [])}")
        st, msg = rapikan_master(client, id_mk, kode, go=go, log=print)
        print(f"     master -> {st}: {msg}")
        ringkas[st] = ringkas.get(st, 0) + 1
        if st == "perlu-manual":
            return ringkas  # stop MK ini
    return ringkas


def _catat_prefix(kode):
    """Ingatkan (tanpa memblokir) bila kode di luar prefix target aturan umum."""
    if not any(kode.upper().startswith(p) for p in TARGET_PREFIXES):
        print(f"  ! catatan: {kode} di luar prefix target {TARGET_PREFIXES} "
              f"(tetap diproses)")


def run(kode, go=False, pddikti=False, confirm=None):
    client = base.client_gated(pddikti, confirm)
    _catat_prefix(kode)
    ta = base.active_ta(client)
    nm = base.semesters(client)[ta]["nm_smt"]
    r = run_mk(client, kode, ta, nm, go)
    print(f"\nringkasan {kode}: ok {r['ok']} | dry-run {r['dry-run']} | "
          f"perlu-manual {r['perlu-manual']}")
    return 1 if r["perlu-manual"] else 0


def run_prefix(prefix, go=False, pddikti=False, confirm=None):
    client = base.client_gated(pddikti, confirm)
    ta = base.active_ta(client)
    nm = base.semesters(client)[ta]["nm_smt"]
    kodes = mk_dari_prefix(semua_kelas(client, ta, nm), prefix)
    print(f"prefix {prefix} -> {len(kodes)} MK: {', '.join(kodes)}")
    total = {"ok": 0, "dry-run": 0, "perlu-manual": 0}
    for k in kodes:
        r = run_mk(client, k, ta, nm, go)
        for key in total:
            total[key] += r.get(key, 0)
        if r.get("perlu-manual"):
            print(f"\nSTOP BATCH: {k} perlu-manual.")
            break
    print(f"\nringkasan batch: ok {total['ok']} | dry-run {total['dry-run']} | "
          f"perlu-manual {total['perlu-manual']}")
    return 1 if total["perlu-manual"] else 0


def check():
    """Self-check offline: gate DB + bentuk aman master + baris baru + prefix."""
    assert base.gate_db("sandbox") is None
    assert base.gate_db("sandbox", pddikti=True) is None
    assert base.gate_db("pddikti") is not None
    assert base.gate_db("pddikti", pddikti=True) is not None
    assert base.gate_db("pddikti", pddikti=True, confirm="salah") is not None
    assert base.gate_db("pddikti", pddikti=True, confirm=PDDIKTI_CONFIRM) is None
    assert base.gate_db("lain") is not None

    m6 = [{"id_jns_eval": 2, "nama": "", "bobot": 15},
          {"id_jns_eval": 3, "nama": "", "bobot": 35},
          {"id_jns_eval": 4, "nama": "TGS", "bobot": 10},
          {"id_jns_eval": 4, "nama": "QIZ", "bobot": 10},
          {"id_jns_eval": 4, "nama": "UTS", "bobot": 15},
          {"id_jns_eval": 4, "nama": "UAS", "bobot": 15}]
    assert master_bentuk_aman([]) == (True, "kosong")
    assert master_bentuk_aman(m6)[0] is True
    assert master_bentuk_aman(m6[:4])[0] is False          # parsial
    assert master_bentuk_aman(m6 + m6[:1])[0] is False      # duplikat
    assert master_bentuk_aman(m6 + [{"id_jns_eval": 4, "nama": "X", "bobot": 0}])[0] is False
    assert master_benar(m6) is True
    assert master_benar(m6[:4]) is False
    assert master_benar([{**x, "bobot": 20} for x in m6]) is False  # total 120
    assert bobot_per_slot(m6) == DEFAULT_TARGET

    bm = _baris_master("idmk", "nilai_tugas", 3, 10)
    assert bm["tambah"] == "oke" and bm["bobot"] == "10"
    assert bm["id_jns_eval"] == 4 and bm["nama"] == "TGS" and bm["urutan"] == 3
    assert _baris_master("idmk", "nilai_aktivitas_partisipatif", 1, 15)["nama"] is None

    rows = [{"kode_mk": "SF201202"}, {"kode_mk": "SF23667"}, {"kode_mk": "SF24220"},
            {"kode_mk": "DF244219"}, {"kode_mk": "PA25101"}]
    assert mk_dari_prefix(rows, ["SF23", "SF24", "DF24"]) == ["DF244219", "SF23667", "SF24220"]
    assert _tally("ok") == {"ok": 1, "dry-run": 0, "perlu-manual": 0}
    assert _tally("skip") == {"ok": 0, "dry-run": 0, "perlu-manual": 0}
    print("check ok: gate DB + bentuk aman master + baris baru + prefix")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--mk", default=None, help="kode matakuliah, mis. SF24220")
    ap.add_argument("--prefix", default=None,
                    help="daftar prefix MK, mis. 'SF23,SF24,DF24' (batch)")
    ap.add_argument("--go", action="store_true", help="tulis (default dry-run)")
    ap.add_argument("--pddikti", action="store_true",
                    help="izinkan DB produksi (wajib + --confirm)")
    ap.add_argument("--confirm", default=None,
                    help=f"konfirmasi produksi: {PDDIKTI_CONFIRM}")
    a = ap.parse_args()
    if a.prefix:
        prefix = [p for p in a.prefix.split(",") if p.strip()]
        raise SystemExit(run_prefix(prefix, a.go, a.pddikti, a.confirm))
    if not a.mk:
        ap.error("butuh --mk ATAU --prefix")
    raise SystemExit(run(a.mk, a.go, a.pddikti, a.confirm))
