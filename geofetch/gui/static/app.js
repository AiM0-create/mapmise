"use strict";
// geofetch GUI — a view over the engine. All decisions happen server-side; this file only shows them.

const $ = (id) => document.getElementById(id);
const state = { prep: null, project: null, job: null, use: {}, skip: [], aoiUpload: null, maps: {} };

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Geofetch-Token": window.GF_TOKEN },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: "The app did not answer." }));
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const size = (b) => (b >= 5e7 ? (b / 1e9).toFixed(2) + " GB" : Math.round(b / 1e6) + " MB");
const BADGE = { ok: ["b-ok", "Ready"], composite: ["b-composite", "Cloudy"], infeasible: ["b-infeasible", "Infeasible"],
  incomplete: ["b-incomplete", "Partial"], skipped: ["b-skipped", "Not possible"], uncovered: ["b-uncovered", "Not covered"] };
const badge = (k, text) => `<span class="badge ${BADGE[k][0]}">${esc(text || BADGE[k][1])}</span>`;

function show(screen) {
  document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.dataset.screen !== screen));
  document.querySelectorAll("#steps button").forEach((b) => b.classList.toggle("on", b.dataset.screen === screen));
  Object.values(state.maps).forEach((m) => setTimeout(() => m.invalidateSize(), 50));
}
document.querySelectorAll("#steps button").forEach((b) => (b.onclick = () => { show(b.dataset.screen); if (b.dataset.screen === "library") loadLibrary(); }));

function drawAoi(id, geojson) {
  if (!state.maps[id]) {
    const m = L.map(id, { zoomControl: true, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(m);
    state.maps[id] = m;
    m._aoi = null;
  }
  const m = state.maps[id];
  if (m._aoi) m.removeLayer(m._aoi);
  m._aoi = L.geoJSON(geojson, { style: { color: "#185fa5", weight: 2, fillOpacity: 0.12 } }).addTo(m);
  setTimeout(() => { m.invalidateSize(); m.fitBounds(m._aoi.getBounds(), { padding: [12, 12] }); }, 60);
}

// ------------------------------------------------------------ 1. Ask

async function loadProjects() {
  try {
    const d = await api("/api/projects");
    const sel = $("opt-project");
    sel.innerHTML = '<option value="">New project</option>' + d.projects.map((p) => `<option value="${esc(p.path)}">${esc(p.name)} · ${esc(p.start)} → ${esc(p.end)}</option>`).join("");
    $("recent-card").hidden = d.projects.length === 0;
    $("recent").innerHTML = d.projects.map((p) => `<div class="recent-item" data-path="${esc(p.path)}"><span>${esc(p.name)} <span class="muted small">· ${esc(p.start)} → ${esc(p.end)} · ${Math.round(p.area_km2).toLocaleString()} km²</span></span><span class="muted small">${esc(p.last_ask || "")}</span></div>`).join("");
    document.querySelectorAll(".recent-item").forEach((el) => (el.onclick = () => openProject(el.dataset.path)));
  } catch (e) { /* listing is a convenience */ }
}

$("opt-aoi").onchange = async (ev) => {
  const f = ev.target.files[0];
  state.aoiUpload = null;
  if (!f) return;
  try { state.aoiUpload = { name: f.name, geojson: JSON.parse(await f.text()) }; }
  catch { showError("ask-error", "That file is not valid GeoJSON."); ev.target.value = ""; }
};

function showNote(id, msg) { const el = $(id); if (el) { el.textContent = msg; el.hidden = !msg; } }
function showError(id, msg) { const el = $(id); el.textContent = msg; el.hidden = !msg; }
$("ask-text").addEventListener("input", () => showError("ask-error", ""));
$("ask-text").addEventListener("keydown", (e) => { if (e.key === "Enter") plan(); });
$("plan-btn").onclick = () => { state.use = {}; state.skip = []; plan(); };

async function plan() {
  const text = $("ask-text").value.trim();
  if (!text) return showError("ask-error", "Describe what you want to analyse first.");
  showError("ask-error", ""); showError("plan-error", "");
  const btn = $("plan-btn"); btn.disabled = true; btn.textContent = "Planning…";
  showNote("ask-note", "Finding the place, searching the catalogues and checking clouds and coverage. The first plan for a new area can take up to a minute.");
  try {
    const body = { text, place: $("opt-place").value, event: $("opt-event").value, start: $("opt-start").value, end: $("opt-end").value,
      mode: $("opt-mode").value, project: state.prep?.project && state.replan ? state.prep.project : $("opt-project").value,
      use: state.use, skip: state.skip };
    if (state.aoiUpload && !body.project) { body.aoi_geojson = state.aoiUpload.geojson; body.aoi_name = state.aoiUpload.name; }
    state.prep = await api("/api/prepare", body);
    state.replan = false;
    show("plan");      // make the map container visible before drawing into it
    renderPlan();
  } catch (e) {
    showError(state.replan ? "plan-error" : "ask-error", e.message);
  } finally { btn.disabled = false; btn.textContent = "Plan"; state.replan = false; showNote("ask-note", ""); }
}

// ------------------------------------------------------------ 2. Plan

function renderPlan() {
  const r = state.prep;
  $("understood").innerHTML = `
    <p class="small" style="margin:0 0 8px"><strong>${esc(r.place.split("\n")[0])}</strong><br><span class="muted">${Math.round(r.area_km2).toLocaleString()} km² · ${esc(r.country || "")} · EPSG:${r.epsg}</span></p>
    <p class="small" style="margin:0 0 8px">${esc(r.start)} → ${esc(r.end)} <span class="muted">(${esc(r.period_source)})</span></p>
    <p class="small" style="margin:0 0 4px">${r.n_needs} data needs, because:</p>
    ${r.rules.map((id) => `<p class="small" style="margin:0 0 2px">· <strong>${esc(id)}</strong> <span class="muted">${esc(r.how[id] === "keyword" ? "word in your question" : r.how[id].replace(/^meaning: /, "built-in AI: "))}</span></p>`).join("")}
    ${r.ai !== "on" ? `<p class="small muted" style="margin:4px 0 0">Built-in AI ${esc(r.ai)} — keyword rules only.</p>` : ""}
    ${r.event_note ? `<p class="small" style="margin:8px 0 0;color:var(--warn)">${esc(r.event_note)}</p>` : ""}
    ${r.notes.length ? `<p class="small muted" style="margin:8px 0 0">${r.notes.map(esc).join("<br>")}</p>` : ""}
    ${r.library_here.length ? `<p class="small" style="margin:8px 0 0">Already in your library over this area:</p>${r.library_here.map((g) => `<p class="small muted" style="margin:0">· ${esc(g.source)} — ${g.files} files${g.dates.length ? ` (${esc(g.dates.join(" → "))})` : ""} in ${esc(g.project)}</p>`).join("")}` : ""}
    <p class="muted small" style="margin:8px 0 0">Project: ${esc(r.project)}</p>`;
  drawAoi("map", r.aoi);

  $("plan-rows").innerHTML = r.plans.map((p, i) => {
    const needs = p.needs.map((n) => `${n.theme} <span class="muted">(${n.temporal === "pair" ? "before/after" : n.temporal})</span>`).join(", ");
    const tag = p.fallback_for ? '<span class="tag">added: cloud fallback</span>' : p.complement_for ? '<span class="tag">added: fills missing years</span>' : "";
    const opts = [p.source, ...p.alternatives].map((s) => `<option ${s === p.source ? "selected" : ""}>${esc(s)}</option>`).join("");
    const have = p.files && p.present === p.files;
    const lib = !have && p.files && p.present + p.from_library === p.files;
    const sz = have ? "have" : lib ? "0 MB" : p.size_known ? (p.unknown_sizes ? "≥" : "") + size(p.bytes) : "live";
    const skipped = p.worst === "skipped";
    return `<div class="prow" data-i="${i}">
      <input type="checkbox" ${skipped ? "disabled" : "checked"} aria-label="Include ${esc(p.source)}">
      <span class="need" title="${esc(p.needs.map((n) => n.why).join(" · "))}">${needs}<small>${esc(p.needs[0]?.priority || "")} ${tag}</small></span>
      <select data-need="${esc(p.need_key)}" ${p.alternatives.length ? "" : "disabled"} aria-label="Source">${opts}</select>
      <span>${p.present ? `${p.present}/${p.files}` : p.files}</span><span>${sz}</span><span>${have ? badge("ok", "Already have") : lib ? badge("ok", "In your library") : badge(p.worst)}</span>
      <div class="detail" hidden>
        <strong>${esc(p.source_name)}</strong> · ${esc(p.licence)}${p.resolution_m ? ` · ${p.resolution_m} m` : ""}
        <ul>${p.needs.map((n) => `<li>${esc(n.why)}</li>`).join("")}${p.explanations.slice(0, 2).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <ul>${p.windows.slice(0, 8).map((w) => `<li>${esc(w.label)}: ${esc(w.text)}</li>`).join("")}${p.windows.length > 8 ? `<li>… ${p.windows.length - 8} more periods</li>` : ""}</ul>
      </div></div>`;
  }).join("");

  document.querySelectorAll(".prow .need").forEach((el) => (el.onclick = () => { const d = el.parentElement.querySelector(".detail"); d.hidden = !d.hidden; }));
  document.querySelectorAll(".prow input").forEach((cb) => (cb.onchange = total));
  document.querySelectorAll(".prow select").forEach((sel) => (sel.onchange = () => {
    state.use[sel.dataset.need] = sel.value; state.replan = true; $("total-sub").textContent = "· re-planning with " + sel.value + "…"; plan();
  }));
  total();

  const att = r.attention;
  $("attention-card").hidden = att.length === 0;
  $("attention").innerHTML = att.map((a) => `<p class="att">${badge(a.level === "uncovered" ? "uncovered" : a.level)} <strong>${esc(a.source)}</strong> ${esc(a.text)}</p>`).join("");
  $("unmet-card").hidden = r.unmet.length === 0;
  $("unmet").innerHTML = r.unmet.map((u) => `<p class="att">${badge("skipped", "Unmet")} <strong>${esc(u.theme)}</strong> ${esc(u.why)}</p>`).join("");
}

function checkedPlans() {
  return [...document.querySelectorAll(".prow")].filter((row) => row.querySelector("input").checked).map((row) => state.prep.plans[+row.dataset.i]);
}
function total() {
  const ps = checkedPlans();
  $("total").textContent = size(ps.reduce((s, p) => s + (p.size_known ? p.bytes : 0), 0));
  const have = ps.reduce((s, p) => s + p.present, 0);
  const fromLib = ps.reduce((s, p) => s + (p.from_library || 0), 0);
  $("total-sub").textContent = `· ${ps.reduce((s, p) => s + p.files - p.present - (p.from_library || 0), 0)} files to download from ${ps.length} sources`
    + (have ? ` · ${have} already in the project` : "") + (fromLib ? ` · ${fromLib} from your library` : "") + " · only your area is downloaded";
  $("fetch-btn").disabled = ps.length === 0;
}

$("fetch-btn").onclick = async () => {
  const ps = checkedPlans();
  if (!ps.length) return showError("plan-error", "Tick at least one dataset.");
  try {
    const j = await api("/api/run", { project: state.prep.project, request_id: state.prep.request_id, plan_ids: ps.map((p) => p.id) });
    state.project = state.prep.project;
    state.job = j.job;
    show("fetch");
    poll();
  } catch (e) { showError("plan-error", e.message); }
};

// ------------------------------------------------------------ 3. Fetching

async function poll() {
  if (!state.job) return;
  let v;
  try { v = await api(`/api/job?id=${state.job}`); } catch (e) { $("fetch-note").textContent = e.message; return setTimeout(poll, 3000); }
  $("progress").innerHTML = v.plans.map((p) => {
    const pct = p.total ? Math.round((100 * p.present) / p.total) : 100;
    return `<div class="prog-row"><span>${esc(p.source)}</span><div class="bar"><div style="width:${pct}%"></div></div><span class="muted small">${p.present}/${p.total} · ${esc(p.status)}</span></div>`;
  }).join("");
  $("log").textContent = v.messages.join("\n");
  if (v.done) {
    const r = v.result;
    $("fetch-note").textContent = v.error ? `Stopped: ${v.error}` :
      `Finished: ${r.fetched} files fetched${r.reused ? ` (${r.reused} from your library, no download)` : ""}, ${r.skipped} already present, ${r.failed} failed${r.failed ? " — open the project and fetch again to retry" : ""}.`;
    state.job = null;
    return;
  }
  setTimeout(poll, 1500);
}
$("to-project").onclick = () => openProject(state.project || state.prep?.project);

// ------------------------------------------------------------ 4. Project

const MARK = { complete: ["b-ok", "Complete"], partial: ["b-incomplete", "Partial"], missing: ["b-incomplete", "Not fetched"], skipped: ["b-skipped", "Not possible"] };

async function openProject(path) {
  if (!path) return;
  state.project = path;
  show("project");
  $("report-card").hidden = true; $("project-note").textContent = "";
  try {
    const s = await api(`/api/status?project=${encodeURIComponent(path)}`);
    drawAoi("map2", s.aoi);
    const missing = s.requests.reduce((n, r) => n + r.plans.filter((p) => p.state === "missing" || p.state === "partial").length, 0);
    $("m-items").textContent = s.items; $("m-size").textContent = size(s.bytes_on_disk); $("m-missing").textContent = missing;
    $("project-body").innerHTML = `<p class="lbl">${esc(s.name)} · ${esc(s.start)} → ${esc(s.end)}</p>` + s.requests.slice().reverse().map((r) => `
      <p class="small" style="margin:12px 0 4px"><strong>“${esc(r.ask)}”</strong> <span class="muted">${esc(r.status)}</span></p>
      ${r.plans.map((p) => `<div class="srow"><span></span><span>${esc(p.needs.join(", "))} <span class="muted">← ${esc(p.source)}</span>${p.reason ? `<br><span class="muted small">${esc(p.reason)}</span>` : ""}</span><span><span class="badge ${MARK[p.state][0]}">${MARK[p.state][1]} ${p.state === "skipped" ? "" : `${p.present}/${p.total}`}</span></span></div>`).join("")}
      ${r.unmet.map((u) => `<div class="srow"><span></span><span>${esc(u.theme)} <span class="muted small">${esc(u.why)}</span></span><span><span class="badge b-skipped">Unmet</span></span></div>`).join("")}`).join("");
    state.lastStatus = s;
  } catch (e) { $("project-body").textContent = e.message; }
}

$("open-qgis").onclick = async () => {
  try { const r = await api("/api/open-qgis", { project: state.project }); $("project-note").textContent = r.ok ? `QGIS is opening with ${r.layers} layers.` : r.error; }
  catch (e) { $("project-note").textContent = e.message; }
};
$("open-folder").onclick = () => api("/api/open-folder", { project: state.project }).catch((e) => ($("project-note").textContent = e.message));
$("show-report").onclick = async () => {
  const d = await api(`/api/report?project=${encodeURIComponent(state.project)}`);
  $("report").innerHTML = d.markdown ? md(d.markdown) : "<p class='muted'>No report yet — it is written when a fetch finishes.</p>";
  $("report-card").hidden = false;
};
$("resume").onclick = async () => {
  const s = state.lastStatus;
  const req = s?.requests[s.requests.length - 1];
  if (!req) return;
  const ids = req.plans.filter((p) => p.state === "missing" || p.state === "partial").map((p) => p.plan);
  if (!ids.length) { $("project-note").textContent = "Nothing is missing."; return; }
  const j = await api("/api/run", { project: state.project, request_id: req.id, plan_ids: ids });
  state.job = j.job; show("fetch"); poll();
};
$("share-recipe").onclick = async () => {
  try {
    const res = await fetch(`/api/recipe?project=${encodeURIComponent(state.project)}`, { headers: { "X-Geofetch-Token": window.GF_TOKEN } });
    if (!res.ok) throw new Error("Could not create the recipe.");
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "project.recipe.json";
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
    $("project-note").textContent = `Recipe saved as ${name} (also kept in the project folder). Share it; anyone can rebuild this dataset with: geofetch recipe run ${name} <folder>`;
  } catch (e) { $("project-note").textContent = e.message; }
};
$("refresh").onclick = async () => {
  const req = state.lastStatus?.requests[state.lastStatus.requests.length - 1];
  if (!req) return;
  const today = new Date().toISOString().slice(0, 10);
  if (req.period && req.period[1] >= today) { $("project-note").textContent = `The latest ask already runs to ${req.period[1]}.`; return; }
  $("ask-text").value = req.ask; $("opt-project").value = state.project;
  $("opt-start").value = req.period ? req.period[0] : ""; $("opt-end").value = today; $("opt-event").value = req.event || "";
  $("project-note").textContent = "Re-planning up to today — only new data will be fetched…";
  state.use = {}; state.skip = []; await plan();
};

async function loadLibrary() {
  try {
    const d = await api("/api/library");
    $("l-files").textContent = d.files; $("l-size").textContent = size(d.bytes); $("l-projects").textContent = d.by_project.length;
    $("library-body").innerHTML = !d.files ? "<p class='muted small'>Empty — files are added as you fetch. Index older projects with: geofetch library --scan ~/geofetch-projects</p>" :
      d.by_source.map((r) => `<div class="srow"><span></span><span>${esc(r.source)} <span class="muted small">in ${r.projects} project(s)</span></span><span class="muted small">${r.files} files · ${size(r.bytes)}</span></div>`).join("")
      + `<p class="lbl" style="margin-top:14px">Projects</p>` + d.by_project.map((p) => `<div class="recent-item" data-path="${esc(p.project)}"><span>${esc(p.project.split("/").pop())}${p.exists ? "" : " <span class='muted small'>(folder missing)</span>"}</span><span class="muted small">${p.files} files · ${size(p.bytes)}</span></div>`).join("");
    document.querySelectorAll("#library-body .recent-item").forEach((el) => (el.onclick = () => openProject(el.dataset.path)));
  } catch (e) { $("library-body").textContent = e.message; }
}

$("ask-here").onclick = () => {
  $("opt-project").value = state.project;
  $("ask-text").value = ""; show("ask"); $("ask-text").focus();
};

// Small markdown renderer for REPORT.md: headings, tables, bullets, emphasis, code.
function md(src) {
  const inline = (t) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/_([^_]+)_/g, "<em>$1</em>");
  const out = []; const lines = src.split("\n"); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^#{1,3} /.test(l)) { const n = l.match(/^#+/)[0].length; out.push(`<h${n + 1}>${inline(l.replace(/^#+ /, ""))}</h${n + 1}>`); i++; }
    else if (l.startsWith("|")) {
      const rows = []; while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i++; }
      const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim());
      out.push("<table>" + rows.filter((r) => !/^\|[-| ]+\|$/.test(r)).map((r, k) => `<tr>${cells(r).map((c) => (k ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`)).join("")}</tr>`).join("") + "</table>");
    } else if (l.startsWith("- ")) {
      const items = []; while (i < lines.length && lines[i].startsWith("- ")) { items.push(`<li>${inline(lines[i].slice(2))}</li>`); i++; }
      out.push(`<ul>${items.join("")}</ul>`);
    } else { if (l.trim()) out.push(`<p>${inline(l)}</p>`); i++; }
  }
  return out.join("");
}

loadProjects();
