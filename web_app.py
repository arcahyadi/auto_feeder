#!/usr/bin/env python3
"""Server web lokal Automation Feeder (stdlib only).

Jalankan:
    python3 web_app.py --host 127.0.0.1 --port 8765

Token Feeder hanya disimpan di memori proses, terikat pada sesi dan alamat
Feeder. Bind default loopback. Semua aksi tulis melalui preview lalu apply.
"""
import argparse
import csv
import io
import json
import os
import secrets
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import web_operations as ops
from feeder_client import (Cancelled, FeederClient, FeederError,
                           check_connection, normalize_base)

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")


def load_env(path=None):
    """Baca .env repo ini -> dict. Nilai tidak pernah dikirim ke browser."""
    path = path or os.path.join(HERE, ".env")
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


DEFAULT_BASE = "http://localhost:8100"


def env_session():
    """Muat ulang .env lalu kembalikan base, token, dan metadata koneksi."""
    env = load_env()
    token = env.get("FEEDER_TOKEN", "").strip()
    address = env.get("FEEDER_BASE", "").strip()
    if not token:
        raise FeederError("input", "FEEDER_TOKEN tidak ada di .env")
    base = normalize_base(address or DEFAULT_BASE)
    client = FeederClient(base, token, timeout=20)
    return base, token, check_connection(client)


STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
MAX_BODY = 5 * 1024 * 1024
SESSION_TTL = 8 * 3600
PREVIEW_TTL = 30 * 60
MAX_JOBS = 20
MAX_PREVIEWS = 20
COOKIE = "feeder_sid"

AUDITS = {
    "kelas": ops.run_kelas,
    "tm_sks": ops.run_tm_sks,
    "nilai": ops.run_nilai,
    "akm": ops.run_akm,
    "aktivitas": ops.run_aktivitas,
    "konversi": ops.run_konversi,
    "bobotnilai": ops.run_bobotnilai,
    "mhslulus": ops.run_mhslulus,
    "ijazah": ops.run_mhslulus,
    "mhs": ops.run_mhs,
    "dayatampung": ops.run_dayatampung,
}
PRESETS = {"ijazah": {"status": "Lulus", "tanpa_ijazah": True}}
PREVIEWS = {
    "mhslulus_add": ops.preview_mhslulus_add,
    "akm_biaya": ops.preview_akm_biaya,
    "kelas_edit": ops.preview_kelas_edit,
    "tm_sks_fix": ops.preview_tm_sks_fix,
    "konversi_isi": ops.preview_konversi_isi,
    "bobotnilai_edit": ops.preview_bobotnilai_edit,
    "mhs_edit": ops.preview_mhs_edit,
    "mhs_add": ops.preview_mhs_add,
    "dayatampung_edit": ops.preview_dayatampung_edit,
}
APPLIES = {
    "mhslulus_add": ops.apply_mhslulus_add,
    "akm_biaya": ops.apply_akm_biaya,
    "kelas_edit": ops.apply_kelas_edit,
    "tm_sks_fix": ops.apply_tm_sks_fix,
    "konversi_isi": ops.apply_konversi_isi,
    "bobotnilai_edit": ops.apply_bobotnilai_edit,
    "mhs_edit": ops.apply_mhs_edit,
    "mhs_add": ops.apply_mhs_add,
    "dayatampung_edit": ops.apply_dayatampung_edit,
}
EXPORTS = {
    "kelas": lambda r: r["rows"],
    "tm_sks": ops.csv_rows_tm_sks,
    "nilai": lambda r: r["rows"],
    "akm": lambda r: r["rows"],
    "aktivitas": lambda r: r["rows"],
    "konversi": lambda r: r["rows"],
    "bobotnilai": lambda r: r["rows"],
    "mhslulus": lambda r: r["rows"],
    "ijazah": lambda r: r["rows"],
    "mhs": lambda r: r["rows"],
    "dayatampung": lambda r: r["rows"],
}
EXPORT_COLUMNS = {
    "kelas": ["kode_mk", "nm_mk", "nm_kls", "sks_mk", "nm_lemb", "dosen",
              "jmlmhs", "anomali"],
    "tm_sks": ["kode_mk", "nm_mk", "nm_kls", "sks_mk", "nm_sdm", "jml_tm_renc",
               "jml_tm_real", "sks_subst_tot", "anomali"],
    "nilai": ["kode_mk", "nm_mk", "nm_kls", "sks_mk", "jmlmhs", "jml_sudahdinilai",
              "anomali"],
    "akm": ["nipd", "nm_pd", "nm_lemb", "nm_stat_mhs", "ips", "ipk", "sks_smt",
            "sks_total", "nm_pembiayaan", "biaya_smt", "anomali"],
    "aktivitas": ["judul", "jenis", "nm_smt", "tgl_sk", "lokasi", "nm_lemb", "anomali"],
    "konversi": ["judul", "jenis", "nipd", "nama", "mk", "sks", "angka", "huruf",
                 "indeks", "status"],
    "bobotnilai": ["prodi", "huruf", "min", "maks", "indeks", "tgl_mulai",
                   "tgl_akhir", "anomali"],
    "mhslulus": ["nipd", "nm_pd", "nm_lemb", "ket_keluar", "tgl_keluar",
                 "nm_smt_keluar", "sk_yudisium", "ipk", "no_seri_ijazah", "anomali"],
    "mhs": ["nipd", "nm_pd", "jk", "nm_agama", "tgl_lahir", "nm_lemb",
             "nm_stat_mhs", "mulai_smt", "anomali"],
    "dayatampung": ["nm_smt", "nm_lemb", "status_prodi", "target_mhs_baru",
                    "calon_ikut_seleksi", "calon_lulus_seleksi",
                    "daftar_sbg_mhs", "pst_undur_diri", "jml_mgu_kul",
                    "tgl_awal_kul", "tgl_akhir_kul", "anomali"],
}
TEMPLATES = {
    "mhslulus_add": ["nim", "jenis_keluar", "tgl_keluar", "periode", "tgl_sk",
                     "no_sk", "ipk"],
    "akm_biaya": ["nim", "biaya"],
    "kelas_edit": ["kode_mk", "kelas", "field", "nilai"],
    "tm_sks_fix": ["kode_mk", "kelas"],
    "konversi_isi": ["kode_mk", "nim", "nama", "nilai"],
    "bobotnilai_edit": ["prodi", "nilai_huruf", "field", "nilai"],
    "mhs_edit": ["nim", "field", "nilai"],
    "mhs_add": ["nipd", "nm_pd", "jk", "tmpt_lahir", "tgl_lahir", "nik", "nisn",
                "email", "no_hp", "nm_ibu_kandung", "jln", "rt", "rw", "ds_kel",
                "id_wil", "kode_pos", "id_agama", "prodi", "periode", "tgl_masuk",
                 "jalur_masuk", "jenis_daftar", "pembiayaan", "biaya_masuk"],
    "dayatampung_edit": ["prodi", "semester", "field", "nilai"],
}


class ApiError(Exception):
    def __init__(self, status, kind, message):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


class Session:
    def __init__(self, sid, base, token, meta):
        self.id = sid
        self.base = base
        self.token = token
        self.meta = meta
        self.created = time.time()
        self.seen = time.time()
        self.jobs = {}
        self.previews = {}
        self.lock = threading.Lock()

    def client(self):
        return FeederClient(self.base, self.token)


class Job:
    def __init__(self, kind):
        self.id = secrets.token_hex(8)
        self.kind = kind
        self.status = "queued"       # queued/running/done/failed/cancelled
        self.progress = {"done": 0, "total": 0, "label": "Menunggu"}
        self.result = None
        self.error = None
        self.log = []
        self.created = time.time()
        self.cancel_event = threading.Event()

    def snapshot(self):
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "progress": self.progress, "result": self.result,
                "error": self.error, "log": self.log[-50:]}


class App:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()

    def sweep(self):
        now = time.time()
        with self.lock:
            for sid in list(self.sessions):
                s = self.sessions[sid]
                if now - s.seen > SESSION_TTL:
                    del self.sessions[sid]

    def get(self, sid):
        if not sid:
            return None
        with self.lock:
            s = self.sessions.get(sid)
        if s:
            s.seen = time.time()
        return s

    def drop(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)

    def create(self, base, token, meta):
        sid = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[sid] = Session(sid, base, token, meta)
        return self.sessions[sid]

    def start_job(self, session, kind, target, *, write=False):
        job = Job(kind)
        with session.lock:
            session.jobs[job.id] = job
            if len(session.jobs) > MAX_JOBS:
                oldest = min(session.jobs.values(), key=lambda j: j.created)
                session.jobs.pop(oldest.id, None)
        t = threading.Thread(target=self._run, args=(session, job, target, write),
                             daemon=True)
        t.start()
        return job

    def _run(self, session, job, target, write):
        job.status = "running"
        job.progress["label"] = "Membaca Feeder"
        client = session.client()
        cancel = job.cancel_event.is_set
        progress = lambda done, total, *rest: job.progress.update(
            {"done": done, "total": total, "label": "Membaca data"})

        def guarded():
            if write:
                # ponytail: satu lock global; per-target lock bila throughput tulis penting
                acquired = self.write_lock.acquire(timeout=900)
                if not acquired:
                    raise FeederError("busy", "Pekerjaan tulis lain sedang berjalan")
            try:
                return target(client, progress, cancel)
            finally:
                if write:
                    self.write_lock.release()
        try:
            job.result = guarded()
            job.status = "done"
            job.progress["label"] = "Selesai"
        except Cancelled:
            job.status = "cancelled"
            job.progress["label"] = "Dibatalkan"
        except FeederError as e:
            job.status = "failed"
            job.error = e.to_dict()
        except Exception as e:  # noqa: BLE001 - jangan bocorkan detail internal
            job.status = "failed"
            job.error = {"kind": "internal", "message": f"Kesalahan internal: {type(e).__name__}"}

    def audit_job(self, session, menu, params):
        fn = AUDITS.get(menu)
        if not fn:
            raise ApiError(400, "input", f"Menu audit '{menu}' tidak dikenal")
        merged = dict(params or {})
        merged.update(PRESETS.get(menu, {}))
        ctx = {"active_sem": {"id_smt": session.meta["id_smt"],
                              "nm_smt": _nm_smt(session.meta, session.meta["id_smt"])},
               "semesters": session.meta["semesters"], "prodi": session.meta["prodi"]}

        def target(client, progress, cancel):
            return fn(client, ctx, merged, progress, cancel)
        return self.start_job(session, f"audit:{menu}", target)


def _nm_smt(meta, sid):
    for s in meta.get("semesters", []):
        if s["id_smt"] == sid:
            return s["nm_smt"]
    return sid


APP = App()


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


def _rows_to_csv(menu, result):
    fn = EXPORTS.get(menu)
    rows = fn(result) if fn else result["rows"]
    cols = EXPORT_COLUMNS.get(menu) or [k for k, _ in result.get("columns", [])]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([ops.csv_safe("; ".join(map(str, r.get(k))) if isinstance(r.get(k), list)
                                 else r.get(k, "")) for k in cols])
    return "\ufeff" + buf.getvalue()


def _store_preview(session, pid, menu, plan):
    """Simpan preview dengan batas memori per sesi."""
    now = time.time()
    with session.lock:
        for old in [key for key, value in session.previews.items()
                    if value["used"] or now - value["created"] > PREVIEW_TTL]:
            session.previews.pop(old, None)
        while len(session.previews) >= MAX_PREVIEWS:
            oldest = min(session.previews, key=lambda key: session.previews[key]["created"])
            session.previews.pop(oldest, None)
        session.previews[pid] = {"menu": menu, "plan": plan, "created": now, "used": False}


class Handler(BaseHTTPRequestHandler):
    server_version = "AutomationFeeder/1.0"

    def log_message(self, fmt, *args):
        pass  # token/PII tidak ditulis ke log

    # -- util ---------------------------------------------------------------

    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj, extra=None):
        self._send(status, _json_bytes(obj), extra=extra)

    def _error(self, status, kind, message):
        self._json(status, {"error": {"kind": kind, "message": message}})

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "input", "Content-Length tidak valid")
        if length > MAX_BODY:
            raise ApiError(413, "input", "Data terlalu besar (maks 5 MB)")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "input", "Body harus JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "input", "Body harus objek JSON")
        return data

    def _session(self):
        sid = self._cookie()
        s = APP.get(sid)
        if not s:
            raise ApiError(401, "session", "Sesi tidak ditemukan; hubungkan ulang")
        return s

    def _cookie(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def _check_origin(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin:
            if urllib.parse.urlsplit(origin).netloc != host:
                raise ApiError(403, "origin", "Origin tidak cocok dengan server")
        elif self.headers.get("X-Requested-With") != "feeder-web":
            raise ApiError(403, "origin", "Permintaan tanpa Origin ditolak")

    def _check_host(self):
        try:
            host = urllib.parse.urlsplit("//" + (self.headers.get("Host") or "")).hostname
        except ValueError:
            host = None
        if host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
            raise ApiError(403, "origin", "Host tidak diizinkan")

    # -- routing ------------------------------------------------------------

    def do_GET(self):
        try:
            self._route("GET")
        except ApiError as e:
            self._error(e.status, e.kind, e.message)
        except Exception:  # noqa: BLE001
            self._error(500, "internal", "Kesalahan internal server")

    def do_POST(self):
        try:
            self._route("POST")
        except ApiError as e:
            self._error(e.status, e.kind, e.message)
        except Exception:  # noqa: BLE001
            self._error(500, "internal", "Kesalahan internal server")

    def _route(self, method):
        APP.sweep()
        self._check_host()
        path = urllib.parse.urlsplit(self.path).path
        if method == "GET" and path in STATIC:
            return self._static(path)
        if method == "GET" and path == "/api/session":
            return self._api_session()
        if method == "GET" and path == "/api/env":
            return self._api_env()
        if method == "GET" and path == "/api/env/check":
            return self._api_env_check()
        if method == "GET" and path.startswith("/api/jobs/"):
            return self._api_job(path.rsplit("/", 1)[1])
        if method == "GET" and path == "/api/export":
            return self._api_export()
        if method == "GET" and path == "/api/template":
            return self._api_template()
        if method == "POST":
            self._check_origin()
            if path == "/api/connect":
                return self._api_connect()
            if path == "/api/connect-env":
                return self._api_connect_env()
            if path == "/api/logout":
                return self._api_logout()
            if path == "/api/audit":
                return self._api_audit()
            if path == "/api/preview":
                return self._api_preview()
            if path == "/api/apply":
                return self._api_apply()
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                return self._api_cancel(path.split("/")[3])
        raise ApiError(404, "not_found", "Alamat tidak ditemukan")

    def _static(self, path):
        name, ctype = STATIC[path]
        with open(os.path.join(WEB_DIR, name), "rb") as f:
            body = f.read()
        self._send(200, body, ctype)

    # -- API ----------------------------------------------------------------

    def _start_session(self, base, token, meta):
        sid = self._cookie()
        if sid:
            APP.drop(sid)
        s = APP.create(base, token, meta)
        cookie = (f"{COOKIE}={s.id}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}")
        self._json(200, {"base": base, "active_sem": s.meta["id_smt"],
                         "semesters": s.meta["semesters"], "prodi": s.meta["prodi"]},
                   extra={"Set-Cookie": cookie})

    def _api_connect(self):
        body = self._body()
        addr = body.get("address")
        token = body.get("token")
        if not isinstance(addr, str) or not isinstance(token, str):
            raise ApiError(400, "input", "Alamat dan token harus berupa teks")
        token = token.strip()
        if not token:
            raise ApiError(400, "input", "Token kosong")
        if len(token) > 8000:
            raise ApiError(400, "input", "Token terlalu panjang")
        try:
            base = normalize_base(addr)
        except FeederError as e:
            raise ApiError(400, e.kind, e.message)
        client = FeederClient(base, token, timeout=20)
        try:
            meta = check_connection(client)
        except FeederError as e:
            raise ApiError(401 if e.kind == "auth" else 502, e.kind, e.message)
        self._start_session(base, token, meta)

    def _api_connect_env(self):
        """Hubungkan memakai token dari .env repo; token tak pernah ke browser."""
        try:
            base, token, meta = env_session()
        except FeederError as e:
            raise ApiError(400 if e.kind == "input" else
                           (401 if e.kind == "auth" else 502), e.kind, e.message)
        self._start_session(base, token, meta)

    def _api_env(self):
        """Info .env untuk UI; token sengaja tidak pernah dikirim."""
        env = load_env()
        self._json(200, {"available": bool(env.get("FEEDER_TOKEN", "").strip()),
                         "address": env.get("FEEDER_BASE", "").strip() or DEFAULT_BASE})

    def _api_env_check(self):
        try:
            base, _, meta = env_session()
        except FeederError as e:
            self._json(200, {"valid": False,
                             "available": bool(load_env().get("FEEDER_TOKEN", "").strip()),
                             "message": e.message})
            return
        self._json(200, {"valid": True, "available": True, "base": base,
                         "message": "Token .env masih valid",
                         "active_sem": meta["id_smt"],
                         "nm_smt": _nm_smt(meta, meta["id_smt"]),
                         "prodi": meta["prodi"]})

    def _api_logout(self):
        sid = self._cookie()
        if sid:
            APP.drop(sid)
        self._json(200, {"ok": True},
                   extra={"Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})

    def _api_session(self):
        s = self._session()
        self._json(200, {"base": s.base, "active_sem": s.meta["id_smt"],
                         "semesters": s.meta["semesters"], "prodi": s.meta["prodi"]})

    def _api_job(self, job_id):
        s = self._session()
        with s.lock:
            job = s.jobs.get(job_id)
        if not job:
            raise ApiError(404, "not_found", "Pekerjaan tidak ditemukan")
        self._json(200, job.snapshot())

    def _api_cancel(self, job_id):
        s = self._session()
        with s.lock:
            job = s.jobs.get(job_id)
        if not job:
            raise ApiError(404, "not_found", "Pekerjaan tidak ditemukan")
        job.cancel_event.set()
        self._json(200, {"ok": True})

    def _api_audit(self):
        s = self._session()
        body = self._body()
        menu, params = body.get("menu"), body.get("params", {})
        if not isinstance(menu, str) or not isinstance(params, dict):
            raise ApiError(400, "input", "Menu/parameter audit tidak valid")
        job = APP.audit_job(s, menu, params)
        self._json(200, {"job_id": job.id})

    def _api_preview(self):
        s = self._session()
        body = self._body()
        menu = body.get("menu")
        if not isinstance(menu, str):
            raise ApiError(400, "input", "Menu preview tidak valid")
        fn = PREVIEWS.get(menu)
        if not fn:
            raise ApiError(400, "input", f"Menu tulis '{menu}' tidak dikenal")
        ctx = {"active_sem": {"id_smt": s.meta["id_smt"],
                              "nm_smt": _nm_smt(s.meta, s.meta["id_smt"])},
               "semesters": s.meta["semesters"], "prodi": s.meta["prodi"]}
        form = body.get("form", {})
        if not isinstance(form, dict):
            raise ApiError(400, "input", "Form preview harus berupa objek")
        rows = form.get("rows")
        if rows is not None:
            if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(r, dict) for r in rows):
                raise ApiError(400, "input", "Baris preview tidak valid atau terlalu banyak")

        def target(client, progress, cancel):
            return fn(client, ctx, form, progress, cancel)
        job = APP.start_job(s, f"preview:{menu}", target)
        while job.status in ("queued", "running"):
            time.sleep(0.15)
        if job.status == "failed":
            err = job.error or {}
            raise ApiError(502 if err.get("kind") != "input" else 400,
                           err.get("kind", "error"), err.get("message", "Preview gagal"))
        if job.status == "cancelled":
            raise ApiError(409, "cancelled", "Preview dibatalkan")
        pid = secrets.token_hex(8)
        _store_preview(s, pid, menu, job.result)
        self._json(200, {"preview_id": pid, "items": job.result["items"],
                         "errors": job.result["errors"],
                         "summary": job.result["summary"]})

    def _api_apply(self):
        s = self._session()
        body = self._body()
        pid = body.get("preview_id") or ""
        ids = body.get("item_ids")
        if not isinstance(ids, list) or not ids:
            raise ApiError(400, "input", "Tidak ada baris yang dipilih")
        try:
            if any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
                raise ValueError
            wanted = set(ids)
        except (TypeError, ValueError):
            raise ApiError(400, "input", "ID baris tidak valid")
        with s.lock:
            p = s.previews.get(pid)
            if not p:
                raise ApiError(404, "not_found", "Preview tidak ditemukan; buat preview baru")
            if p["used"]:
                raise ApiError(409, "conflict", "Preview sudah diterapkan; buat preview baru")
            if time.time() - p["created"] > PREVIEW_TTL:
                raise ApiError(409, "conflict", "Preview kedaluwarsa; buat preview baru")
            menu, plan = p["menu"], p["plan"]
            items = [it for it in plan["items"] if it["id"] in wanted]
            if {it["id"] for it in items} != wanted:
                raise ApiError(400, "input", "Baris terpilih tidak ada di preview")
            p["used"] = True
        apply_fn = APPLIES.get(menu)
        ctx = {"active_sem": {"id_smt": s.meta["id_smt"],
                              "nm_smt": _nm_smt(s.meta, s.meta["id_smt"])},
               "semesters": s.meta["semesters"], "prodi": s.meta["prodi"]}

        def target(client, progress, cancel):
            results = []
            total = len(items)
            for n, it in enumerate(items, 1):
                if cancel():
                    return {"results": results,
                            "summary": {"total": total, "selesai": len(results),
                                        "tersisa": total - len(results),
                                        "ok": sum(r["status"] == "ok" for r in results),
                                        "fail": sum(r["status"] == "fail" for r in results),
                                        "konflik": sum(r["status"] == "konflik" for r in results),
                                        "unknown": sum(r["status"] == "unknown" for r in results),
                                        "dibatalkan": 1}}
                try:
                    out = apply_fn(client, ctx, it)
                except FeederError as e:
                    uncertain = e.kind in ("connection", "timeout", "response", "server")
                    out = {"status": "unknown" if uncertain else "fail",
                           "message": ("hasil belum pasti: " if uncertain else "") + e.message}
                except Exception as e:  # data Feeder tidak sesuai kontrak
                    out = {"status": "unknown",
                           "message": f"hasil belum pasti: respons {type(e).__name__}"}
                out["item_id"] = it["id"]
                out["target"] = it.get("target", {})
                results.append(out)
                progress(n, total, "Menulis")
            ok = sum(1 for r in results if r["status"] == "ok")
            return {"results": results,
                    "summary": {"total": total, "ok": ok,
                                "fail": sum(1 for r in results if r["status"] == "fail"),
                                "konflik": sum(1 for r in results if r["status"] == "konflik"),
                                "unknown": sum(1 for r in results if r["status"] == "unknown")}}
        job = APP.start_job(s, f"apply:{menu}", target, write=True)
        self._json(200, {"job_id": job.id})

    def _api_export(self):
        s = self._session()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        menu = (q.get("menu") or [""])[0]
        job_id = (q.get("job_id") or [""])[0]
        with s.lock:
            job = s.jobs.get(job_id)
        if not job or job.status != "done" or not job.result:
            raise ApiError(409, "conflict", "Hasil belum siap diunduh")
        if menu not in EXPORTS:
            raise ApiError(400, "input", "Menu ekspor tidak dikenal")
        body = _rows_to_csv(menu, job.result).encode("utf-8")
        fname = f"feeder_{menu}_{s.meta['id_smt']}.csv"
        self._send(200, body, "text/csv; charset=utf-8",
                   extra={"Content-Disposition": f'attachment; filename="{fname}"'})

    def _api_template(self):
        self._session()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        menu = (q.get("menu") or [""])[0]
        cols = TEMPLATES.get(menu)
        if not cols:
            raise ApiError(400, "input", "Template tidak tersedia untuk menu ini")
        body = ("\ufeff" + ",".join(cols) + "\n").encode("utf-8")
        self._send(200, body, "text/csv; charset=utf-8",
                   extra={"Content-Disposition": f'attachment; filename="template_{menu}.csv"'})


def check_env():
    """Cek validitas token .env tanpa menjalankan server. Exit 0 bila valid."""
    env = load_env()
    if not env.get("FEEDER_TOKEN", "").strip():
        print("Token .env: TIDAK ADA (FEEDER_TOKEN kosong)")
        return 1
    print(f"Alamat  : {env.get('FEEDER_BASE') or '(default) http://localhost:8100'}")
    print("Cek token .env...", end=" ", flush=True)
    try:
        base, _, meta = env_session()
    except FeederError as e:
        print(f"TIDAK VALID ({e.kind}): {e.message}")
        return 1
    print("VALID ✓")
    print(f"Base    : {base}")
    print(f"Semester: {meta['id_smt']} ({_nm_smt(meta, meta['id_smt'])})")
    print(f"Prodi   : {', '.join(p.get('nm_lemb', '') for p in meta['prodi'])}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Automation Feeder Web (lokal)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--check", action="store_true",
                    help="cek token .env lalu keluar (tanpa menjalankan server)")
    a = ap.parse_args()
    if a.check:
        raise SystemExit(check_env())
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Hanya boleh bind ke loopback (127.0.0.1/localhost/::1)")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Automation Feeder Web: http://{a.host}:{a.port}")
    env = load_env()
    if env.get("FEEDER_TOKEN", "").strip():
        print(f"Token .env terdeteksi ({env.get('FEEDER_BASE') or 'alamat default'}).")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
