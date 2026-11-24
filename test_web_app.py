#!/usr/bin/env python3
"""Test web Automation Feeder (stdlib unittest).

Operasi tulis diuji terhadap fake Feeder; tidak ada koneksi Feeder nyata.
Jalankan: python3 -B -m unittest test_web_app
"""
import io
import json
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import feeder_client
import akm_check
import dayatampung_check
import dayatampung_edit
import kelas_tm_fix
import konversi_isi
import mhs_add
import mhs_check
import mhs_edit
import web_app
import web_operations as ops
from feeder_client import FeederError, normalize_base


class FakeFeeder:
    """Klien Feeder palsu: rute GET/POST/PUT + catatan tulisan."""

    def __init__(self, routes=None, writes=None):
        self.routes = routes or {}
        self.writes = writes if writes is not None else []
        self.base = "http://fake/ws"
        self.token = "tok"

    def _route(self, method, path):
        for (m, p), v in self.routes.items():
            if m == method and path == p:
                return v
        raise FeederError("http", f"404 {method} {path}")

    def get(self, path, params=None):
        v = self._route("GET", path)
        return v(params) if callable(v) else v

    def post(self, path, payload=None, params=None):
        v = self._route("POST", path)
        self.writes.append(("POST", path, payload))
        return v(payload) if callable(v) else v

    def put(self, path, payload=None, params=None):
        v = self._route("PUT", path)
        self.writes.append(("PUT", path, payload))
        return v(payload) if callable(v) else v

    def delete(self, path, params=None):
        self.writes.append(("DELETE", path, None))
        return {"code": 200}


def ctx():
    return {
        "active_sem": {"id_smt": "20252", "nm_smt": "2025/2026 Genap"},
        "semesters": [{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}],
        "prodi": [{"id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika"}],
    }


def no_progress(done, total, *rest):
    pass


def never_cancel():
    return False


class TestNormalize(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(normalize_base("192.0.2.10:8100"),
                         "http://192.0.2.10:8100/ws")
        self.assertEqual(normalize_base("http://192.0.2.10:8100"),
                         "http://192.0.2.10:8100/ws")
        self.assertEqual(normalize_base("http://192.0.2.10:8100/ws"),
                         "http://192.0.2.10:8100/ws")
        self.assertEqual(normalize_base("https://feeder.web.ac.id"),
                         "https://feeder.web.ac.id/ws")
        self.assertEqual(normalize_base("https://feeder.web.ac.id/ws/"),
                         "https://feeder.web.ac.id/ws")

    def test_invalid(self):
        for bad in ("", "ftp://x", "http://user:pw@host", "http://",
                    "http://host:99999", "http://host/path?q=1"):
            with self.assertRaises(FeederError, msg=bad):
                normalize_base(bad)

    def test_axios0(self):
        pairs = urllib.parse.parse_qsl(urllib.parse.urlencode(
            feeder_client.axios0({"a": None, "b": ["x", {"k": 1}], "c": True,
                                  "d": {"p": 1}})))
        multi = {}
        for k, v in pairs:
            multi.setdefault(k, []).append(v)
        self.assertEqual(multi["b[]"], ["x", '{"k": 1}'])
        self.assertEqual(multi["c"], ["true"])
        self.assertNotIn("a", multi)
        self.assertEqual(json.loads(multi["d"][0]), {"p": 1})

    def test_invalid_port(self):
        with self.assertRaises(FeederError):
            normalize_base("http://localhost:99999")


class TestTmSksRules(unittest.TestCase):
    def test_cli_and_web_rules_stay_in_sync(self):
        for n in (1, 2, 3):
            self.assertEqual(ops.bagi_tm_sks(n, 2), kelas_tm_fix.bagi_tm_sks(n, 2))

    def test_rules(self):
        self.assertEqual(ops.bagi_tm_sks(1, 2), [(16, 16, "2.00")])
        self.assertEqual(ops.bagi_tm_sks(2, 2), [(8, 8, "1.00"), (8, 8, "1.00")])
        self.assertEqual(ops.bagi_tm_sks(3, 2),
                         [(5, 5, "0.67"), (5, 5, "0.67"), (6, 6, "0.66")])
        with self.assertRaises(ValueError):
            ops.bagi_tm_sks(4, 2)

    def test_csv_safe(self):
        self.assertEqual(ops.csv_safe("202500001"), "202500001")
        self.assertEqual(ops.csv_safe("=cmd"), "'=cmd")
        self.assertEqual(ops.csv_safe("+1"), "'+1")
        self.assertEqual(ops.csv_safe(None), "")


class TestAuditKelas(unittest.TestCase):
    def test_rows_and_anomaly(self):
        index = [[
            {"kode_mk": "DF1", "nm_mk": "A", "nm_kls": "A", "sks_mk": "2.00",
             "nm_lemb": "D3 Manajemen Informatika", "dosen": "X", "jmlmhs": 10, "id_kls": "k1",
             "last_update": "2026-01-01", "last_sync": "2026-01-02"},
            {"kode_mk": "DF2", "nm_mk": "B", "nm_kls": "B", "sks_mk": "2.00",
             "nm_lemb": "D3 Manajemen Informatika", "dosen": "", "jmlmhs": 0, "id_kls": "k2",
             "last_update": "2026-02-01", "last_sync": "2026-01-02"},
        ], 2]
        client = FakeFeeder({
            ("GET", "/kelas/index"): index,
            ("GET", "/kelas/jumlah_jmlmhs/k1"): 10,
            ("GET", "/kelas/jumlah_jmlmhs/k2"): 0,
        })
        res = ops.run_kelas(client, ctx(), {}, no_progress, never_cancel)
        self.assertEqual(res["summary"]["total"], 2)
        self.assertEqual(res["summary"]["tanpa_dosen"], 1)
        self.assertEqual(res["summary"]["belum_sync"], 1)
        self.assertEqual(res["summary"]["peserta_0"], 1)


class TestTmSksAudit(unittest.TestCase):
    def _client(self):
        index = [[{"kode_mk": "DF1", "nm_mk": "A", "nm_kls": "A", "sks_mk": "2.00",
                   "nm_lemb": "D3 Manajemen Informatika", "dosen": "X", "jmlmhs": 1, "id_kls": "k1",
                   "last_update": "2026-01-01", "last_sync": "2026-01-02"}], 1]
        detail = {"data": {
            "detail": {"sks_mk": "2.00"},
            "dosen": [
                {"id_ajar": "a1", "nm_sdm": "DOSEN A", "jml_tm_renc": "8",
                 "jml_tm_real": "8", "sks_subst_tot": "1.00"},
                {"id_ajar": "a2", "nm_sdm": "MA'RUF", "jml_tm_renc": "8",
                 "jml_tm_real": "8", "sks_subst_tot": "1.00"},
            ]}}
        dup = {"data": {
            "detail": {"sks_mk": "2.00"},
            "dosen": [
                {"id_ajar": "a1", "nm_sdm": "DOSEN A", "jml_tm_renc": "4",
                 "jml_tm_real": "4", "sks_subst_tot": "1.00"},
                {"id_ajar": "a2", "nm_sdm": "DOSEN A", "jml_tm_renc": "8",
                 "jml_tm_real": "8", "sks_subst_tot": "1.00"},
            ]}}
        return FakeFeeder({
            ("GET", "/kelas/index"): index,
            ("GET", "/kelas/detail/k1"): detail,
        }), FakeFeeder({
            ("GET", "/kelas/index"): index,
            ("GET", "/kelas/detail/k1"): dup,
        })

    def test_ok_class(self):
        client, _ = self._client()
        res = ops.run_tm_sks(client, ctx(), {}, no_progress, never_cancel)
        self.assertEqual(res["rows"][0]["anomali"], [])
        self.assertEqual(res["summary"]["sesuai"], 1)

    def test_duplicate_flagged(self):
        _, client = self._client()
        res = ops.run_tm_sks(client, ctx(), {}, no_progress, never_cancel)
        self.assertIn("duplikat nama dosen", res["rows"][0]["anomali"])
        self.assertIn("total TM rencana 12 != 16", res["rows"][0]["anomali"])


class TestMhslulus(unittest.TestCase):
    def rows(self):
        return [
            {"nipd": "1", "nm_pd": "LULUS OK", "nm_lemb": "D3 Manajemen Informatika",
             "ket_keluar": "Lulus", "tgl_keluar": "2026-08-31T00:00:00.000Z",
             "smt_yudisium": "20252", "nm_smt_keluar": "2025/2026 Genap",
             "sk_yudisium": "SK1", "ipk": 3.5, "no_seri_ijazah": None,
             "tgl_terbit_ijazah": None},
            {"nipd": "2", "nm_pd": "MAHASISWA B", "nm_lemb": "S1 Teknik Informatika",
             "ket_keluar": "Mengajukan pengunduran diri",
             "tgl_keluar": "2026-03-02T00:00:00.000Z", "smt_yudisium": "20252",
             "nm_smt_keluar": "2025/2026 Genap", "sk_yudisium": "SK2",
             "ipk": 2.37, "no_seri_ijazah": None, "tgl_terbit_ijazah": None},
            {"nipd": "3", "nm_pd": "SUDAH", "nm_lemb": "D3 Manajemen Informatika",
             "ket_keluar": "Lulus", "tgl_keluar": "2026-05-19T00:00:00.000Z",
             "smt_yudisium": "20252", "nm_smt_keluar": "2025/2026 Genap",
             "sk_yudisium": "SK3", "ipk": 3.0, "no_seri_ijazah": "113",
             "tgl_terbit_ijazah": None},
        ]

    def test_ijazah_preset_excludes_withdrawal(self):
        client = FakeFeeder({("GET", "/mhslulus/index"): [self.rows(), 3]})
        res = ops.run_mhslulus(client, ctx(), {}, no_progress, never_cancel,
                               preset={"status": "Lulus", "tanpa_ijazah": True})
        self.assertEqual([r["nipd"] for r in res["rows"]], ["1"])
        self.assertEqual(res["summary"]["lulus_tanpa_ijazah"], 1)

    def test_full_report_counts_status(self):
        client = FakeFeeder({("GET", "/mhslulus/index"): [self.rows(), 3]})
        res = ops.run_mhslulus(client, ctx(), {}, no_progress, never_cancel)
        self.assertEqual(res["summary"]["lulus"], 2)
        self.assertEqual(res["summary"]["bukan_lulus"], 1)
        self.assertEqual(res["summary"]["lulus_tanpa_ijazah"], 1)

    def test_year_and_status_filter(self):
        client = FakeFeeder({("GET", "/mhslulus/index"): [self.rows(), 3]})
        res = ops.run_mhslulus(client, ctx(), {"tahun": "2026", "status": "Lulus"},
                               no_progress, never_cancel)
        self.assertEqual([r["nipd"] for r in res["rows"]], ["1", "3"])

    def test_preview_and_apply_add(self):
        rows = self.rows()
        index_holder = {"rows": rows}
        routes = {
            ("GET", "/mhslulus/index"): lambda p=None: [index_holder["rows"], len(index_holder["rows"])],
            ("GET", "/mhslulus/filter"): {
                "list_status": [[{"id_jns_keluar": "1", "ket_keluar": "Lulus"}]],
                "list_semester": [[{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}]],
            },
            ("GET", "/mhslulus/cari_mahasiswa"): {
                "list_mahasiswa": [[{"id_reg_pd": "r9", "id_pd": "p9", "id_sms": "s9",
                                     "nipd": "9", "nm_pd": "BARU"}]]},
            ("POST", "/mhslulus/add"): {"code": 200, "message": "ok"},
        }

        def on_post(payload):
            index_holder["rows"] = rows + [{
                "nipd": "9", "nm_pd": "BARU", "nm_lemb": "D3 Manajemen Informatika",
                "ket_keluar": "Lulus", "tgl_keluar": "2026-08-31T00:00:00.000Z",
                "smt_yudisium": "20252", "nm_smt_keluar": "2025/2026 Genap",
                "sk_yudisium": "SK9", "ipk": 3.1, "no_seri_ijazah": None,
                "tgl_terbit_ijazah": None, "id_jns_keluar": "1"}]
            return {"code": 200}

        routes[("POST", "/mhslulus/add")] = on_post
        client = FakeFeeder(routes)
        form = {"rows": [{"nim": "9", "jenis_keluar": "Lulus",
                          "tgl_keluar": "2026-08-31", "periode": "20252",
                          "tgl_sk": "2026-08-31", "no_sk": "SK9", "ipk": "3.10"}]}
        prev = ops.preview_mhslulus_add(client, ctx(), form)
        self.assertEqual(prev["summary"], {"siap": 1, "error": 0})
        out = ops.apply_mhslulus_add(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(client.writes), 1)

    def test_preview_rejects_existing_and_bad_input(self):
        rows = self.rows()
        routes = {
            ("GET", "/mhslulus/index"): [rows, len(rows)],
            ("GET", "/mhslulus/filter"): {
                "list_status": [[{"id_jns_keluar": "1", "ket_keluar": "Lulus"}]],
                "list_semester": [[{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}]],
            },
            ("GET", "/mhslulus/cari_mahasiswa"): {"list_mahasiswa": [[{"id_reg_pd": "r"}]], },
        }
        client = FakeFeeder(routes)
        form = {"rows": [
            {"nim": "1", "jenis_keluar": "Lulus", "tgl_keluar": "2026-08-31",
             "periode": "20252", "tgl_sk": "2026-08-31", "no_sk": "SK", "ipk": "3.0"},
            {"nim": "", "jenis_keluar": "Lulus", "tgl_keluar": "bad",
             "periode": "99999", "tgl_sk": "", "no_sk": "", "ipk": "x"},
        ]}
        prev = ops.preview_mhslulus_add(client, ctx(), form)
        self.assertEqual(prev["summary"]["siap"], 0)
        self.assertEqual(prev["summary"]["error"], 2)
        self.assertIn("NIM sudah tercatat keluar di Feeder", prev["errors"][0]["errors"])

    def test_preview_rejects_impossible_date_and_duplicate_nim(self):
        routes = {
            ("GET", "/mhslulus/index"): [[], 0],
            ("GET", "/mhslulus/filter"): {
                "list_status": [[{"id_jns_keluar": "1", "ket_keluar": "Lulus"}]],
                "list_semester": [[{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}]],
            },
            ("GET", "/mhslulus/cari_mahasiswa"): {
                "list_mahasiswa": [[{"id_reg_pd": "r9", "nipd": "9", "nm_pd": "BARU"}]]},
        }
        rows = [
            {"nim": "9", "jenis_keluar": "Lulus", "tgl_keluar": "2026-02-31",
             "periode": "20252", "tgl_sk": "2026-08-31", "no_sk": "SK", "ipk": "3.0"},
            {"nim": "9", "jenis_keluar": "Lulus", "tgl_keluar": "2026-08-31",
             "periode": "20252", "tgl_sk": "2026-08-31", "no_sk": "SK", "ipk": "3.0"},
        ]
        prev = ops.preview_mhslulus_add(FakeFeeder(routes), ctx(), {"rows": rows})
        self.assertEqual(prev["summary"]["error"], 2)
        self.assertIn("tanggal keluar harus YYYY-MM-DD", prev["errors"][0]["errors"])
        self.assertIn("NIM duplikat dalam CSV", prev["errors"][1]["errors"])


class TestAkmBiaya(unittest.TestCase):
    def _routes(self, biaya="0"):
        holder = {"biaya": biaya}
        rows = [{"nipd": "5", "nm_pd": "MHS", "id_reg_pd": "r5", "id_sms": "s5",
                 "id_stat_mhs": "A", "id_pembiayaan": "1", "biaya_smt": holder["biaya"],
                 "nm_stat_mhs": "Aktif", "nm_pembiayaan": "Mandiri",
                 "nm_smt": "2025/2026 Genap", "nm_lemb": "D3 Manajemen Informatika", "ips": 3.0,
                 "ipk": 3.0, "sks_smt": "2", "sks_total": "10"}]

        def on_put(payload):
            holder["biaya"] = payload["biaya_smt"]
            rows[0]["biaya_smt"] = payload["biaya_smt"]
            return {"code": 200}

        routes = {
            ("GET", "/kuliahmhs/index"): lambda p=None: [rows, 1],
            ("GET", "/kuliahmhs/filter"): {
                "list_status": [[{"id_stat_mhs": "A", "nm_stat_mhs": "Aktif"}]],
                "list_pembiayaan": [[{"id_pembiayaan": "1", "nm_pembiayaan": "Mandiri"}]],
            },
            ("GET", "/kuliahmhs/cari_mahasiswa"): {
                "list_mahasiswa": [[{"id_reg_pd": "r5", "id_sms": "s5", "nipd": "5"}]]},
            ("PUT", "/kuliahmhs/edit?id_reg_pd=r5&id_smt=20252"): on_put,
        }
        return FakeFeeder(routes), holder

    def test_preview_blocks_same_value(self):
        client, _ = self._routes(biaya="9100000")
        prev = ops.preview_akm_biaya(client, ctx(), {"rows": [{"nim": "5", "biaya": "9100000"}]})
        self.assertEqual(prev["summary"]["siap"], 1)
        out = ops.apply_akm_biaya(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")

    def test_preview_invalid(self):
        client, _ = self._routes()
        prev = ops.preview_akm_biaya(client, ctx(), {"rows": [
            {"nim": "5", "biaya": "abc"}, {"nim": "999", "biaya": "1000"}]})
        self.assertEqual(prev["summary"]["error"], 2)

    def test_apply_timeout_is_unknown(self):
        client, _ = self._routes()
        prev = ops.preview_akm_biaya(client, ctx(), {
            "rows": [{"nim": "5", "biaya": "1000"}]})

        def timeout(payload):
            raise FeederError("timeout", "timeout")

        client.routes[("PUT", "/kuliahmhs/edit?id_reg_pd=r5&id_smt=20252")] = timeout
        out = ops.apply_akm_biaya(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "unknown")

    def test_successful_write_then_readback_timeout_is_unknown(self):
        calls = {"index": 0}
        row = {"nipd": "5", "nm_pd": "MHS", "id_reg_pd": "r5", "id_sms": "s5",
               "id_stat_mhs": "A", "id_pembiayaan": "1", "biaya_smt": "0"}

        def on_index(params):
            calls["index"] += 1
            if calls["index"] >= 2:
                raise FeederError("timeout", "timeout")
            return [[row], 1]

        client = FakeFeeder({
            ("GET", "/kuliahmhs/index"): on_index,
            ("GET", "/kuliahmhs/filter"): {
                "list_status": [[{"id_stat_mhs": "A"}]],
                "list_pembiayaan": [[{"id_pembiayaan": "1"}]],
            },
            ("GET", "/kuliahmhs/cari_mahasiswa"): {
                "list_mahasiswa": [[{"id_reg_pd": "r5", "id_sms": "s5", "nipd": "5"}]]},
            ("PUT", "/kuliahmhs/edit?id_reg_pd=r5&id_smt=20252"): {"code": 200},
        })
        item = {"target": {"nim": "5", "semester": "20252"},
                "before": {"biaya": "0"}, "after": {"biaya": "1000"}}
        out = ops.apply_akm_biaya(client, ctx(), item)
        self.assertEqual(out["status"], "unknown")
        self.assertIn("baca-ulang gagal", out["message"])

    def test_preview_flags_status_lulus_as_error(self):
        rows = [{"nipd": "5", "nm_pd": "MHS", "id_reg_pd": "r5", "id_sms": "s5",
                 "id_stat_mhs": "L", "id_pembiayaan": "1", "biaya_smt": "0",
                 "nm_stat_mhs": "Lulus", "nm_pembiayaan": "Beasiswa Penuh"}]
        client = FakeFeeder({
            ("GET", "/kuliahmhs/index"): [rows, 1],
            ("GET", "/kuliahmhs/filter"): {
                "list_status": [[{"id_stat_mhs": "A", "nm_stat_mhs": "Aktif"}]],
                "list_pembiayaan": [[{"id_pembiayaan": "1", "nm_pembiayaan": "Beasiswa Penuh"}]],
            },
        })
        prev = ops.preview_akm_biaya(client, ctx(), {"rows": [{"nim": "5", "biaya": "4000000"}]})
        self.assertEqual(prev["summary"], {"siap": 0, "error": 1})
        self.assertIn("tidak bisa diubah via AKM", prev["errors"][0]["errors"][0])

    def test_preview_uses_selected_semester(self):
        rows = [{"nipd": "5", "nm_pd": "MHS", "id_reg_pd": "r5", "id_sms": "s5",
                 "id_stat_mhs": "A", "id_pembiayaan": "1", "biaya_smt": "0"}]
        seen = []

        def on_index(params):
            seen.append(params["setFilter"]["semester"][0]["id_smt"])
            return [rows, 1]

        client = FakeFeeder({("GET", "/kuliahmhs/index"): on_index})
        c = ctx()
        c["semesters"].append({"id_smt": "20251", "nm_smt": "2025/2026 Ganjil"})
        prev = ops.preview_akm_biaya(client, c, {
            "semester": "20251", "rows": [{"nim": "5", "biaya": "1000"}]})
        self.assertEqual(prev["summary"]["siap"], 1)
        self.assertEqual(prev["items"][0]["target"]["semester"], "20251")
        self.assertEqual(seen, ["20251"])


class TestAkmAudit(unittest.TestCase):
    def test_cli_and_web_status_rules_stay_in_sync(self):
        for status in ("Aktif", "Kampus Merdeka", "Keluar", "Cuti", "Non-Aktif",
                       "Lulus", "Mengajukan pengunduran diri", "tidak aktif", ""):
            self.assertEqual(ops._akm_studi(status), akm_check.studi(status))

    def test_cli_and_web_anomalies_stay_in_sync(self):
        rows = [self._akm("Aktif", ips="0", sks="0"),
                self._akm("Lulus", ips="0", sks="0")]
        rows[0]["biaya_smt"] = "0.00"
        res = self._run(rows)
        for source, web in zip(rows, res["rows"]):
            self.assertEqual(akm_check.anomali(source), web["anomali"])

    def _akm(self, status, ips="0", sks="0"):
        return {"nipd": "1", "nm_pd": "MHS", "nm_lemb": "D3 Manajemen Informatika",
                "nm_smt": "2025/2026 Genap", "nm_stat_mhs": status,
                "ips": ips, "ipk": "3.0", "sks_smt": sks, "sks_total": "10",
                "nm_pembiayaan": "Mandiri", "biaya_smt": "9100000"}

    def _run(self, rows):
        client = FakeFeeder({("GET", "/kuliahmhs/index"): [rows, len(rows)]})
        return ops.run_akm(client, ctx(), {}, no_progress, never_cancel)

    def test_aktif_nol_flagged(self):
        res = self._run([self._akm("Aktif")])
        self.assertIn("IPS/SKS 0", res["rows"][0]["anomali"])
        self.assertEqual(res["summary"]["ips_sks_nol"], 1)

    def test_keluar_cuti_nonaktif_nol_not_flagged(self):
        res = self._run([self._akm(s) for s in
                         ("Keluar", "Cuti", "Non-Aktif", "Lulus",
                          "Mengajukan pengunduran diri", "tidak aktif")])
        self.assertEqual(res["summary"]["ips_sks_nol"], 0)
        for r in res["rows"]:
            self.assertEqual(r["anomali"], [])

    def test_aktif_with_study_not_flagged(self):
        res = self._run([self._akm("Aktif", ips="3.5", sks="20")])
        self.assertEqual(res["rows"][0]["anomali"], [])


class TestKonversiAudit(unittest.TestCase):
    def test_mixed_values_are_classified_per_conversion_row(self):
        client = FakeFeeder({
            ("GET", "/aktmhs/index"): [[{
                "id_akt_mhs": "a1", "id_sms": "p1", "judul_akt_mhs": "Skripsi X",
                "nm_jns_akt_mhs": "Skripsi",
            }], 1],
            ("GET", "/aktmhs/edit/a1"): {"anggota": [{
                "nipd": "202500002", "nm_pd": "MAHASISWA A", "id_ang_akt_mhs": "ang1",
            }]},
            ("GET", "/nilaiaktmhs/transfermatkul/ang1"): {"konversi": [
                {"nm_mk": "Skripsi I", "sks_mk": "2.00", "nilai_angka": "80.18",
                 "nilai_huruf": "A", "nilai_indeks": "4.00"},
                {"nm_mk": "Skripsi II", "sks_mk": "4.00", "nilai_angka": "",
                 "nilai_huruf": "", "nilai_indeks": ""},
            ]},
        })
        out = ops.run_konversi(client, ctx(), {}, no_progress, never_cancel)
        self.assertEqual(len(out["rows"]), 2)
        self.assertEqual([r["status"] for r in out["rows"]], ["bernilai", "tanpa nilai"])
        self.assertEqual(out["summary"]["anggota_tanpa_nilai"], 1)


class TestKonversiIsi(unittest.TestCase):
    def _routes(self, konversi=None):
        holder = {"konversi": list(konversi or [])}
        act = {"id_akt_mhs": "a1", "id_sms": "p1", "judul_akt_mhs": "Skripsi X",
               "nm_jns_akt_mhs": "Skripsi"}
        angg = [{"nipd": "202500002", "nm_pd": "MAHASISWA A", "id_ang_akt_mhs": "ang1"}]
        band = [
            {"id_sms": "p1", "nilai_huruf": "B+", "bobot_nilai_min": "75.0100",
             "bobot_nilai_maks": "80.0000", "nilai_indeks": "3.50"},
            {"id_sms": "p1", "nilai_huruf": "A", "bobot_nilai_min": "80.0100",
             "bobot_nilai_maks": "100.0000", "nilai_indeks": "4.00"},
        ]
        mk = {"id_mk": "m1", "sks_mk": "2.00",
              "nm_mk": "SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023"}

        def on_add(payload):
            holder["konversi"].append({
                "id_konversi_akt_mhs": "new1", "nm_mk": "Skripsi I", "sks_mk": "2.00",
                "nilai_angka": payload["nilai_angka"], "nilai_huruf": "A  ",
                "nilai_indeks": "4.00"})
            return {"code": 200, "message": "Data berhasil ditambahkan"}

        routes = {
            ("GET", "/aktmhs/index"): [[act], 1],
            ("GET", "/aktmhs/edit/a1"): {"anggota": angg},
            ("GET", "/bobotnilai/index"): [band, 2],
            ("GET", "/matakuliah/search/cari_matakuliah"): {"list_matakuliah": [[mk], 1]},
            ("GET", "/nilaiaktmhs/transfermatkul/ang1"):
                lambda p=None: {"konversi": holder["konversi"],
                                "data": [{"nipd": "202500002", "nm_pd": "MAHASISWA A"}]},
            ("GET", "/nilaiaktmhs/transfermatkuladd/ang1"): {
                "anggota": {"id_smt": "20252", "id_akt_mhs": "a1", "id_sms": "p1"},
                "filter_periode": {"id_smt": "20252", "nm_smt": "2025/2026 Genap"}},
            ("POST", "/nilaiaktmhs/transfermatkul/add"): on_add,
        }
        return FakeFeeder(routes), holder

    def _rows(self, **over):
        row = {"kode_mk": "SF23776", "nim": "202500002", "nama": "MAHASISWA A",
               "nilai": "80.17875"}
        row.update(over)
        return [row]

    def test_preview_and_apply_ok(self):
        client, holder = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 0})
        item = prev["items"][0]
        self.assertEqual(item["after"], {"nilai": "80.18", "huruf": "A", "indeks": "4.00"})
        self.assertEqual(item["mk"]["id_mk"], "m1")
        out = ops.apply_konversi_isi(client, ctx(), item)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(holder["konversi"][0]["nm_mk"], "Skripsi I")

    def test_preview_blocks_existing_mk(self):
        existing = [{"id_konversi_akt_mhs": "old1", "nm_mk": "Skripsi I",
                     "sks_mk": "2.00", "nilai_angka": "79.00", "nilai_huruf": "B+"}]
        client, _ = self._routes(konversi=existing)
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertIn("sudah ada", prev["errors"][0]["errors"][0])

    def test_preview_rejects_bad_rows(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": [
            {"kode_mk": "SF23776", "nim": "202500002", "nilai": "abc"},
            {"kode_mk": "SF23776", "nim": "999", "nilai": "80"},
            {"kode_mk": "SF23776", "nim": "202500002", "nilai": "101"},
        ]})
        self.assertEqual(prev["summary"]["error"], 3)

    def test_preview_rejects_nan(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {
            "rows": self._rows(nilai="NaN")})
        self.assertEqual(prev["summary"]["error"], 1)

    def test_preview_duplicate_nim_mk(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows() + self._rows()})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertIn("duplikat", prev["errors"][0]["errors"][0])

    def test_preview_rounds_to_feeder_precision_before_grading(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows(nilai="80.005")})
        self.assertEqual(prev["items"][0]["after"],
                         {"nilai": "80.01", "huruf": "A", "indeks": "4.00"})

    def test_preview_rejects_mismatched_name(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {
            "rows": self._rows(nama="BUKAN MAHASISWA A")})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertTrue(any("nama tidak cocok" in e for e in prev["errors"][0]["errors"]))

    def test_preview_reports_one_transfer_read_failure_per_row(self):
        client, _ = self._routes()

        def timeout(params=None):
            raise FeederError("timeout", "timeout")

        client.routes[("GET", "/nilaiaktmhs/transfermatkul/ang1")] = timeout
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        self.assertEqual(prev["summary"], {"siap": 0, "error": 1})
        self.assertIn("gagal baca nilai konversi", prev["errors"][0]["errors"][0])

    def test_preview_rejects_ambiguous_activity(self):
        client, _ = self._routes()
        second = {"id_akt_mhs": "a2", "id_sms": "p1", "judul_akt_mhs": "Skripsi Y",
                  "nm_jns_akt_mhs": "Skripsi"}
        client.routes[("GET", "/aktmhs/index")] = [[
            {"id_akt_mhs": "a1", "id_sms": "p1", "judul_akt_mhs": "Skripsi X",
             "nm_jns_akt_mhs": "Skripsi"}, second], 2]
        client.routes[("GET", "/aktmhs/edit/a2")] = {"anggota": [{
            "nipd": "202500002", "nm_pd": "MAHASISWA A", "id_ang_akt_mhs": "ang2",
        }]}
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertTrue(any("lebih dari satu aktivitas" in e
                            for e in prev["errors"][0]["errors"]))

    def test_preview_reports_nim_and_mk_errors_together(self):
        client, _ = self._routes()
        client.routes[("GET", "/matakuliah/search/cari_matakuliah")] = {
            "list_matakuliah": [[], 0]}
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": [{
            "kode_mk": "SALAH", "nim": "999", "nilai": "80",
        }]})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertTrue(any("NIM bukan anggota" in e for e in prev["errors"][0]["errors"]))
        self.assertIn("matakuliah SALAH tidak ditemukan", prev["errors"][0]["errors"])

    def test_apply_write_timeout_is_unknown(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})

        def timeout(payload):
            raise FeederError("timeout", "timeout")

        client.routes[("POST", "/nilaiaktmhs/transfermatkul/add")] = timeout
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "unknown")

    def test_apply_conflict_when_changed_since_preview(self):
        client, holder = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        holder["konversi"].append({"id_konversi_akt_mhs": "lain", "nm_mk": "KKN",
                                   "sks_mk": "4.00"})
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")

    def test_apply_rejects_changed_target_activity(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        client.routes[("GET", "/nilaiaktmhs/transfermatkuladd/ang1")]["anggota"][
            "id_akt_mhs"] = "berubah"
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")

    def test_apply_rejects_changed_grade_band(self):
        client, _ = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        client.routes[("GET", "/bobotnilai/index")][0][1].update({
            "bobot_nilai_min": "81.0000", "bobot_nilai_maks": "100.0000",
        })
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")

    def test_apply_verifies_target_when_unrelated_row_appears_first(self):
        client, holder = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})

        def on_add(payload):
            holder["konversi"].extend([
                {"id_konversi_akt_mhs": "unrelated", "nm_mk": "KKN", "sks_mk": "4.00",
                 "nilai_angka": "80.18", "nilai_huruf": "A", "nilai_indeks": "4.00"},
                {"id_konversi_akt_mhs": "target", "nm_mk": "Skripsi I", "sks_mk": "2.00",
                 "nilai_angka": payload["nilai_angka"], "nilai_huruf": "A  ",
                 "nilai_indeks": "4.00"},
            ])
            return {"code": 200}

        client.routes[("POST", "/nilaiaktmhs/transfermatkul/add")] = on_add
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")

    def test_malformed_grade_band_is_preview_error(self):
        client, _ = self._routes()
        client.routes[("GET", "/bobotnilai/index")][0][1]["bobot_nilai_min"] = "bogus"
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertTrue(any("gagal baca band nilai" in e
                            for e in prev["errors"][0]["errors"]))

    def test_invalid_course_sks_is_preview_error(self):
        for value in ("0", "bogus"):
            with self.subTest(value=value):
                client, _ = self._routes()
                client.routes[("GET", "/matakuliah/search/cari_matakuliah")][
                    "list_matakuliah"][0][0]["sks_mk"] = value
                prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
                self.assertEqual(prev["summary"]["error"], 1)
                self.assertIn("SKS mata kuliah tidak valid", prev["errors"][0]["errors"])

    def test_apply_rejects_changed_member_identity(self):
        client, holder = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        original = client.routes[("GET", "/nilaiaktmhs/transfermatkul/ang1")]

        def swapped(params=None):
            out = dict(original(params))
            out["data"] = [{"nipd": "999", "nm_pd": "ORANG LAIN"}]
            return out

        client.routes[("GET", "/nilaiaktmhs/transfermatkul/ang1")] = swapped
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")
        self.assertIn("anggota", out["message"])

    def test_apply_ok_when_transfermatkul_has_no_identity_row(self):
        client, holder = self._routes()
        prev = ops.preview_konversi_isi(client, ctx(), {"rows": self._rows()})
        client.routes[("GET", "/nilaiaktmhs/transfermatkul/ang1")] = \
            lambda p=None: {"konversi": holder["konversi"]}
        out = ops.apply_konversi_isi(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")


class TestKonversiIsiCli(unittest.TestCase):
    def test_batch_name_must_match_feeder(self):
        target = ({"id_sms": "p1"}, {
            "nm_pd": "MAHASISWA A", "id_ang_akt_mhs": "ang1",
        })
        context = {"peta": {"202500002": [target]}, "bobot": [], "mk": {}}
        status, pesan = konversi_isi.tulis_satu(
            "202500002", "BUKAN MAHASISWA A", "80", context, False)
        self.assertEqual(status, "gagal")
        self.assertIn("nama tidak cocok", pesan)

    def test_ambiguous_activity_is_rejected(self):
        target = ({"id_sms": "p1"}, {"nm_pd": "MAHASISWA A", "id_ang_akt_mhs": "ang1"})
        context = {"peta": {"202500002": [target, target]}, "bobot": [], "mk": {}}
        status, pesan = konversi_isi.tulis_satu(
            "202500002", "MAHASISWA A", "80", context, False)
        self.assertEqual(status, "gagal")
        self.assertIn("lebih dari satu aktivitas", pesan)

    def test_value_is_rounded_before_grading(self):
        self.assertEqual(konversi_isi.normalisasi_nilai("80.005"), "80.01")

    def test_overlapping_grade_bands_are_rejected(self):
        rows = [
            {"id_sms": "x", "nilai_huruf": "B+", "bobot_nilai_min": "75",
             "bobot_nilai_maks": "81", "nilai_indeks": "3.50"},
            {"id_sms": "x", "nilai_huruf": "A", "bobot_nilai_min": "80",
             "bobot_nilai_maks": "100", "nilai_indeks": "4.00"},
        ]
        with self.assertRaisesRegex(ValueError, "tumpang tindih"):
            konversi_isi.cari_band(rows, "x", "80.50")

    def test_out_of_range_source_value_is_rejected_before_rounding(self):
        for value in ("-0.004", "100.004"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    konversi_isi.normalisasi_nilai(value)

    def test_readback_matches_target_course_not_first_new_row(self):
        unrelated = {"id_konversi_akt_mhs": "x", "nm_mk": "KKN", "sks_mk": "4.00",
                     "nilai_angka": "80.18", "nilai_huruf": "A", "nilai_indeks": "4.00"}
        target = {"id_konversi_akt_mhs": "y", "nm_mk": "Skripsi I", "sks_mk": "2.00",
                  "nilai_angka": "80.18", "nilai_huruf": "A", "nilai_indeks": "4.00"}
        self.assertFalse(konversi_isi.sudah_ada([unrelated], {
            "nm_mk": "SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023",
            "sks_mk": "2.00"}))
        self.assertTrue(konversi_isi.sudah_ada([target], {
            "nm_mk": "SF23776 - Skripsi I (2 sks) KURIKULUM SF 2023",
            "sks_mk": "2.00"}))


class TestKelasEdit(unittest.TestCase):
    def _routes(self):
        rows = [{"id_kls": "k1", "kode_mk": "DF1", "nm_kls": "A", "nm_mk": "MK",
                 "sks_mk": "2.00", "nm_lemb": "D3 Manajemen Informatika", "dosen": "X", "jmlmhs": 1,
                 "last_update": "2026-01-01", "last_sync": "2026-01-02"}]

        def on_put(payload):
            rows[0]["nm_kls"] = payload["nm_kls"]
            return {"code": 200}
        return FakeFeeder({
            ("GET", "/kelas/index"): lambda p=None: [rows, 1],
            ("PUT", "/kelas/edit/k1"): on_put,
        }), rows

    def test_apply_allowed_field(self):
        client, rows = self._routes()
        form = {"rows": [{"kode_mk": "DF1", "kelas": "A", "field": "nm_kls", "nilai": "B"}]}
        prev = ops.preview_kelas_edit(client, ctx(), form)
        self.assertEqual(prev["summary"]["siap"], 1)
        out = ops.apply_kelas_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(rows[0]["nm_kls"], "B")

    def test_disallowed_field(self):
        client, _ = self._routes()
        form = {"rows": [{"kode_mk": "DF1", "kelas": "A", "field": "id_sms", "nilai": "x"}]}
        prev = ops.preview_kelas_edit(client, ctx(), form)
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertIn("tidak diizinkan", prev["errors"][0]["errors"][0])

    def test_empty_value_and_duplicate_target(self):
        client, _ = self._routes()
        rows = [
            {"kode_mk": "DF1", "kelas": "A", "field": "nm_kls", "nilai": ""},
            {"kode_mk": "DF1", "kelas": "A", "field": "nm_kls", "nilai": "B"},
        ]
        prev = ops.preview_kelas_edit(client, ctx(), {"rows": rows})
        self.assertEqual(prev["summary"]["error"], 2)
        self.assertIn("nilai kosong", prev["errors"][0]["errors"])
        self.assertIn("target/field duplikat dalam CSV", prev["errors"][1]["errors"])


class TestTmSksPreview(unittest.TestCase):
    def test_csv_rows_select_multiple_classes(self):
        index = [[
            {"kode_mk": "DF1", "nm_mk": "MK1", "nm_kls": "A", "sks_mk": "2.00",
             "id_kls": "k1", "id_sms": "p1"},
            {"kode_mk": "DF2", "nm_mk": "MK2", "nm_kls": "B", "sks_mk": "2.00",
             "id_kls": "k2", "id_sms": "p1"},
        ], 2]
        details = {
            "k1": {"data": {"detail": {"sks_mk": "2.00"}, "dosen": [
                {"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
                 "jml_tm_real": "1", "sks_subst_tot": "2.00"}]}},
            "k2": {"data": {"detail": {"sks_mk": "2.00"}, "dosen": [
                {"id_ajar": "a2", "nm_sdm": "D2", "jml_tm_renc": "2",
                 "jml_tm_real": "2", "sks_subst_tot": "2.00"}]}},
        }
        client = FakeFeeder({
            ("GET", "/kelas/index"): index,
            ("GET", "/kelas/detail/k1"): details["k1"],
            ("GET", "/kelas/detail/k2"): details["k2"],
        })
        prev = ops.preview_tm_sks_fix(client, ctx(), {"rows": [
            {"kode_mk": "DF1", "kelas": "A"}, {"kode_mk": "DF2", "kelas": "B"}]})
        self.assertEqual(prev["summary"]["kelas_diperiksa"], 2)
        self.assertEqual(prev["summary"]["perlu_perbaikan"], 2)

    def test_missing_csv_class_is_reported(self):
        index = [[{"kode_mk": "DF1", "nm_mk": "MK1", "nm_kls": "A",
                   "sks_mk": "2.00", "id_kls": "k1", "id_sms": "p1"}], 1]
        detail = {"data": {"detail": {"sks_mk": "2.00"}, "dosen": [
            {"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
             "jml_tm_real": "1", "sks_subst_tot": "2.00"}]}}
        client = FakeFeeder({
            ("GET", "/kelas/index"): index,
            ("GET", "/kelas/detail/k1"): detail,
        })
        prev = ops.preview_tm_sks_fix(client, ctx(), {"rows": [
            {"kode_mk": "DF1", "kelas": "A"},
            {"kode_mk": "HILANG", "kelas": "B"},
        ]})
        self.assertEqual(prev["summary"]["kelas_diperiksa"], 2)
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertEqual(prev["errors"][0]["target"]["kode_mk"], "HILANG")

    def test_partial_apply_is_unknown(self):
        detail = {"data": {"detail": {"sks_mk": "2.00"}, "dosen": [
            {"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
             "jml_tm_real": "1", "sks_subst_tot": "1.00"},
            {"id_ajar": "a2", "nm_sdm": "D2", "jml_tm_renc": "1",
             "jml_tm_real": "1", "sks_subst_tot": "1.00"},
        ]}}
        client = FakeFeeder({
            ("GET", "/kelas/detail/k1"): detail,
            ("GET", "/kelas/edit/dosen/a1"): dict(detail["data"]["dosen"][0]),
            ("GET", "/kelas/edit/dosen/a2"): dict(detail["data"]["dosen"][1]),
            ("PUT", "/kelas/edit/dosen/a1"): {"code": 200},
            ("PUT", "/kelas/edit/dosen/a2"): {"code": 500, "message": "gagal"},
        })
        item = {"target": {"id_kls": "k1", "kode_mk": "DF1", "kelas": "A",
                           "sks_mk": "2.00"},
                "snapshot": [
                    {"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
                     "jml_tm_real": "1", "sks_subst_tot": "1.00"},
                    {"id_ajar": "a2", "nm_sdm": "D2", "jml_tm_renc": "1",
                     "jml_tm_real": "1", "sks_subst_tot": "1.00"}],
                "errors": [], "changes": [
                    {"id_ajar": "a1", "nm_sdm": "D1",
                     "before": {"jml_tm_renc": "1", "jml_tm_real": "1", "sks_subst_tot": "1.00"},
                     "after": {"jml_tm_renc": "8", "jml_tm_real": "8", "sks_subst_tot": "1.00"}},
                    {"id_ajar": "a2", "nm_sdm": "D2",
                     "before": {"jml_tm_renc": "1", "jml_tm_real": "1", "sks_subst_tot": "1.00"},
                     "after": {"jml_tm_renc": "8", "jml_tm_real": "8", "sks_subst_tot": "1.00"}},
                ]}
        out = ops.apply_tm_sks_fix(client, ctx(), item)
        self.assertEqual(out["status"], "unknown")
        self.assertIn("parsial", out["message"])

    def test_changed_lecturer_membership_is_conflict(self):
        detail = {"data": {"detail": {"sks_mk": "2.00"}, "dosen": [
            {"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
             "jml_tm_real": "1", "sks_subst_tot": "2.00"},
            {"id_ajar": "baru", "nm_sdm": "BARU", "jml_tm_renc": "1",
             "jml_tm_real": "1", "sks_subst_tot": "1.00"},
        ]}}
        client = FakeFeeder({("GET", "/kelas/detail/k1"): detail})
        item = {"target": {"id_kls": "k1", "kode_mk": "DF1", "kelas": "A",
                           "sks_mk": "2.00"},
                "snapshot": [{"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
                              "jml_tm_real": "1", "sks_subst_tot": "2.00"}],
                "errors": [], "changes": [{"id_ajar": "a1", "nm_sdm": "D1",
                    "before": {"jml_tm_renc": "1", "jml_tm_real": "1", "sks_subst_tot": "2.00"},
                    "after": {"jml_tm_renc": "16", "jml_tm_real": "16", "sks_subst_tot": "2.00"}}]}
        out = ops.apply_tm_sks_fix(client, ctx(), item)
        self.assertEqual(out["status"], "konflik")

    def test_invalid_course_sks_is_not_selectable(self):
        for bad in ("bogus", "0", "-2"):
            with self.subTest(sks=bad):
                index = [[{"kode_mk": "DF1", "nm_mk": "MK1", "nm_kls": "A",
                           "sks_mk": bad, "id_kls": "k1", "id_sms": "p1"}], 1]
                detail = {"data": {"detail": {"sks_mk": bad}, "dosen": [
                    {"id_ajar": "a1", "nm_sdm": "D1", "jml_tm_renc": "1",
                     "jml_tm_real": "1", "sks_subst_tot": "2.00"}]}}
                client = FakeFeeder({
                    ("GET", "/kelas/index"): index,
                    ("GET", "/kelas/detail/k1"): detail,
                })
                prev = ops.preview_tm_sks_fix(client, ctx(), {"kode": "DF1"})
                self.assertEqual(prev["summary"]["perlu_perbaikan"], 0)
                self.assertEqual(prev["summary"]["error"], 1)
                self.assertIn("SKS mata kuliah tidak valid", prev["errors"][0]["errors"])


class TestCsvExport(unittest.TestCase):
    def test_formula_escaped_and_columns(self):
        result = {"rows": [{"kode_mk": "DF1", "nm_mk": "=cmd", "anomali": ["x", "y"]}]}
        csv_text = web_app._rows_to_csv("kelas", result)
        self.assertTrue(csv_text.startswith("\ufeffkode_mk,"))
        self.assertIn("'=cmd", csv_text)
        self.assertIn("x; y", csv_text)


class TestMhs(unittest.TestCase):
    """Kontrak menu Mahasiswa: audit, edit biodata, tambah mhs baru."""

    def _rows(self):
        return [{"id_pd": "p1", "nipd": "1", "nm_pd": "ANI", "jk": "P",
                 "nm_agama": "Islam", "tgl_lahir": "2000-01-02T00:00:00.000Z",
                 "nm_lemb": "D3 Manajemen Informatika", "nm_stat_mhs": "Aktif",
                 "mulai_smt": "20201", "id_sms": "p1"},
                {"id_pd": "p2", "nipd": "2", "nm_pd": "BUDI", "jk": "L",
                 "nm_agama": "Islam", "tgl_lahir": "2001-03-04T00:00:00.000Z",
                 "nm_lemb": "S1 Teknik Informatika", "nm_stat_mhs": "Lulus",
                 "mulai_smt": "20191", "id_sms": "p2"}]

    def _client(self):
        rows = self._rows()
        holder = {"p1": {"nik": "3201010101010001", "nisn": "1", "email": "a@b",
                         "no_hp": "08", "ds_kel": "X", "id_wil": "1",
                         "nm_ibu_kandung": "Y", "tmpt_lahir": "Z",
                         "tgl_lahir": "2000-01-02T00:00:00.000Z", "id_agama": 1,
                         "kewarganegaraan": {"id_negara": "ID"}, "jk": "L",
                         "nm_pd": "ANI",
                         "agama": {"id_agama": 1, "nm_agama": "Islam"}}}

        def on_put(payload):
            holder["p1"] = dict(payload)
            return {"code": 200}

        sel = {
            "agama": [[{"id_agama": 1, "nm_agama": "Islam"}]],
            "wilayah": [[{"id_wil": "1", "kecamatan": "Kota X"}]],
            "jenis_tinggal": [[{"id_jns_tinggal": "1"}]],
            "alat_transportasi": [[{"id_alat_transport": "1"}]],
            "jenjang_pendidikan": [[{"id_jenj_didik": "21"}]],
            "pekerjaan": [[{"id_pekerjaan": 10}]],
            "penghasilan": [[{"id_penghasilan": 15}]],
            "kewarganegaraan": [[{"id_negara": "ID"}]],
        }

        def on_index(params):
            return [rows, len(rows)] if params["pagination"]["page"] == 1 else [[], len(rows)]

        client = FakeFeeder({
            ("GET", "/mahasiswa/index"): on_index,
            ("GET", "/mahasiswa/detail/p1"): lambda p=None: dict(holder["p1"]),
            ("GET", "/mahasiswa/detail/p2"): dict(holder["p1"], nm_pd="BUDI"),
            ("GET", "/mahasiswa/select/edit"): sel,
            ("PUT", "/mahasiswa/edit"): on_put,
        })
        client.holder = holder
        return client

    def test_cli_and_web_rules_stay_in_sync(self):
        self.assertEqual(ops.MHS_TEXT_FIELDS, mhs_edit.TEXT_FIELDS)
        self.assertEqual(ops.MHS_REF_FIELDS, mhs_edit.REF_FIELDS)
        self.assertEqual(ops.MHS_ENUM_FIELDS, mhs_edit.ENUM_FIELDS)
        self.assertEqual(ops.MHS_FIELDS, mhs_edit.ALL_FIELDS)
        self.assertEqual(ops.MHS_AUDIT_WAJIB, mhs_check.AUDIT_WAJIB)
        for field, nilai in (("jk", "L"), ("jk", "perempuan"),
                             ("a_terima_kps", "ya"),
                             ("nik", " 3201010101010001 "),
                             ("tgl_lahir", " 2000-01-02 ")):
            self.assertEqual(ops.mhs_normal(field, nilai),
                             mhs_edit.normal_nilai(field, nilai))
        for bad in (("jk", "X"), ("a_terima_kps", "mungkin"), ("nik", ""),
                    ("nik", "123"), ("nik_ibu", "352924470700000X"),
                    ("tgl_lahir", "02-01-2000"), ("tgl_lahir_ibu", "2026-02-31")):
            with self.assertRaises(ValueError):
                mhs_edit.normal_nilai(*bad)
            with self.assertRaises(ValueError):
                ops.mhs_normal(*bad)

    def test_preview_rejects_bad_nik_and_date(self):
        client = self._client()
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "nik", "nilai": "123"},
            {"nim": "1", "field": "tgl_lahir", "nilai": "2026-02-31"}]})
        self.assertEqual(prev["summary"], {"siap": 0, "error": 2})
        self.assertEqual(prev["errors"][0]["errors"], ["nik harus 16 digit angka"])
        self.assertEqual(prev["errors"][1]["errors"],
                         ["tgl_lahir harus tanggal YYYY-MM-DD"])

    def test_add_wajib_matches_cli(self):
        with open("mhs_add.py", encoding="utf-8") as f:
            source = f.read()
        for k in ops.MHS_ADD_WAJIB:
            self.assertIn(f'"{k}"', source)

    def test_anomali_matches_cli(self):
        d = {"nik": "123"}
        self.assertEqual(ops.mhs_anomali(d), mhs_check.audit_anomali(d))
        lengkap = {"nik": "3201010101010001", "nisn": "1", "email": "a@b",
                   "no_hp": "08", "ds_kel": "X", "id_wil": "1",
                   "nm_ibu_kandung": "Y", "tmpt_lahir": "Z",
                   "tgl_lahir": "2000-01-01", "id_agama": 1,
                   "kewarganegaraan": {"id_negara": "ID"}, "jk": "L"}
        self.assertEqual(ops.mhs_anomali(lengkap), mhs_check.audit_anomali(lengkap))

    def test_audit_filters_and_anomali(self):
        client = self._client()
        res = ops.run_mhs(client, ctx(), {"prodi": "p1", "status": "Aktif"},
                          no_progress, never_cancel)
        self.assertEqual(res["summary"]["total"], 1)
        self.assertEqual(res["rows"][0]["nipd"], "1")
        res = ops.run_mhs(client, ctx(), {"audit": True}, no_progress, never_cancel)
        self.assertEqual(res["summary"]["total"], 2)
        self.assertEqual(res["summary"]["anomali"], 0)

    def test_pagination_stops_on_empty_page(self):
        def on_index(params):
            page = params["pagination"]["page"]
            return [self._rows(), 2] if page == 1 else [[], 2]

        client = FakeFeeder({("GET", "/mahasiswa/index"): on_index})
        rows = ops.fetch_mhs_all(client)
        self.assertEqual(len(rows), 2)

    def test_pagination_retries_flaky_response(self):
        calls = {"n": 0}

        def on_index(params):
            calls["n"] += 1
            if calls["n"] == 1:
                return {}  # Feeder kadang balas {} untuk halaman 1
            return [self._rows(), 2]

        client = FakeFeeder({("GET", "/mahasiswa/index"): on_index})
        rows = ops.fetch_mhs_all(client, ulang=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(calls["n"], 2)

    def test_preview_edit_ok_and_errors(self):
        client = self._client()
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "nm_pd", "nilai": "ANI BARU"},
            {"nim": "1", "field": "csf", "nilai": "x"},
            {"nim": "9", "field": "nm_pd", "nilai": "X"},
            {"nim": "1", "field": "jk", "nilai": "L"},
        ]})
        self.assertEqual(prev["summary"], {"siap": 2, "error": 2})
        self.assertEqual(prev["items"][0]["before"]["nilai"], "ANI")
        self.assertIn("tidak diizinkan", prev["errors"][0]["errors"][0])
        self.assertIn("NIM tidak ditemukan", prev["errors"][1]["errors"])

    def test_preview_edit_detail_failure_is_row_error(self):
        client = self._client()

        def boom(p=None):
            raise FeederError("response", "bukan JSON")

        client.routes[("GET", "/mahasiswa/detail/p1")] = boom
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "nm_pd", "nilai": "ANI BARU"},
            {"nim": "2", "field": "nm_pd", "nilai": "BUDI BARU"}]})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 1})
        self.assertIn("detail gagal dibaca", prev["errors"][0]["errors"][0])

    def test_preview_edit_reference_missing(self):
        client = self._client()
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "id_wil", "nilai": "999"}]})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertIn("tak unik/tak ada", prev["errors"][0]["errors"][0])

    def test_apply_edit_verifies(self):
        client = self._client()
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "nm_pd", "nilai": "ANI BARU"}]})
        out = ops.apply_mhs_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        payload = client.writes[0][2]
        self.assertEqual(payload["nm_pd"], "ANI BARU")
        self.assertEqual(payload["nik"], "3201010101010001")

    def test_apply_edit_conflict_when_changed(self):
        client = self._client()
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "nm_pd", "nilai": "ANI BARU"}]})
        client.holder["p1"] = {"nm_pd": "ORANG LAIN"}
        out = ops.apply_mhs_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")
        self.assertEqual(client.writes, [])

    def test_apply_edit_reference_verifies_via_nested(self):
        client = self._client()
        prev = ops.preview_mhs_edit(client, ctx(), {"rows": [
            {"nim": "1", "field": "id_agama", "nilai": "1"}]})
        out = ops.apply_mhs_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(client.writes[0][2]["agama"]["id_agama"], 1)

    def _add_routes(self, nim_exists=False):
        hist = {
            "list_prodi": [[{"id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika"}]],
            "jalur_masuk": [[{"id_jalur_masuk": "12", "nm_jalur_masuk": "Seleksi Mandiri"}]],
            "jenis_daftar": [[{"id_jns_daftar": "1", "nm_jns_daftar": "Peserta didik baru"}]],
            "pembiayaan": [[{"id_pembiayaan": "1", "nm_pembiayaan": "Mandiri"}]],
        }
        sel = {
            "agama": [[{"id_agama": 1, "nm_agama": "Islam"}]],
            "wilayah": [[{"id_wil": "1", "kecamatan": "Kota X"}]],
        }
        holder = {"rows": self._rows() if nim_exists else []}

        def on_index(params):
            rows = holder["rows"]
            return [rows, len(rows)] if params["pagination"]["page"] == 1 else [[], len(rows)]

        def on_add(payload):
            return {"code": 200, "data": {"id_pd": "p9"}}

        return FakeFeeder({
            ("GET", "/mahasiswa/index"): on_index,
            ("GET", "/mahasiswa/select/edit"): sel,
            ("GET", "/history_pendidikan/get/select"): hist,
            ("GET", "/history_pendidikan/periode_aktif_all"): [{"id_smt": "20252"}],
            ("POST", "/mahasiswa/add"): on_add,
            ("GET", "/mahasiswa/detail/p9"): {"nm_pd": "BARU", "nik": "3201010101010001"},
            ("POST", "/history_pendidikan/add"): {"code": 200},
            ("GET", "/history_pendidikan/index/p9"): {
                "history_mahasiswa": [[{"nipd": "9", "id_reg_pd": "r9"}], 1]},
        })

    def _add_row(self, **over):
        row = {"nipd": "9", "nm_pd": "BARU", "jk": "L", "tmpt_lahir": "KOTA",
               "tgl_lahir": "2007-01-02", "nik": "3201010101010001",
               "nisn": "007", "email": "a@b.c", "no_hp": "0812",
               "nm_ibu_kandung": "IBU", "ds_kel": "KEL", "id_wil": "1",
               "prodi": "D3 Manajemen Informatika", "periode": "20252", "tgl_masuk": "2025-09-01",
               "jalur_masuk": "12", "jenis_daftar": "1", "pembiayaan": "1",
               "biaya_masuk": "5000000"}
        row.update(over)
        return row

    def test_preview_add_ok_and_errors(self):
        client = self._add_routes()
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [
            self._add_row(),
            self._add_row(nipd="9"),
            self._add_row(nipd="8", nik="123"),
            self._add_row(nipd="7", periode="99999"),
            self._add_row(nipd="6", prodi="Prodi Asing"),
        ]})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 4})
        self.assertEqual(prev["items"][0]["row"]["nipd"], "9")
        self.assertEqual(prev["items"][0]["after"]["prodi"], "D3 Manajemen Informatika")

    def test_preview_add_accepts_nim_column(self):
        client = self._add_routes()
        row = self._add_row()
        del row["nipd"]
        row["nim"] = "9"
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [row]})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 0})

    def test_preview_add_rejects_existing_nim(self):
        client = self._add_routes(nim_exists=True)
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [self._add_row(nipd="1")]})
        self.assertEqual(prev["summary"]["error"], 1)
        self.assertIn("sudah ada di Feeder", prev["errors"][0]["errors"][0])

    def test_preview_add_empty_jalur_message_matches_cli(self):
        client = self._add_routes()
        row = self._add_row()
        row["jalur_masuk"] = ""
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [row]})
        self.assertEqual(prev["errors"][0]["errors"],
                         ["jalur_masuk wajib (lihat opsi /history_pendidikan/get/select)"])

    def test_apply_add_writes_both_steps_and_verifies(self):
        client = self._add_routes()
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [self._add_row()]})
        out = ops.apply_mhs_add(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        self.assertIn("r9", out["message"])
        posts = [w for w in client.writes if w[0] == "POST"]
        self.assertEqual([w[1] for w in posts],
                         ["/mahasiswa/add", "/history_pendidikan/add"])
        self.assertEqual(posts[1][2]["id_pd"], "p9")
        self.assertEqual(posts[1][2]["detail"]["nik"], "3201010101010001")

    def test_apply_add_history_payload_matches_cli_keys(self):
        client = self._add_routes()
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [self._add_row()]})
        ops.apply_mhs_add(client, ctx(), prev["items"][0])
        payload = [w for w in client.writes if w[1] == "/history_pendidikan/add"][0][2]
        for key in ("id_pd", "detail", "nipd", "id_jns_daftar", "id_jalur_masuk",
                    "periode_pendaftaran_masking", "biaya_masuk_kuliah_masking",
                    "id_sms", "nm_sp"):
            self.assertIn(key, payload)

    def test_apply_add_without_row_fails_cleanly(self):
        client = self._add_routes()
        out = ops.apply_mhs_add(client, ctx(), {"id": 1, "target": {"nim": "9"}})
        self.assertEqual(out["status"], "fail")
        self.assertIn("preview", out["message"])

    def test_apply_add_history_rejected_is_unknown(self):
        client = self._add_routes()
        client.routes[("POST", "/history_pendidikan/add")] = {"code": 500, "message": "boom"}
        prev = ops.preview_mhs_add(client, ctx(), {"rows": [self._add_row()]})
        out = ops.apply_mhs_add(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "unknown")
        self.assertIn("id_pd p9", out["message"])


class TestServer(unittest.TestCase):
    """Integrasi HTTP dengan fake Feeder (tanpa jaringan luar)."""

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeFeeder({
            ("GET", "/mhslulus/filter"): {
                "list_status": [[{"id_jns_keluar": "1", "ket_keluar": "Lulus"}]],
                "list_semester": [[{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}]],
            },
            ("GET", "/history_pendidikan/periode_aktif"): [{"id_smt": "20252"}],
            ("GET", "/kelas/filter"): {
                "list_semester": [[{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}]],
                "list_prodi": [[{"id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika"}]],
            },
            ("GET", "/kelas/index"): [[{"kode_mk": "DF1", "nm_mk": "MK", "nm_kls": "A",
                                        "sks_mk": "2.00", "nm_lemb": "D3 Manajemen Informatika",
                                        "dosen": "X", "jmlmhs": 1, "id_kls": "k1",
                                        "last_update": "2026-01-01",
                                        "last_sync": "2026-01-02"}], 1],
            ("GET", "/kelas/jumlah_jmlmhs/k1"): 1,
        })
        cls._orig_client = web_app.FeederClient
        cls._orig_check = web_app.check_connection

        def fake_client(base, token, timeout=None):
            return cls.fake

        def fake_check(client):
            return {"id_smt": "20252",
                    "semesters": [{"id_smt": "20252", "nm_smt": "2025/2026 Genap"}],
                    "prodi": [{"id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika"}]}

        web_app.FeederClient = fake_client
        web_app.check_connection = fake_check
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        web_app.FeederClient = cls._orig_client
        web_app.check_connection = cls._orig_check

    def _req(self, method, path, body=None, cookie=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/json",
            "X-Requested-With": "feeder-web",
        })
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()
                return e.code, e.headers, json.loads(raw.decode() or "{}")
            finally:
                e.fp = None
                e.close()

    def test_requires_session(self):
        status, _, body = self._req("GET", "/api/session")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["kind"], "session")

    def test_connect_audit_export_flow(self):
        status, headers, body = self._req("POST", "/api/connect",
                                          {"address": "127.0.0.1:9", "token": "tok"})
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", headers.get("Set-Cookie"))
        self.assertEqual(body["active_sem"], "20252")

        status, _, body = self._req("GET", "/api/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body["prodi"][0]["nm_lemb"], "D3 Manajemen Informatika")

        status, _, body = self._req("POST", "/api/audit",
                                    {"menu": "kelas", "params": {}}, cookie=cookie)
        self.assertEqual(status, 200)
        job_id = body["job_id"]
        for _ in range(60):
            status, _, job = self._req("GET", "/api/jobs/" + job_id, cookie=cookie)
            if job["status"] not in ("queued", "running"):
                break
            threading.Event().wait(0.1)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["summary"]["total"], 1)

        url = f"http://127.0.0.1:{self.port}/api/export?menu=kelas&job_id={job_id}"
        req = urllib.request.Request(url, headers={"Cookie": cookie,
                                                   "X-Requested-With": "feeder-web"})
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8")
        self.assertIn("kode_mk", text)
        self.assertIn("DF1", text)

    def test_env_endpoints_never_leak_token(self):
        original = web_app.load_env
        web_app.load_env = lambda path=None: {
            "FEEDER_TOKEN": "tok", "FEEDER_BASE": "127.0.0.1:9"}
        try:
            status, _, body = self._req("GET", "/api/env")
            self.assertEqual(status, 200)
            self.assertTrue(body["available"])
            self.assertEqual(body["address"], "127.0.0.1:9")
            self.assertNotIn("token", json.dumps(body).lower())

            status, _, body = self._req("GET", "/api/env/check")
            self.assertEqual(status, 200)
            self.assertTrue(body["valid"])
            self.assertEqual(body["active_sem"], "20252")
            self.assertNotIn("tok", json.dumps(body))

            status, headers, body = self._req("POST", "/api/connect-env")
            self.assertEqual(status, 200)
            self.assertIn("HttpOnly", headers.get("Set-Cookie"))
            self.assertEqual(body["active_sem"], "20252")
        finally:
            web_app.load_env = original

    def test_env_absent(self):
        original = web_app.load_env
        web_app.load_env = lambda path=None: {}
        try:
            status, _, body = self._req("GET", "/api/env")
            self.assertEqual(status, 200)
            self.assertFalse(body["available"])
            status, _, body = self._req("GET", "/api/env/check")
            self.assertFalse(body["valid"])
            status, _, _ = self._req("POST", "/api/connect-env")
            self.assertEqual(status, 400)
        finally:
            web_app.load_env = original

    def test_env_check_reloads_file_each_time(self):
        original = web_app.load_env
        seen = []
        values = iter([
            {"FEEDER_TOKEN": "lama", "FEEDER_BASE": "127.0.0.1:9"},
            {"FEEDER_TOKEN": "baru", "FEEDER_BASE": "127.0.0.1:9"},
        ])
        web_app.load_env = lambda path=None: next(values)
        original_client = web_app.FeederClient

        def capture(base, token, timeout=None):
            seen.append(token)
            return self.fake

        web_app.FeederClient = capture
        try:
            self._req("GET", "/api/env/check")
            self._req("GET", "/api/env/check")
            self.assertEqual(seen, ["lama", "baru"])
        finally:
            web_app.load_env = original
            web_app.FeederClient = original_client

    def test_unknown_menu(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        status, _, body = self._req("POST", "/api/audit",
                                    {"menu": "drop_tables", "params": {}}, cookie=cookie)
        self.assertEqual(status, 400)

    def test_apply_requires_preview(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        status, _, body = self._req("POST", "/api/apply",
                                    {"preview_id": "nope", "item_ids": [1]}, cookie=cookie)
        self.assertEqual(status, 404)

    def test_invalid_selection_does_not_consume_preview(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        sid = cookie.split("=", 1)[1]
        session = web_app.APP.get(sid)
        session.previews["p1"] = {
            "menu": "akm_biaya",
            "plan": {"items": [{"id": 1, "target": {}, "before": {}, "after": {}}]},
            "created": __import__("time").time(),
            "used": False,
        }
        status, _, _ = self._req("POST", "/api/apply",
                                  {"preview_id": "p1", "item_ids": [999]}, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertFalse(session.previews["p1"]["used"])

    def test_selection_must_match_all_requested_ids(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        sid = cookie.split("=", 1)[1]
        session = web_app.APP.get(sid)
        session.previews["p2"] = {
            "menu": "akm_biaya",
            "plan": {"items": [{"id": 1, "target": {}, "before": {}, "after": {}}]},
            "created": __import__("time").time(),
            "used": False,
        }
        status, _, _ = self._req("POST", "/api/apply",
                                  {"preview_id": "p2", "item_ids": [1, 999]}, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertFalse(session.previews["p2"]["used"])

    def test_expired_preview_rejected(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        sid = cookie.split("=", 1)[1]
        session = web_app.APP.get(sid)
        session.previews["old"] = {
            "menu": "akm_biaya",
            "plan": {"items": [{"id": 1, "target": {}, "before": {}, "after": {}}]},
            "created": 0,
            "used": False,
        }
        status, _, body = self._req("POST", "/api/apply",
                                     {"preview_id": "old", "item_ids": [1]}, cookie=cookie)
        self.assertEqual(status, 409)
        self.assertIn("kedaluwarsa", body["error"]["message"])

    def test_preview_store_is_bounded_and_purges_used_or_expired(self):
        session = web_app.Session("s", "http://fake/ws", "tok", {
            "id_smt": "20252", "semesters": [], "prodi": []})
        now = __import__("time").time()
        session.previews = {
            "used": {"created": now, "used": True},
            "expired": {"created": 0, "used": False},
        }
        for i in range(web_app.MAX_PREVIEWS):
            session.previews[f"p{i}"] = {"created": now + i, "used": False}
        web_app._store_preview(session, "new", "akm_biaya",
                               {"items": [], "errors": [], "summary": {}})
        self.assertNotIn("used", session.previews)
        self.assertNotIn("expired", session.previews)
        self.assertNotIn("p0", session.previews)
        self.assertIn("new", session.previews)
        self.assertEqual(len(session.previews), web_app.MAX_PREVIEWS)

    def test_apply_runtime_error_is_reported_unknown(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        sid = cookie.split("=", 1)[1]
        session = web_app.APP.get(sid)
        session.previews["boom"] = {
            "menu": "akm_biaya",
            "plan": {"items": [{"id": 1, "target": {}, "before": {}, "after": {}}]},
            "created": __import__("time").time(), "used": False,
        }
        original = web_app.APPLIES["akm_biaya"]

        def boom(client, context, item):
            raise TypeError("bad feeder shape")

        web_app.APPLIES["akm_biaya"] = boom
        try:
            status, _, body = self._req("POST", "/api/apply",
                                        {"preview_id": "boom", "item_ids": [1]}, cookie=cookie)
            self.assertEqual(status, 200)
            for _ in range(30):
                _, _, job = self._req("GET", "/api/jobs/" + body["job_id"], cookie=cookie)
                if job["status"] not in ("queued", "running"):
                    break
                threading.Event().wait(0.05)
        finally:
            web_app.APPLIES["akm_biaya"] = original
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["results"][0]["status"], "unknown")

    def test_ijazah_preset_cannot_be_overridden(self):
        status, headers, _ = self._req("POST", "/api/connect",
                                       {"address": "127.0.0.1:9", "token": "tok"})
        cookie = headers.get("Set-Cookie").split(";")[0]
        original = web_app.AUDITS["ijazah"]
        seen = {}

        def capture(client, context, params, progress, cancel):
            seen.update(params)
            return {"rows": [], "summary": {}, "columns": []}

        web_app.AUDITS["ijazah"] = capture
        try:
            status, _, body = self._req("POST", "/api/audit", {
                "menu": "ijazah", "params": {"status": "Mutasi", "tanpa_ijazah": False}},
                cookie=cookie)
            self.assertEqual(status, 200)
            for _ in range(30):
                _, _, job = self._req("GET", "/api/jobs/" + body["job_id"], cookie=cookie)
                if job["status"] not in ("queued", "running"):
                    break
                threading.Event().wait(0.05)
        finally:
            web_app.AUDITS["ijazah"] = original
        self.assertEqual(seen["status"], "Lulus")
        self.assertTrue(seen["tanpa_ijazah"])

    def test_origin_checked(self):
        url = f"http://127.0.0.1:{self.port}/api/connect"
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={
            "Content-Type": "application/json",
            "Origin": "http://evil.example",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
            e.read()
            e.fp = None
            e.close()
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBobotnilai(unittest.TestCase):
    def _band(self, huruf="A", **over):
        row = {"id": "b1", "nm_lemb": "D3 Manajemen Informatika", "id_sms": "p1",
               "nilai_huruf": huruf, "bobot_nilai_min": "80.0100",
               "bobot_nilai_maks": "100.0000", "nilai_indeks": "4.00",
               "tgl_mulai_efektif": "2020-08-22T00:00:00.000Z",
               "tgl_akhir_efektif": "2021-01-31T00:00:00.000Z"}
        row.update(over)
        return row

    def _routes(self):
        bplus = self._band("B+", id="b2", bobot_nilai_min="75.0100",
                           bobot_nilai_maks="80.0000", nilai_indeks="3.50")
        holder = {"form": dict(self._band(), filter_prodi={"nm_lemb": "D3 Manajemen Informatika"})}

        def on_put(payload):
            holder["form"].update(payload)
            return {"code": 200, "message": "Data berhasil diubah"}

        return FakeFeeder({
            ("GET", "/bobotnilai/index"): [[self._band(), bplus], 2],
            ("GET", "/bobotnilai/filter"): {"list_prodi": [[
                {"id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika"}], 1]},
            ("GET", "/bobotnilai/edit/b1"): lambda p=None: holder["form"],
            ("PUT", "/bobotnilai/edit/b1"): on_put,
        }), holder

    def _rows(self, **over):
        row = {"prodi": "D3 Manajemen Informatika", "nilai_huruf": "A",
               "field": "tgl_akhir_efektif", "nilai": "2029-01-31"}
        row.update(over)
        return [row]

    def test_audit_lists_bands(self):
        client, _ = self._routes()
        out = ops.run_bobotnilai(client, ctx(), {}, None, never_cancel)
        self.assertEqual(out["summary"], {"total": 2, "tumpang_tindih": 0})
        self.assertEqual(len(out["rows"]), 2)

    def test_audit_flags_overlap(self):
        client, _ = self._routes()
        client.routes[("GET", "/bobotnilai/index")] = [[
            self._band("A", bobot_nilai_min="80.0100", bobot_nilai_maks="100.0000"),
            self._band("B", id="b2", bobot_nilai_min="70.0100", bobot_nilai_maks="85.0000"),
        ], 2]
        out = ops.run_bobotnilai(client, ctx(), {}, None, never_cancel)
        self.assertEqual(out["summary"]["tumpang_tindih"], 1)
        self.assertIn("tumpang tindih", out["rows"][0]["anomali"][0])

    def test_preview_and_apply_ok(self):
        client, holder = self._routes()
        holder["form"]["tgl_akhir_efektif"] = "2021-01-31T00:00:00.000Z"
        prev = ops.preview_bobotnilai_edit(client, ctx(), {"rows": self._rows()})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 0})
        out = ops.apply_bobotnilai_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        self.assertTrue(any(m[0] == "PUT" for m in client.writes))

    def test_preview_rejects_bad_rows(self):
        client, _ = self._routes()
        prev = ops.preview_bobotnilai_edit(client, ctx(), {"rows": [
            {"prodi": "Prodi Asing", "nilai_huruf": "A",
             "field": "tgl_akhir_efektif", "nilai": "2029-01-31"},
            {"prodi": "D3 Manajemen Informatika", "nilai_huruf": "A",
             "field": "bobot_nilai_min", "nilai": "abc"},
            {"prodi": "D3 Manajemen Informatika", "nilai_huruf": "A",
             "field": "nilai_huruf", "nilai": "A"},
        ]})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 2})

    def test_apply_rejects_stale(self):
        client, holder = self._routes()
        prev = ops.preview_bobotnilai_edit(client, ctx(), {"rows": self._rows()})
        holder["form"]["tgl_akhir_efektif"] = "2025-05-05T00:00:00.000Z"
        out = ops.apply_bobotnilai_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")


class TestDayatampung(unittest.TestCase):
    def _row(self, **over):
        row = {"id_smt": "20252", "id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika",
               "nm_smt": "2025/2026 Genap", "status_prodi": "A",
               "target_mhs_baru": "0", "calon_ikut_seleksi": "0",
               "calon_lulus_seleksi": "0", "daftar_sbg_mhs": "0",
               "pst_undur_diri": "0", "jml_mgu_kul": "16",
               "tgl_awal_kul": "2026-03-01T00:00:00.000Z",
               "tgl_akhir_kul": "2026-08-31T00:00:00.000Z",
               "last_update": "2026-04-07T10:35:03.200Z",
               "last_sync": "2026-04-07T14:49:40.406Z"}
        row.update(over)
        return row

    def _routes(self):
        holder = {"form": {"result": dict(self._row())}}

        def on_put(payload):
            holder["form"]["result"].update(payload)
            return {"code": 200, "message": "Data berhasil diubah"}

        filt = {"list_prodi": [[{"id_sms": "p1", "nm_lemb": "D3 Manajemen Informatika"}], 1],
                "list_semester": [[{"id_smt": "20252",
                                    "nm_smt": "2025/2026 Genap"}], 1]}
        return FakeFeeder({
            ("GET", "/dayatampung/index"): [
                [self._row(), self._row(id_sms="p2", nm_lemb="S1 X",
                                        target_mhs_baru="6")], 2],
            ("GET", "/dayatampung/filter"): filt,
            ("GET", "/history_pendidikan/periode_aktif_all"):
                [{"id_smt": "20252", "id_sms": None}],
            ("GET", "/dayatampung/edit?id_sms=p1&id_smt=20252"):
                lambda p=None: holder["form"],
            ("PUT", "/dayatampung/edit?id_smt=20252&id_sms=p1"): on_put,
        }), holder

    def test_audit_flags(self):
        client, _ = self._routes()
        out = ops.run_dayatampung(client, ctx(), {}, None, never_cancel)
        self.assertEqual(out["summary"],
                         {"total": 2, "belum_sync": 0, "target_0": 1})
        self.assertIn("target 0", out["rows"][0]["anomali"])

    def test_preview_and_apply_ok(self):
        client, holder = self._routes()
        prev = ops.preview_dayatampung_edit(client, ctx(), {"rows": [
            {"prodi": "D3 Manajemen Informatika", "semester": "20252",
             "field": "target_mhs_baru", "nilai": "120"}]})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 0})
        self.assertEqual(prev["items"][0]["before"], {"nilai": "0"})
        out = ops.apply_dayatampung_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "ok")
        self.assertTrue(any(m[0] == "PUT" for m in client.writes))

    def test_preview_rejects_bad_rows(self):
        client, _ = self._routes()
        prev = ops.preview_dayatampung_edit(client, ctx(), {"rows": [
            {"prodi": "Prodi Asing", "semester": "20252",
             "field": "target_mhs_baru", "nilai": "5"},
            {"prodi": "D3 Manajemen Informatika", "semester": "20252",
             "field": "target_mhs_baru", "nilai": "abc"},
            {"prodi": "D3 Manajemen Informatika", "semester": "20252",
             "field": "tgl_awal_kul", "nilai": "2026-02-31"},
            {"prodi": "D3 Manajemen Informatika", "semester": "20252",
             "field": "jml_mgu_kul", "nilai": "17"},
            {"prodi": "D3 Manajemen Informatika", "semester": "20252",
             "field": "jml_mgu_kul", "nilai": "18"},
        ]})
        self.assertEqual(prev["summary"], {"siap": 1, "error": 4})

    def test_apply_rejects_stale(self):
        client, holder = self._routes()
        prev = ops.preview_dayatampung_edit(client, ctx(), {"rows": [
            {"prodi": "D3 Manajemen Informatika", "semester": "20252",
             "field": "target_mhs_baru", "nilai": "120"}]})
        holder["form"]["result"]["target_mhs_baru"] = "99"
        out = ops.apply_dayatampung_edit(client, ctx(), prev["items"][0])
        self.assertEqual(out["status"], "konflik")

    def test_kontrak_cli(self):
        self.assertEqual(ops.DAYATAMPUNG_FIELDS, dayatampung_edit.FIELDS)
        r = {"last_update": "2026-04-07", "last_sync": "2026-01-01",
             "target_mhs_baru": "0", "id_smt": "20252", "id_sms": "p1"}
        self.assertEqual(dayatampung_check.daya_anomali(r, {("20252", "p1")}),
                         ops._dayatampung_anomali(r, {("20252", "p1")}))
        for field, nilai, ok in [("target_mhs_baru", "120", True),
                                 ("target_mhs_baru", "abc", False),
                                 ("tgl_awal_kul", "2026-03-01", True),
                                 ("tgl_awal_kul", "2026-02-31", False)]:
            cli = dayatampung_edit.validasi_nilai(field, nilai) is None
            web = ops._dayatampung_valid(field, nilai) is None
            self.assertEqual(cli, ok, (field, nilai))
            self.assertEqual(web, ok, (field, nilai))


if __name__ == "__main__":
    unittest.main(verbosity=2)
