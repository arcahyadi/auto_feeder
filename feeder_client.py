#!/usr/bin/env python3
"""Koneksi Feeder per sesi web: normalisasi alamat, serialisasi axios 0.x,
HTTP bertoken eksplisit, dan error terstruktur. Stdlib only.

Dipakai web_app.py; CLI lama tetap memakai modul masing-masing.
"""
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 30


class FeederError(Exception):
    """Error terstruktur; kind dipakai UI untuk membedakan penyebab."""

    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status

    def to_dict(self):
        return {"kind": self.kind, "message": self.message, "status": self.status}


def normalize_base(addr):
    """Alamat pengguna -> base URL berakhiran /ws.

    Menerima '192.0.2.10:8100', 'http://host:8100', 'https://host',
    'https://host/ws'. Menolak skema lain, kredensial dalam URL, dan
    alamat tanpa host. IP privat/Tailscale memang didukung.
    """
    addr = (addr or "").strip()
    if not addr:
        raise FeederError("invalid_address", "Alamat Feeder kosong")
    if "://" not in addr:
        addr = "http://" + addr
    p = urllib.parse.urlsplit(addr)
    if p.scheme not in ("http", "https"):
        raise FeederError("invalid_address", "Skema harus http atau https")
    if not p.hostname:
        raise FeederError("invalid_address", "Alamat tanpa host")
    if p.username or p.password:
        raise FeederError("invalid_address", "Jangan menyisipkan kredensial di URL")
    if p.query or p.fragment:
        raise FeederError("invalid_address", "Alamat tidak boleh berisi query/fragmen")
    try:
        port = p.port
    except ValueError:
        raise FeederError("invalid_address", "Port tidak valid")
    netloc = p.hostname if port is None else f"{p.hostname}:{port}"
    if p.hostname.count(":") > 1:  # IPv6 literal
        netloc = f"[{p.hostname}]" + (f":{port}" if port is not None else "")
    path = (p.path or "").rstrip("/")
    if not path.endswith("/ws"):
        path += "/ws"
    return f"{p.scheme}://{netloc}{path}"


def axios0(params):
    """Serialisasi param gaya axios 0.x (pola POLA_SCRIPT.md)."""
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


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Token tidak boleh ikut ke host lain."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise FeederError(
                "connection",
                "Feeder mengalihkan ke alamat lain; permintaan dihentikan",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class FeederClient:
    """Klien HTTP dengan token per koneksi (bukan global modul)."""

    def __init__(self, base, token, timeout=DEFAULT_TIMEOUT):
        self.base = base
        self.token = token
        self.timeout = timeout
        # Koneksi Feeder/Tailscale harus langsung; jangan bocorkan token ke proxy env.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _SameHostRedirect)

    def _url(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(axios0(params))
        return url

    def request(self, method, path, payload=None, params=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self._url(path, params),
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "authorization": "Bearer " + self.token,
            },
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            kind = "auth" if e.code in (401, 403) else (
                "server" if e.code >= 500 else "http")
            raise FeederError(kind, f"Feeder menjawab HTTP {e.code}", e.code)
        except socket.timeout:
            raise FeederError("timeout", "Permintaan melewati batas waktu")
        except ssl.SSLError as e:
            raise FeederError("connection", f"TLS gagal: {e.reason}")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.timeout):
                raise FeederError("timeout", "Permintaan melewati batas waktu")
            raise FeederError("connection", f"Koneksi gagal: {reason}")
        try:
            return json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise FeederError("response", "Respons Feeder bukan JSON")

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, payload=None, params=None):
        return self.request("POST", path, payload=payload, params=params)

    def put(self, path, payload=None, params=None):
        return self.request("PUT", path, payload=payload, params=params)

    def delete(self, path, params=None):
        return self.request("DELETE", path, params=params)


def check_connection(client):
    """Validasi token. Kembalikan metadata koneksi atau raise FeederError."""
    data = client.get("/mhslulus/filter")
    if not isinstance(data, dict) or "list_status" not in data:
        raise FeederError("auth", "Token ditolak atau bukan sesi Feeder yang valid")
    periode = client.get("/history_pendidikan/periode_aktif")
    if not isinstance(periode, list) or not periode or "id_smt" not in periode[0]:
        raise FeederError("response", "Semester aktif tidak terbaca dari Feeder")
    flt = client.get("/kelas/filter")
    if not isinstance(flt, dict) or "list_semester" not in flt:
        raise FeederError("response", "Daftar semester tidak terbaca dari Feeder")
    return {
        "id_smt": periode[0]["id_smt"],
        "semesters": flt["list_semester"][0],
        "prodi": flt.get("list_prodi", [[]])[0],
    }


def page_rows(client, path, params):
    """GET index berbentuk [rows, count]; validasi bentuk dan jumlah."""
    data = client.get(path, params=params)
    if not (isinstance(data, list) and len(data) == 2 and isinstance(data[0], list)):
        raise FeederError("response", f"Respons {path} tidak berbentuk daftar")
    try:
        count = int(data[1])
    except (TypeError, ValueError):
        raise FeederError("response", f"Jumlah baris {path} tidak valid")
    return data[0], count


def fetch_all(client, path, sem, *, prodi_ids=None, extra_setfilter=None,
              column_search=None, sort_by="id_sms", cancel=None, progress=None):
    """Paginasi penuh 100 baris dengan validasi hitungan."""
    rows, page, total = [], 1, None
    while True:
        if cancel and cancel():
            raise Cancelled()
        setf = {"semester": [sem], "prodi": list(prodi_ids or [])}
        if extra_setfilter:
            setf.update(extra_setfilter)
        batch, count = page_rows(client, path, {
            "pagination": {"sortBy": sort_by, "descending": True, "page": page,
                           "rowsPerPage": 100, "rowsNumber": 0},
            "filter": "",
            "columnSearch": list(column_search or []),
            "setFilter": setf,
            "extendFilter": {"filter_sync": "semua"},
            "sortList": None,
        })
        total = count if total is None else total
        if count != total:
            raise FeederError("response", f"Jumlah baris {path} berubah saat paginasi")
        rows += batch
        if progress:
            progress(len(rows), total, path)
        if len(batch) < 100:
            break
        page += 1
    if len(rows) != total:
        raise FeederError("response", f"Paginasi {path} tidak lengkap")
    return rows


class Cancelled(Exception):
    """Pekerjaan dihentikan oleh pengguna."""


def check():
    """Self-check offline: normalisasi + serialisasi."""
    assert normalize_base("192.0.2.10:8100") == "http://192.0.2.10:8100/ws"
    assert normalize_base("https://feeder.web.ac.id") == "https://feeder.web.ac.id/ws"
    assert normalize_base("https://feeder.web.ac.id/ws") == "https://feeder.web.ac.id/ws"
    for bad in ("", "ftp://x", "http://user:pw@host", "http://"):
        try:
            normalize_base(bad)
            raise AssertionError("harus ditolak: " + repr(bad))
        except FeederError:
            pass
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlencode(
        axios0({"a": None, "b": True, "c": ["x"], "d": {"p": 1}}))))
    assert q["b"] == "true" and q["c[]"] == "x" and "a" not in q
    assert json.loads(q["d"]) == {"p": 1}
    print("check ok: feeder_client normalisasi + axios0")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        check()
    else:
        print(__doc__)
