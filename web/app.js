"use strict";

/* Automation Feeder Web — UI operator. Tanpa framework; fetch ke server lokal. */

const state = {
  base: "",
  activeSem: "",
  semesters: [],
  prodi: [],
  menu: "kelas",
  job: null,
  rows: [],
  columns: [],
  summary: {},
  notes: [],
  filterText: "",
  onlyIssues: false,
  sortKey: "",
  sortDir: "asc",
  page: 1,
  pageSize: 50,
  preview: null,
  previewRows: [],
  previewSeq: 0,
  selection: new Set(),
  timer: null,
  tableMenu: null,
};

const MENUS = [
  { id: "kelas", label: "Kelas perkuliahan", kind: "Audit + ekspor",
    desc: "Daftar kelas beserta dosen dan jumlah peserta.",
    fields: ["semester", "prodi", "kode"] },
  { id: "tm_sks", label: "Dosen, TM & SKS", kind: "Audit + perbaikan",
    desc: "Rincian penugasan dosen, total TM, dan total SKS per kelas.",
    fields: ["semester", "prodi", "kode", "kelas"], write: "tm_sks_fix" },
  { id: "nilai", label: "Nilai perkuliahan", kind: "Audit + ekspor",
    desc: "Kelengkapan penilaian tiap kelas.",
    fields: ["semester", "prodi", "kode"] },
  { id: "akm", label: "AKM", kind: "Audit + ubah biaya",
    desc: "Status mahasiswa, IPS/IPK/SKS, dan pembiayaan semester.",
    fields: ["semester", "prodi", "nim"], write: "akm_biaya" },
  { id: "aktivitas", label: "Aktivitas mahasiswa", kind: "Audit + ekspor",
    desc: "Kegiatan mahasiswa pada semester aktif.",
    fields: ["semester", "prodi", "cari"] },
  { id: "konversi", label: "Konversi nilai", kind: "Audit + isi nilai",
    desc: "Nilai konversi aktivitas dan anomali nilainya.",
    fields: ["semester", "prodi", "jenis", "nim"], write: "konversi_isi" },
  { id: "bobotnilai", label: "Skala nilai", kind: "Audit + ubah band",
    desc: "Rentang huruf/indeks bobot nilai per prodi.",
    fields: ["prodi"], write: "bobotnilai_edit" },
  { id: "mhslulus", label: "Mahasiswa lulus/keluar", kind: "Audit + tambah",
    desc: "Data kelulusan dan status keluar mahasiswa.",
    fields: ["tahun", "status", "prodi-nama", "nim"], write: "mhslulus_add" },
  { id: "ijazah", label: "Nomor ijazah", kind: "Pemantauan",
    desc: "Lulusan yang belum punya nomor ijazah (menunggu sinkronisasi PISN).",
    fields: ["tahun", "prodi-nama", "nim"] },
  { id: "mhs", label: "Mahasiswa", kind: "Audit + edit biodata + tambah",
    desc: "Daftar mahasiswa; audit kelengkapan biodata, edit per field, dan tambah mahasiswa baru.",
    fields: ["prodi", "status-mhs", "cari", "audit"],
    writes: ["mhs_edit", "mhs_add"] },
  { id: "dayatampung", label: "Daya tampung", kind: "Audit + ubah",
    desc: "Kuota per prodi per semester: target MHS baru, seleksi, dan tanggal perkuliahan.",
    fields: ["semester"], write: "dayatampung_edit" },
];

const STATUS_KELUAR = ["Lulus", "Mengajukan pengunduran diri", "Mutasi",
  "Dikeluarkan", "Putus Studi", "Meninggal dunia", "Selesai Pendidikan Non Gelar"];

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, children = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c) n.appendChild(c);
  return n;
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Requested-With": "feeder-web" },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const msg = (data && data.error && data.error.message) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.kind = data && data.error ? data.error.kind : "http";
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ---------- tema ---------- */

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("feeder-theme", theme); } catch (e) { /* privat */ }
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("feeder-theme"); } catch (e) { /* privat */ }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
  $("theme-toggle").addEventListener("click", () => {
    const now = document.documentElement.getAttribute("data-theme");
    applyTheme(now === "dark" ? "light" : "dark");
  });
}

/* ---------- koneksi ---------- */

async function connect(ev) {
  ev.preventDefault();
  const errBox = $("connect-error");
  errBox.hidden = true;
  const btn = $("connect-btn");
  btn.disabled = true;
  btn.textContent = "Menghubungkan...";
  try {
    const data = await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({ address: $("address").value, token: $("token").value }),
    });
    $("token").value = "";
    enterApp(data);
  } catch (e) {
    errBox.textContent = e.message;
    errBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Cek koneksi dan token";
  }
}

/* ---------- token .env ---------- */

async function initEnv() {
  try {
    const env = await api("/api/env");
    if (!env.available) return;
    $("env-box").hidden = false;
    if (env.address && !$("address").value) $("address").value = env.address;
    checkEnvToken();
  } catch (e) { /* .env tidak terbaca; pakai form manual */ }
}

async function checkEnvToken() {
  const box = $("env-status");
  box.textContent = "Mengecek token .env...";
  box.classList.remove("error");
  try {
    const r = await api("/api/env/check");
    if (r.valid) {
      box.textContent = `Token .env VALID — ${r.base}, semester ${r.nm_smt} `
        + `(${r.prodi.length} prodi).`;
    } else {
      box.classList.add("error");
      box.textContent = `Token .env TIDAK VALID: ${r.message}`;
    }
  } catch (e) {
    box.classList.add("error");
    box.textContent = `Gagal cek token .env: ${e.message}`;
  }
}

async function connectEnv() {
  const errBox = $("connect-error");
  errBox.hidden = true;
  const btn = $("connect-env-btn");
  btn.disabled = true;
  btn.textContent = "Menghubungkan...";
  try {
    const data = await api("/api/connect-env", { method: "POST" });
    enterApp(data);
  } catch (e) {
    errBox.textContent = e.message;
    errBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Hubungkan dengan token .env";
  }
}

function enterApp(data) {
  state.base = data.base;
  state.activeSem = data.active_sem;
  state.semesters = data.semesters || [];
  state.prodi = data.prodi || [];
  $("connect").hidden = true;
  $("app").hidden = false;
  $("conn-info").innerHTML = "";
  $("conn-info").append(
    el("div", {}, [
      el("div", { text: "Terhubung ke " }),
      el("strong", { text: data.base }),
    ]),
  );
  const semSel = $("semester");
  semSel.innerHTML = "";
  for (const s of state.semesters) {
    semSel.appendChild(el("option", { value: s.id_smt, text: s.nm_smt }));
  }
  semSel.value = state.activeSem;
  const prodiSel = $("prodi");
  prodiSel.innerHTML = "";
  prodiSel.appendChild(el("option", { value: "", text: "Semua prodi" }));
  for (const p of state.prodi) {
    prodiSel.appendChild(el("option", { value: p.id_sms, text: p.nm_lemb }));
  }
  renderNav();
  selectMenu("kelas");
}

async function resume() {
  try {
    const data = await api("/api/session");
    enterApp(data);
  } catch (e) {
    $("connect").hidden = false;
    $("app").hidden = true;
  }
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (e) { /* keluar lokal */ }
  stopPolling();
  state.job = null;
  state.preview = null;
  state.rows = [];
  $("app").hidden = true;
  $("connect").hidden = false;
  $("token").value = "";
}

function invalidatePreview() {
  state.previewSeq++;  // batalkan preview yang masih dalam perjalanan
  if (state.preview) closePreview();
}

/* ---------- navigasi ---------- */

function renderNav() {
  const nav = $("nav");
  nav.innerHTML = "";
  for (const m of MENUS) {
    const btn = el("button", {
      type: "button",
      onclick: () => selectMenu(m.id),
      "data-menu": m.id,
    }, [
      el("span", { class: "nav-label", text: m.label }),
      el("span", { class: "nav-kind", text: m.kind }),
    ]);
    nav.appendChild(btn);
  }
}

function currentMenu() { return MENUS.find((m) => m.id === state.menu); }

function selectMenu(id) {
  state.menu = id;
  state.rows = [];
  state.columns = [];
  state.summary = {};
  state.notes = [];
  state.page = 1;
  state.filterText = "";
  state.onlyIssues = false;
  state.sortKey = "";
  state.tableMenu = null;
  closePreview();
  stopPolling();
  state.job = null;
  for (const b of $("nav").querySelectorAll("button")) {
    b.setAttribute("aria-current", String(b.dataset.menu === id));
  }
  const m = currentMenu();
  $("panel-title").textContent = m.label;
  $("panel-desc").textContent = m.desc;
  renderFilters(m);
  renderActions(m);
  showState("Pilih filter lalu tekan Tarik data.");
  $("table-wrap").hidden = true;
  $("resultbar").hidden = true;
  $("export-all").hidden = true;
  $("export-filtered").hidden = true;
  $("jobbar").hidden = true;
}

function writesOf(m) {
  return m.writes || (m.write ? [m.write] : []);
}

const WRITE_LABELS = {
  mhs_edit: "Edit biodata",
  mhs_add: "Tambah mahasiswa",
};

function renderActions(m) {
  const box = $("panel-actions");
  box.innerHTML = "";
  const exportAll = $("export-all");
  const exportFiltered = $("export-filtered");
  exportAll.hidden = true;
  exportFiltered.hidden = true;
  const writes = writesOf(m);
  if (writes.length > 1) {
    for (const w of writes) {
      const wrap = el("span", { class: "write-group" }, [
        el("span", { class: "write-label", text: WRITE_LABELS[w] || w }),
        el("button", {
          type: "button", class: "btn ghost", text: "Unduh template",
          onclick: () => downloadTemplate(w),
        }),
        el("button", {
          type: "button", class: "btn ghost", text: "Unggah CSV",
          onclick: () => openImport(w),
        }),
      ]);
      box.appendChild(wrap);
    }
    return;
  }
  if (writes.length === 1) {
    const w = writes[0];
    if (w === "konversi_isi") {
      box.appendChild(el("p", { class: "hint action-hint",
        text: "Opsional: isi Jenis aktivitas di bawah (mis. Skripsi) sebelum unggah CSV." }));
    }
    box.appendChild(el("button", {
      type: "button", class: "btn ghost", text: "Unduh template CSV",
      onclick: () => downloadTemplate(w),
    }));
    box.appendChild(el("button", {
      type: "button", class: "btn ghost", text: "Unggah CSV / preview perubahan",
      onclick: () => openImport(w),
    }));
  }
}

/* ---------- filter ---------- */

function renderFilters(m) {
  const form = $("filters");
  form.innerHTML = "";
  const add = (label, input, grow) => {
    if (!input.id) input.id = "filter-" + Math.random().toString(16).slice(2);
    if (!input.getAttribute("aria-label")) input.setAttribute("aria-label", label);
    const f = el("label", { class: "field" + (grow ? " grow" : ""), for: input.id }, [el("span", { text: label }), input]);
    form.appendChild(f);
  };
  for (const f of m.fields) {
    if (f === "semester" || f === "prodi") {
      continue; // select global di topbar
    } else if (f === "prodi-nama") {
      const sel = el("select", { id: "f-prodi-nama" });
      sel.appendChild(el("option", { value: "", text: "Semua prodi" }));
      for (const p of state.prodi) {
        sel.appendChild(el("option", { value: p.nm_lemb, text: p.nm_lemb }));
      }
      add("Prodi", sel);
    } else if (f === "kode") {
      add("Kode MK (opsional)", el("input", { id: "f-kode", placeholder: "mis. SF23667" }));
    } else if (f === "kelas") {
      add("Kelas (opsional)", el("input", { id: "f-kelas", placeholder: "A / B" }));
    } else if (f === "nim") {
      add("NIM (opsional)", el("input", { id: "f-nim", placeholder: "mis. 202500001" }));
    } else if (f === "cari") {
      const label = m.id === "mhs" ? "Cari nama/NIM (opsional)" : "Cari judul/SK (opsional)";
      add(label, el("input", { id: "f-cari" }), true);
    } else if (f === "jenis") {
      add("Jenis aktivitas (opsional)", el("input", { id: "f-jenis", placeholder: "mis. Skripsi" }));
    } else if (f === "tahun") {
      add("Tahun keluar", el("input", { id: "f-tahun", value: "2026", inputmode: "numeric" }));
    } else if (f === "status") {
      const sel = el("select", { id: "f-status" });
      sel.appendChild(el("option", { value: "Lulus", text: "Lulus" }));
      for (const s of STATUS_KELUAR) if (s !== "Lulus") sel.appendChild(el("option", { value: s, text: s }));
      sel.appendChild(el("option", { value: "", text: "Semua status" }));
      add("Status keluar", sel);
    } else if (f === "status-mhs") {
      add("Status (opsional)", el("input", { id: "f-status-mhs", placeholder: "mis. Aktif" }));
    } else if (f === "audit") {
      const lab = el("label", { class: "check", for: "f-audit" }, [
        el("input", { id: "f-audit", type: "checkbox" }),
        el("span", { text: "Audit biodata (lambat)" }),
      ]);
      form.appendChild(lab);
    }
  }
  const actions = el("div", { class: "filters-actions" }, [
    el("button", { type: "button", class: "btn", text: "Tarik data", onclick: startAudit }),
  ]);
  form.appendChild(actions);
}

function filterParams() {
  const m = currentMenu();
  const p = {};
  if (m.fields.includes("semester") && m.id !== "mhslulus" && m.id !== "ijazah") {
    p.semester = $("semester").value;
  }
  if (m.fields.includes("prodi")) p.prodi = $("prodi").value;
  if (m.fields.includes("prodi-nama") && $("f-prodi-nama")) p.prodi = $("f-prodi-nama").value;
  for (const [id, key] of [["f-kode", "kode"], ["f-kelas", "kelas"], ["f-nim", "nim"],
    ["f-cari", "cari"], ["f-jenis", "jenis"], ["f-tahun", "tahun"], ["f-status", "status"],
    ["f-status-mhs", "status"]]) {
    const node = $(id);
    if (node) p[key] = node.value.trim();
  }
  const auditBox = $("f-audit");
  if (auditBox) p.audit = auditBox.checked;
  return p;
}

/* ---------- audit job ---------- */

async function startAudit() {
  closePreview();
  showState("Menarik data dari Feeder...");
  $("table-wrap").hidden = true;
  $("resultbar").hidden = true;
  $("jobbar").hidden = false;
  try {
    const { job_id } = await api("/api/audit", {
      method: "POST",
      body: JSON.stringify({ menu: state.menu, params: filterParams() }),
    });
    state.job = job_id;
    pollJob(job_id);
  } catch (e) {
    $("jobbar").hidden = true;
    showState(e.message, true);
  }
}

function pollJob(jobId) {
  stopPolling();
  const tick = async () => {
    let job;
    try {
      job = await api("/api/jobs/" + jobId);
    } catch (e) {
      stopPolling();
      $("jobbar").hidden = true;
      showState(e.message, true);
      return;
    }
    const bar = $("job-progress");
    const total = job.progress.total || 0;
    bar.value = total ? Math.round((job.progress.done / total) * 100) : 0;
    $("job-label").textContent = `${job.progress.label} (${job.progress.done}/${total || "?"})`;
    if (job.status === "done") {
      stopPolling();
      $("jobbar").hidden = true;
      showResult(job.result);
    } else if (job.status === "failed") {
      stopPolling();
      $("jobbar").hidden = true;
      showState((job.error && job.error.message) || "Pekerjaan gagal", true);
    } else if (job.status === "cancelled") {
      stopPolling();
      $("jobbar").hidden = true;
      showState("Pekerjaan dibatalkan.");
    }
  };
  state.timer = setInterval(tick, 600);
  tick();
}

function stopPolling() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
}

/* ---------- hasil tabel ---------- */

function renderResult(result) {
  if (!result) { showState("Hasil kosong.", true); return; }
  state.rows = result.rows || [];
  state.columns = result.columns || [];
  state.summary = result.summary || {};
  state.notes = result.notes || [];
  state.page = 1;
  state.sortKey = "";
  if (!state.rows.length) {
    renderSummary();
    $("resultbar").hidden = false;
    showState("Tidak ada baris yang cocok dengan filter.");
    $("table-wrap").hidden = true;
    $("export-all").hidden = true;
    $("export-filtered").hidden = true;
    return;
  }
  showState(null);
  $("resultbar").hidden = false;
  $("export-all").hidden = false;
  $("export-filtered").hidden = false;
  $("table-wrap").hidden = false;
  renderSummary();
  renderTable();
}

function renderSummary() {
  const box = $("summary");
  box.innerHTML = "";
  const s = state.summary;
  const items = [];
  const known = {
    total: "Total", tanpa_dosen: "Tanpa dosen", belum_sync: "Belum sync",
    sesuai: "Sesuai", anomali: "Anomali", dinilai_kurang: "Nilai kurang",
    gagal_baca: "Gagal baca", biaya_kosong: "Biaya kosong", ips_sks_nol: "IPS/SKS 0",
    sk_kosong: "SK kosong", aktivitas: "Aktivitas", baris: "Baris",
    peserta_gagal: "Peserta gagal dibaca",
    peserta_0: "Peserta 0", ok: "Berhasil", fail: "Gagal",
    konflik: "Konflik", unknown: "Belum pasti",
    aktivitas_tanpa_anggota: "Aktivitas tanpa anggota", anggota_tanpa_nilai: "Anggota tanpa nilai",
    lulus: "Lulus", bukan_lulus: "Bukan lulus", lulus_tanpa_ijazah: "Lulus tanpa no. ijazah",
  };
  for (const [k, v] of Object.entries(s)) {
    const label = known[k] || k;
    const bad = ["tanpa_dosen", "belum_sync", "anomali", "dinilai_kurang", "gagal_baca",
      "biaya_kosong", "ips_sks_nol", "sk_kosong", "aktivitas_tanpa_anggota",
      "anggota_tanpa_nilai", "lulus_tanpa_ijazah", "bukan_lulus",
      "peserta_gagal", "peserta_0", "fail", "konflik", "unknown"].includes(k) && Number(v) > 0;
    const good = ["sesuai", "lulus"].includes(k);
    items.push(el("span", { class: "chip" + (bad ? " bad" : good ? " good" : "") }, [
      el("span", { text: label }), el("b", { text: String(v) }),
    ]));
  }
  for (const it of items) box.appendChild(it);
  for (const note of state.notes) {
    box.appendChild(el("span", { class: "chip warn", text: note }));
  }
}

function visibleRows() {
  let rows = state.rows.slice();
  if (state.onlyIssues) {
    rows = rows.filter((r) => (r.anomali && r.anomali.length) || (r.status && r.status !== "bernilai" && r.status !== "ok"));
  }
  const q = state.filterText.trim().toLowerCase();
  if (q) {
    rows = rows.filter((r) => Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(q)));
  }
  if (state.sortKey) {
    const dir = state.sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      const av = a[state.sortKey], bv = b[state.sortKey];
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn) && av !== "" && bv !== "") return (an - bn) * dir;
      return String(av ?? "").localeCompare(String(bv ?? ""), "id") * dir;
    });
  }
  return rows;
}

function renderTable() {
  const cols = state.columns;
  const rows = visibleRows();
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  if (state.page > pages) state.page = pages;
  const start = (state.page - 1) * state.pageSize;
  const pageRows = rows.slice(start, start + state.pageSize);

  const thead = $("table").querySelector("thead");
  const tbody = $("table").querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";
  const tr = el("tr");
  for (const [key, label] of cols) {
    const th = el("th", { text: label, "data-sort": state.sortKey === key ? state.sortDir : "" });
    th.addEventListener("click", () => {
      if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else { state.sortKey = key; state.sortDir = "asc"; }
      renderTable();
    });
    tr.appendChild(th);
  }
  thead.appendChild(tr);

  for (const row of pageRows) {
    const issues = row.anomali && row.anomali.length;
    const tr2 = el("tr", { class: issues ? "issue" : "" });
    for (const [key] of cols) {
      const v = row[key];
      if (key === "anomali") {
        const td = el("td");
        if (v && v.length) for (const a of v) td.appendChild(el("span", { class: "tag bad", text: a }));
        else td.appendChild(el("span", { class: "tag good", text: "ok" }));
        tr2.appendChild(td);
      } else if (key === "status") {
        const td = el("td");
        const cls = v === "bernilai" || v === "ok" ? "good" : v === "tanpa anggota" ? "bad" : "";
        td.appendChild(el("span", { class: "tag " + cls, text: v ?? "" }));
        tr2.appendChild(td);
      } else if (key === "no_seri_ijazah") {
        tr2.appendChild(el("td", { class: "mono", text: v || "belum ada" }));
      } else if (key === "nipd" || key === "kode_mk") {
        tr2.appendChild(el("td", { class: "mono", text: v ?? "" }));
      } else {
        tr2.appendChild(el("td", { text: v === null || v === undefined ? "" : String(v) }));
      }
    }
    if (row.dosen && row.dosen.length) {
      tr2.dataset.detail = "true";
      tr2.title = "Klik untuk melihat rincian dosen";
      tr2.addEventListener("click", () => toggleDetail(tr2, row));
    }
    tbody.appendChild(tr2);
  }

  $("table-count").textContent = `${rows.length} baris`;
  renderPager(pages, rows.length);
}

function toggleDetail(tr, row) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("rowdetail")) { next.remove(); return; }
  const td = el("td", { colspan: String(state.columns.length) });
  const t = el("table");
  t.appendChild(el("thead", {}, [el("tr", {}, [
    el("th", { text: "Dosen" }), el("th", { text: "TM rencana" }),
    el("th", { text: "TM realisasi" }), el("th", { text: "SKS" }),
  ])]));
  const tb = el("tbody");
  for (const d of row.dosen) {
    tb.appendChild(el("tr", {}, [
      el("td", { text: d.nm_sdm }), el("td", { class: "num", text: d.jml_tm_renc }),
      el("td", { class: "num", text: d.jml_tm_real }), el("td", { class: "num", text: d.sks_subst_tot }),
    ]));
  }
  t.appendChild(tb);
  td.appendChild(t);
  const detailTr = el("tr", { class: "rowdetail" }, [td]);
  tr.after(detailTr);
}

function renderPager(pages, count) {
  const p = $("pager");
  p.innerHTML = "";
  p.appendChild(el("span", { text: `Halaman ${state.page} dari ${pages} (${count} baris)` }));
  const mk = (label, delta, disabled) => {
    const b = el("button", { type: "button", class: "btn ghost small", text: label });
    b.disabled = disabled;
    b.addEventListener("click", () => { state.page += delta; renderTable(); });
    return b;
  };
  p.appendChild(mk("Awal", -(state.page - 1), state.page === 1));
  p.appendChild(mk("Sebelumnya", -1, state.page === 1));
  p.appendChild(mk("Berikutnya", 1, state.page === pages));
  p.appendChild(mk("Akhir", pages - state.page, state.page === pages));
}

function showState(message, isError) {
  const box = $("states");
  box.innerHTML = "";
  if (message) box.appendChild(el("p", { class: "state" + (isError ? " error" : ""), text: message }));
}

/* ---------- ekspor ---------- */

function exportUrl(menu, jobId) {
  return `/api/export?menu=${encodeURIComponent(menu)}&job_id=${encodeURIComponent(jobId)}`;
}

async function downloadExport(filtered) {
  if (!state.job) return;
  const menu = state.tableMenu || state.menu;
  if (filtered) {
    // ekspor hasil terfilter: minta server membuat CSV dari hasil yang tersaring di klien
    const rows = visibleRows();
    const cols = state.columns;
    const csv = toCsv(cols.map((c) => c[0]), rows);
    saveBlob(csv, `feeder_${menu}_terfilter.csv`);
    return;
  }
  const a = document.createElement("a");
  a.href = exportUrl(menu, state.job);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function toCsv(keys, rows) {
  const esc = (v) => {
    let s = v === null || v === undefined ? "" : (Array.isArray(v) ? v.join("; ") : String(v));
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [keys.join(",")];
  for (const r of rows) lines.push(keys.map((k) => esc(r[k])).join(","));
  return "\ufeff" + lines.join("\n") + "\n";
}

function saveBlob(text, filename) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function downloadTemplate(menu) {
  try {
    const res = await fetch(`/api/template?menu=${encodeURIComponent(menu)}`, {
      headers: { "X-Requested-With": "feeder-web" },
    });
    if (!res.ok) throw new Error("Template tidak tersedia");
    saveBlob(await res.text(), `template_${menu}.csv`);
  } catch (e) {
    showState(e.message, true);
  }
}

/* ---------- impor + preview ---------- */

function openImport(menu) {
  const input = el("input", { type: "file", accept: ".csv,text/csv", hidden: "hidden" });
  document.body.appendChild(input);
  input.addEventListener("change", async () => {
    const file = input.files[0];
    input.remove();
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { showState("Berkas CSV terlalu besar (maks 2 MB).", true); return; }
    const text = await file.text();
    const rows = parseCsv(text);
    if (!rows.length) { showState("CSV kosong atau tidak terbaca.", true); return; }
    requestPreview(menu, rows);
  });
  input.click();
}

function parseCsv(text) {
  text = text.replace(/^\ufeff/, "");
  const lines = [];
  let row = [], cell = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; }
        else inQ = false;
      } else cell += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n") { row.push(cell); lines.push(row); row = []; cell = ""; }
    else if (c !== "\r") cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); lines.push(row); }
  if (!lines.length) return [];
  const header = lines[0].map((h) => h.trim().toLowerCase());
  const out = [];
  for (const line of lines.slice(1)) {
    if (!line.some((v) => v.trim() !== "")) continue;
    const obj = {};
    header.forEach((h, i) => { obj[h] = (line[i] ?? "").trim(); });
    out.push(obj);
  }
  return out;
}

async function requestPreview(menu, rows) {
  closePreview();
  showState("Membuat preview (hanya membaca Feeder)...");
  $("jobbar").hidden = false;
  const seq = ++state.previewSeq;
  try {
    const data = await api("/api/preview", {
      method: "POST",
      body: JSON.stringify({ menu, form: {
        rows,
        semester: $("semester") ? $("semester").value : state.activeSem,
        prodi: $("prodi") ? $("prodi").value : "",
        jenis: menu === "konversi_isi" && $("f-jenis") ? $("f-jenis").value.trim() : "",
      } }),
    });
    if (seq !== state.previewSeq) return; // menu/filter berubah saat menunggu
    renderPreview(menu, data);
  } catch (e) {
    if (seq !== state.previewSeq) return;
    showState(e.message, true);
  } finally {
    if (seq === state.previewSeq) $("jobbar").hidden = true;
  }
}

function renderPreview(menu, data) {
  state.preview = { menu, id: data.preview_id };
  state.previewRows = data.items || [];
  state.selection = new Set();
  const box = $("preview");
  box.hidden = false;
  const ready = data.summary.siap ?? data.summary.perlu_perbaikan ?? (data.items || []).length;
  $("preview-summary").textContent =
    `${ready} baris siap, ${data.summary.error || 0} baris error. ` +
    "Periksa sebelum menerapkan.";
  const thead = $("preview-table").querySelector("thead");
  const tbody = $("preview-table").querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";
  const labels = previewLabels(menu);
  thead.appendChild(el("tr", {}, [el("th", { text: "Pilih" }),
    el("th", { text: "Target" }), ...labels.map(([, l]) => el("th", { text: l }))]));

  for (const it of state.previewRows) {
    const tr = el("tr");
    const cb = el("input", { type: "checkbox" });
    cb.addEventListener("change", () => {
      if (cb.checked) state.selection.add(it.id);
      else state.selection.delete(it.id);
      updateApplyButton();
    });
    tr.appendChild(el("td", {}, [cb]));
    tr.appendChild(el("td", { text: targetLabel(it) }));
    for (const [key] of labels) {
      const td = el("td");
      const after = it.after ? it.after[key] : undefined;
      const before = it.before ? it.before[key] : undefined;
      if (key === "changes") {
        for (const ch of it.changes || []) {
          td.appendChild(el("div", {}, [
            el("strong", { text: ch.nm_sdm }),
            el("span", { text: " " }),
            el("span", { class: "delta-before", text: `${ch.before.jml_tm_renc}/${ch.before.jml_tm_real} ${ch.before.sks_subst_tot}` }),
            el("span", { text: " → " }),
            el("span", { class: "delta-after", text: `${ch.after.jml_tm_renc}/${ch.after.jml_tm_real} ${ch.after.sks_subst_tot}` }),
          ]));
        }
        if (!(it.changes || []).length) td.textContent = "tidak ada perubahan";
      } else if (after !== undefined) {
        if (before !== undefined && String(before) !== String(after)) {
          td.appendChild(el("span", { class: "delta-before", text: String(before || "-") }));
          td.appendChild(el("span", { text: " → " }));
          td.appendChild(el("span", { class: "delta-after", text: String(after) }));
        } else {
          td.textContent = String(after);
        }
      } else {
        td.textContent = "-";
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  for (const it of data.errors || []) {
    const tr = el("tr", { class: "issue" });
    tr.appendChild(el("td", { text: "!" }));
    tr.appendChild(el("td", { text: targetLabel(it) }));
    const td = el("td", { colspan: String(labels.length) });
    for (const e of it.errors) td.appendChild(el("span", { class: "tag bad", text: e }));
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  updateApplyButton();
  box.scrollIntoView({ block: "nearest" });
}

function previewLabels(menu) {
  if (menu === "mhslulus_add") return [["jenis_keluar", "Jenis"], ["periode", "Periode"],
    ["tgl_keluar", "Tgl keluar"], ["no_sk", "No. SK"], ["ipk", "IPK"]];
  if (menu === "akm_biaya") return [["biaya", "Biaya"]];
  if (menu === "kelas_edit") return [["nilai", "Nilai baru"]];
  if (menu === "tm_sks_fix") return [["changes", "Perubahan TM/SKS"]];
  if (menu === "konversi_isi") return [["nilai", "Nilai"], ["huruf", "Huruf"],
    ["indeks", "Indeks"]];
  if (menu === "bobotnilai_edit") return [["nilai", "Nilai baru"]];
  if (menu === "mhs_edit") return [["nilai", "Nilai baru"]];
  if (menu === "mhs_add") return [["prodi", "Prodi"], ["periode", "Periode"],
    ["tgl_masuk", "Tgl masuk"], ["pembiayaan", "Pembiayaan"]];
  if (menu === "dayatampung_edit") return [["nilai", "Nilai baru"]];
  return [];
}

function targetLabel(it) {
  const t = it.target || {};
  const semester = t.nm_smt || t.semester;
  return [t.nim, t.nama, t.kode_mk, t.kelas, t.matkul, t.prodi, t.huruf,
    t.field, semester].filter(Boolean).join(" | ") || "-";
}

function updateApplyButton() {
  const btn = $("apply-selected");
  btn.disabled = state.selection.size === 0;
  btn.textContent = state.selection.size
    ? `Terapkan ${state.selection.size} perubahan terpilih`
    : "Terapkan perubahan terpilih";
}

function closePreview() {
  state.preview = null;
  state.previewRows = [];
  state.selection = new Set();
  $("preview").hidden = true;
}

async function applySelected() {
  if (!state.preview || !state.selection.size) return;
  const btn = $("apply-selected");
  btn.disabled = true;
  const ok = window.confirm(
    `Terapkan ${state.selection.size} perubahan ke Feeder? ` +
    "Pastikan preview sudah sesuai.");
  if (!ok) { updateApplyButton(); return; }
  try {
    const data = await api("/api/apply", {
      method: "POST",
      body: JSON.stringify({
        preview_id: state.preview.id,
        item_ids: [...state.selection],
      }),
    });
    closePreview();
    showState("Menulis perubahan...");
    $("jobbar").hidden = false;
    state.job = data.job_id;
    pollJob(data.job_id);
  } catch (e) {
    showState(e.message, true);
    updateApplyButton();
  }
}

/* hasil apply ditampilkan sebagai tabel */
function showResult(result) {
  if (result && result.results) {
    state.rows = result.results.map((r) => ({
      status: r.status,
      target: targetLabel({ target: r.target }),
      pesan: r.message,
    }));
    state.columns = [["status", "Status"], ["target", "Target"], ["pesan", "Pesan"]];
    state.summary = result.summary || {};
    state.notes = [];
    state.page = 1;
    state.sortKey = "";
    $("resultbar").hidden = false;
    $("table-wrap").hidden = false;
    $("export-all").hidden = true;
    $("export-filtered").hidden = false;
    renderSummary();
    renderTable();
    showState(null);
    return;
  }
  renderResult(result);
}

/* ---------- wiring ---------- */

function wire() {
  initTheme();
  $("connect-form").addEventListener("submit", connect);
  $("connect-env-btn").addEventListener("click", connectEnv);
  $("check-env-btn").addEventListener("click", checkEnvToken);
  $("logout").addEventListener("click", logout);
  $("export-all").addEventListener("click", () => downloadExport(false));
  $("export-filtered").addEventListener("click", () => downloadExport(true));
  $("apply-selected").addEventListener("click", applySelected);
  $("preview-close").addEventListener("click", closePreview);
  $("job-cancel").addEventListener("click", async () => {
    if (!state.job) return;
    try { await api(`/api/jobs/${state.job}/cancel`, { method: "POST" }); } catch (e) { /* sudah selesai */ }
  });
  $("table-search").addEventListener("input", (e) => {
    state.filterText = e.target.value;
    state.page = 1;
    renderTable();
  });
  $("only-issues").addEventListener("change", (e) => {
    state.onlyIssues = e.target.checked;
    state.page = 1;
    renderTable();
  });
  $("semester").addEventListener("change", invalidatePreview);
  $("prodi").addEventListener("change", invalidatePreview);
  $("filters").addEventListener("input", invalidatePreview);
  initEnv();
  resume();
}

wire();
