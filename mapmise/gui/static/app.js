"use strict";
// Mapmise interface — a view over the engine. Every decision is made server-side; this file only presents it.

const $ = (id) => document.getElementById(id);
const state = { prep: null, project: null, job: null, jobTitle: "", use: {}, skip: [], aoiUpload: null, maps: {}, names: {} };
if (window.GF_NATIVE) document.documentElement.classList.add("native");

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Mapmise-Token": window.GF_TOKEN },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: "Mapmise didn't answer. Is it still running?" }));
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const size = (b) => (b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : b >= 1e6 ? Math.round(b / 1e6) + " MB" : b > 0 ? "<1 MB" : "0 MB");
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const NEED = (n) => { const [theme, t] = String(n).split("/"); return cap(theme.replace(/_/g, " ")) + ({ series: ", time series", pair: ", before and after" }[t] || ""); };
const km2 = (a) => `${Math.round(a).toLocaleString()} km²`;
const fmtDate = (d) => { const x = new Date(d + "T00:00:00"); return isNaN(x) ? d : x.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
const CHEV = '<svg class="chev" width="8" height="13" viewBox="0 0 8 13" aria-hidden="true"><path d="M1.5 1.5 6.5 6.5l-5 5" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
const WARN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2 1 21h22L12 2zm1 15h-2v2h2v-2zm0-7h-2v5h2v-5z"/></svg>';
const INFO = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>';

// ------------------------------------------------------------ navigation

const segs = [...document.querySelectorAll("#steps button")];
function placeThumb() {
  const b = segs.find((x) => x.classList.contains("on")) || segs[0];
  const t = document.querySelector(".segmented .thumb");
  t.style.left = b.offsetLeft + "px"; t.style.width = b.offsetWidth + "px";
}
function show(screen) {
  document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.dataset.screen !== screen));
  segs.forEach((b) => { b.classList.toggle("on", b.dataset.screen === screen); b.setAttribute("aria-current", b.dataset.screen === screen ? "page" : "false"); });
  placeThumb();
  Object.values(state.maps).forEach((m) => setTimeout(() => m.invalidateSize(), 60));
  window.scrollTo({ top: 0 });
}
segs.forEach((b) => (b.onclick = () => { show(b.dataset.screen); if (b.dataset.screen === "library") loadLibrary(); }));
document.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => show(b.dataset.go)));
window.addEventListener("resize", placeThumb);

// A sheet in place of the browser's confirm(): it works the same in the app window and in a browser
function ask(title, text, ok = "OK") {
  return new Promise((resolve) => {
    $("sheet-title").textContent = title; $("sheet-text").textContent = text; $("sheet-ok").textContent = ok;
    $("sheet").hidden = false; $("sheet-ok").focus();
    const done = (v) => { $("sheet").hidden = true; document.removeEventListener("keydown", key); resolve(v); };
    const key = (e) => { if (e.key === "Escape") done(false); };
    document.addEventListener("keydown", key);
    $("sheet-ok").onclick = () => done(true); $("sheet-cancel").onclick = () => done(false);
  });
}

$("quit-btn").onclick = async () => {
  try {
    let r = await api("/api/quit", {});
    if (!r.ok) {
      if (!(await ask("Quit Mapmise?", `${r.running} acquisition job(s) are still running. Files finished so far are kept, and you can resume from the project later.`, "Quit"))) return;
      r = await api("/api/quit", { force: true });
    }
    document.body.innerHTML = '<main><div class="empty"><h2 class="title">Mapmise has stopped</h2><p class="subhead">You can close this tab. Start Mapmise again from your applications.</p></div></main>';
  } catch (e) { $("ask-error").textContent = e.message; $("ask-error").hidden = false; }
};

function drawAoi(id, geojson) {
  if (!state.maps[id]) {
    const m = L.map(id, { zoomControl: true, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(m);
    state.maps[id] = m; m._aoi = null;
  }
  const m = state.maps[id];
  if (m._aoi) m.removeLayer(m._aoi);
  const blue = getComputedStyle(document.documentElement).getPropertyValue("--blue").trim() || "#007aff";
  m._aoi = L.geoJSON(geojson, { style: { color: blue, weight: 2.5, fillOpacity: 0.12 } }).addTo(m);
  setTimeout(() => { m.invalidateSize(); m.fitBounds(m._aoi.getBounds(), { padding: [14, 14] }); }, 80);
}

// ------------------------------------------------------------ Ask

function setError(id, msg) { const el = $(id); el.textContent = msg || ""; el.hidden = !msg; }

async function loadProjects() {
  try {
    const d = await api("/api/projects");
    window.MAPMISE_PROJECTS_LOADED = true;
    $("opt-project").innerHTML = '<option value="">New project</option>' + d.projects.map((p) => `<option value="${esc(p.path)}">${esc(p.name)}</option>`).join("");
    $("recent-wrap").hidden = d.projects.length === 0;
    $("recent").innerHTML = d.projects.slice(0, 6).map((p, i) => `<div class="row link inset" data-path="${esc(p.path)}" tabindex="0" role="button">
        <span class="glyph ${["g-blue", "g-green", "g-indigo", "g-teal", "g-orange"][i % 5]}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" aria-hidden="true"><path d="M3 7l6-3 6 3 6-3v13l-6 3-6-3-6 3z"/></svg></span>
        <div class="grow"><div class="headline" style="font-size:.98rem">${esc(p.name)}</div><div class="caption">${esc(fmtDate(p.start))} – ${esc(fmtDate(p.end))} · ${km2(p.area_km2)}${p.last_ask ? ` · “${esc(p.last_ask)}”` : ""}</div></div>${CHEV}</div>`).join("");
    document.querySelectorAll("#recent .row").forEach((el) => {
      el.onclick = () => openProject(el.dataset.path);
      el.onkeydown = (e) => { if (e.key === "Enter") openProject(el.dataset.path); };
    });
  } catch { /* the list is a convenience */ }
}

$("opts-toggle").onclick = () => { const o = $("opts"); o.hidden = !o.hidden; $("opts-toggle").setAttribute("aria-expanded", String(!o.hidden)); };
$("opt-aoi").onchange = async (ev) => {
  const f = ev.target.files[0]; state.aoiUpload = null;
  if (!f) return;
  try { state.aoiUpload = { name: f.name, geojson: JSON.parse(await f.text()) }; }
  catch { setError("ask-error", "That file isn't valid GeoJSON. Choose a .geojson file."); ev.target.value = ""; }
};
$("ask-text").addEventListener("input", () => {
  setError("ask-error", ""); state.rules = []; state.noEventDate = false; $("choose").hidden = true; $("when").hidden = true;
  if (state.eventFromWhen) { $("opt-event").value = ""; state.eventFromWhen = false; }  // that date belonged to the previous question
});
$("when-go").onclick = () => {
  if (!$("when-date").value) { $("when-date").focus(); return; }
  $("opt-event").value = $("when-date").value; state.eventFromWhen = true; $("when").hidden = true; plan();
};
$("when-unknown").onclick = () => { state.noEventDate = true; $("when").hidden = true; plan(); };
$("ask-text").addEventListener("keydown", (e) => { if (e.key === "Enter") { state.use = {}; state.skip = []; plan(); } });
$("plan-btn").onclick = () => { state.use = {}; state.skip = []; plan(); };
document.querySelectorAll("#examples .chip").forEach((c) => (c.onclick = () => { $("ask-text").value = c.textContent; $("ask-text").focus(); }));

function showChoices(c, question) {
  const chip = (x, cls) => `<button class="chip ${cls}" data-rule="${esc(x.id)}">${esc(x.title)}</button>`;
  $("choose-text").textContent = c.suggested.length
    ? `The built-in AI isn't sure what “${question}” is about. Choose the analysis you mean — the most likely come first.`
    : `Mapmise didn't recognise the analysis in “${question}”. Choose one.`;
  $("choose-suggested").innerHTML = c.suggested.map((x) => chip(x, "suggested")).join("");
  $("choose-all").innerHTML = c.all.map((x) => chip(x, "")).join("");
  $("choose").hidden = false;
  document.querySelectorAll("#choose [data-rule]").forEach((b) => (b.onclick = () => {
    $("choose").hidden = true; state.rules = [b.dataset.rule]; plan();
  }));
}

async function plan(allowLarge = false) {
  const text = $("ask-text").value.trim();
  if (!text) { setError("ask-error", "Describe what you want to analyse first."); $("ask-text").focus(); return; }
  setError("ask-error", ""); setError("plan-error", ""); $("choose").hidden = true; $("when").hidden = true;
  const btn = $("plan-btn"); btn.disabled = true; btn.textContent = "Planning…";
  const busy = $(state.replan ? "total-sub" : "ask-step");
  if (!state.replan) { $("ask-busy").hidden = false; $("ask-step").textContent = "Starting…"; $("ask-elapsed").textContent = ""; }
  const replan = state.replan;
  try {
    const body = { text, place: $("opt-place").value, event: $("opt-event").value, start: $("opt-start").value, end: $("opt-end").value,
      mode: $("opt-mode").value, project: state.prep?.project && state.replan ? state.prep.project : $("opt-project").value,
      use: state.use, skip: state.skip, allow_large: allowLarge, rules: state.rules || [], no_event_date: !!state.noEventDate };
    if (state.aoiUpload && !body.project) { body.aoi_geojson = state.aoiUpload.geojson; body.aoi_name = state.aoiUpload.name; }
    const { job } = await api("/api/prepare", body);
    state.planJob = job;
    let s;
    for (;;) {  // each step is shown as it happens; Cancel stops at the next step
      s = await api(`/api/prepare-status?id=${job}`);
      busy.textContent = s.step;
      if (!replan) $("ask-elapsed").textContent = s.seconds >= 5 ? `${s.seconds} s` : "";
      if (s.done) break;
      await new Promise((r) => setTimeout(r, 600));
    }
    state.planJob = null;
    if (s.kind === "large") {
      $("ask-busy").hidden = true;
      if (await ask(`${s.area.name} is a very large area`, s.problem, "Plan anyway")) { state.replan = replan; return await plan(true); }  // await: this call's cleanup must not run while the new plan is still going
      setError("ask-error", `Name a smaller place than ${s.area.name} (${s.area.km2.toLocaleString()} km²), or use a boundary file.`);
      return;
    }
    if (s.kind === "cancelled") { setError("ask-error", "Planning was cancelled."); return; }
    if (s.kind === "choose") { showChoices(s.choices, text); return; }
    if (s.kind === "event") {
      $("when-text").textContent = `${s.analysis} compares data from just before and just after the event. Knowing the date lets Mapmise pick the right scenes.`;
      $("when-date").max = new Date().toISOString().slice(0, 10); $("when").hidden = false; $("when-date").focus(); return;
    }
    if (s.problem) throw new Error(s.problem);
    state.prep = s.result;
    state.prep.plans.forEach((p) => (state.names[p.source] = p.source_name));
    show("plan");
    renderPlan();
  } catch (e) {
    setError(replan ? "plan-error" : "ask-error", e.message);
  } finally { btn.disabled = false; btn.textContent = "Create plan"; state.replan = false; $("ask-busy").hidden = true; state.planJob = null; }
}
$("plan-cancel").onclick = () => { if (state.planJob) { $("ask-step").textContent = "Cancelling…"; api("/api/prepare-cancel", { job: state.planJob }).catch(() => {}); } };

// ------------------------------------------------------------ Plan

const W = { "feasible-single": ["d-green", "Ready"], "feasible-composite": ["d-orange", "Needs several scenes"], infeasible: ["d-red", "Too cloudy"],
  incomplete: ["d-orange", "Partial coverage"], "out-of-range": ["d-gray", "Not covered"] };
const WLABEL = (l) => ({ pre: "Before", post: "After", static: "" }[l] ?? l);

function verdictLine(p, have, lib) {
  if (have) return '<span class="dot d-green"></span>Already in this project';
  if (p.worst === "skipped") return `<span class="dot d-gray"></span>Not possible${p.windows[0] ? ` — ${esc(p.windows[0].text)}` : ""}`;
  const suffix = lib ? " · in your library" : "";
  if (p.windows.length <= 3) {
    return p.windows.map((w) => { const [d, t] = W[w.verdict] || ["d-gray", w.verdict]; const l = WLABEL(w.label);
      return `<span class="dot ${d}"></span>${l ? `${esc(l)}: ${t.toLowerCase()}` : t}`; }).join(" · ") + suffix;
  }
  const counts = {};
  p.windows.forEach((w) => { counts[w.verdict] = (counts[w.verdict] || 0) + 1; });
  return Object.entries(counts).map(([v, n]) => { const [d, t] = W[v] || ["d-gray", v]; return `<span class="dot ${d}"></span>${t} · ${n} of ${p.windows.length} periods`; }).join(" ") + suffix;
}

function renderPlan() {
  const r = state.prep;
  $("plan-empty").hidden = true; $("plan-body").hidden = false;
  let placeLine = r.place.split("\n")[0];
  if (placeLine.includes(" → ")) placeLine = placeLine.split(" → ").pop();  // “Noida” → Noida, Dadri, … (lookup trail)
  placeLine = placeLine.split(" — ")[0].replace(/\s*\([^)]*\)\s*$/, "").trim();
  const placeShort = placeLine.split(",")[0];
  const T = (id) => (r.rule_titles && r.rule_titles[id]) || cap(id.replace(/_/g, " "));
  $("plan-title").textContent = r.rules.length ? `${r.rules.map((id, i) => (i ? T(id).toLowerCase() : T(id))).join(" and ")} in ${placeShort}` : `Data for ${placeShort}`;
  $("plan-sub").textContent = `${fmtDate(r.start)} – ${fmtDate(r.end)} · ${km2(r.area_km2)} · ${r.plans.length} datasets`;

  const how = (id) => (r.how[id] === "keyword" ? "a word in your question" : r.how[id].replace(/^meaning: /, "understood by meaning: "));
  $("understood").innerHTML = `
    <div class="row"><div class="grow">Place</div><div class="value">${esc(placeLine)}</div></div>
    <div class="row"><div class="grow">Area</div><div class="value num">${km2(r.area_km2)}${r.country ? ` · ${esc(r.country)}` : ""}</div></div>
    <div class="row"><div class="grow">Period</div><div class="value num">${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))}</div></div>
    <div class="row"><div class="grow">Analysis</div><div class="value">${r.rules.map((id) => esc(T(id))).join(", ") || "—"}</div></div>
    <div class="row"><div class="grow">Projection</div><div class="value num">EPSG:${r.epsg}</div></div>`;
  $("understood-notes").innerHTML = [
    `<p class="footnote understood-note">${r.rules.map((id) => `<b>${esc(T(id))}</b> — ${esc(how(id))}.`).join(" ")} ${r.ai === "unavailable" ? "Built-in AI unavailable." : ""}</p>`,
    `<p class="footnote understood-note">Period ${esc(r.period_source)}.</p>`,
    r.event_note ? `<p class="footnote understood-note">${esc(r.event_note)}</p>` : "",
    r.library_here.length ? `<p class="footnote understood-note">Already in your library here: ${r.library_here.map((g) => `${esc(state.names[g.source] || g.source)} (${g.files} files)`).join(", ")}.</p>` : "",
    r.notes.length ? `<p class="footnote understood-note">${r.notes.map(esc).join("<br>")}</p>` : "",
    `<p class="footnote understood-note" title="${esc(r.project)}">Project folder: ${esc(r.project.split(/[\\/]/).pop())}</p>`].join("");
  drawAoi("map", r.aoi);

  const LEVEL = { infeasible: "too cloudy", composite: "needs several scenes", incomplete: "partial coverage", uncovered: "some years not covered" };
  const WHEN = { pre: "Before the event", post: "After the event" };
  const bySource = {};
  r.attention.forEach((a) => (bySource[a.source] = bySource[a.source] || []).push(a));
  $("attention").innerHTML = Object.entries(bySource).map(([src, items]) => {
    const worst = items.find((a) => a.level === "infeasible") || items[0];
    const lines = items.map((a) => { const m = a.text.match(/^([\w-]+): (.*)$/); const when = m && (WHEN[m[1]] || m[1]);
      return `<div class="footnote">${when ? `<b>${esc(when)}:</b> ${esc(cap(m[2]))}` : esc(cap(a.text))}</div>`; }).join("");
    return `<div class="notice">${WARN}<div><div class="headline">${esc(state.names[src] || src)} — ${esc(LEVEL[worst.level] || worst.level)}</div>${lines}</div></div>`;
  }).join("");
  $("unmet").innerHTML = r.unmet.length ? `<p class="section-label">Not available</p>` + r.unmet.map((u) => `<div class="notice gray">${INFO}<div><div class="headline">${esc(cap(u.theme))}</div><div class="footnote">${esc(u.why)}</div></div></div>`).join("") : "";

  $("plan-rows").innerHTML = r.plans.map((p, i) => {
    const have = p.files && p.present === p.files;
    const lib = !have && p.files && p.present + p.from_library === p.files;
    const sz = have ? "Have" : lib ? "0 MB" : p.size_known ? (p.unknown_sizes && !p.bytes ? "Unknown" : (p.unknown_sizes ? "≥ " : "") + size(p.bytes)) : "Live";
    const pr = p.needs[0]?.priority;
    const tags = (pr === "required" ? '<span class="tag">Required</span>' : pr === "optional" ? '<span class="tag gray">Optional</span>' : "")
      + (p.fallback_for ? '<span class="tag gray">Cloud-free alternative</span>' : "") + (p.complement_for ? '<span class="tag gray">Fills missing years</span>' : "");
    const skipped = p.worst === "skipped";
    const alts = [p.source, ...p.alternatives];
    return `<div class="row link ds" data-i="${i}">
        <div class="grow"><div class="headline">${esc(p.source_name)} ${tags}</div>
          <div class="footnote">${esc(cap(p.needs.map((n) => n.why).join("; ")))}</div>
          <div class="verdict">${verdictLine(p, have, lib)}</div></div>
        <div class="size num">${sz}</div>
        <input type="checkbox" class="switch" ${skipped || have ? "" : "checked"} ${skipped ? "disabled" : ""} aria-label="Include ${esc(p.source_name)}">
      </div>
      <div class="detail" data-for="${i}" hidden>
        ${esc(p.licence)}${p.resolution_m ? ` · ${p.resolution_m} m` : ""} · ${p.files} file${p.files === 1 ? "" : "s"}
        ${p.explanations.length ? `<ul>${p.explanations.slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${p.windows.length ? `<ul>${p.windows.slice(0, 8).map((w) => `<li>${esc(WLABEL(w.label) || "Static")}: ${esc(w.text)}</li>`).join("")}${p.windows.length > 8 ? `<li>and ${p.windows.length - 8} more periods</li>` : ""}</ul>` : ""}
        ${p.alternatives.length ? `<div class="src"><span>Source</span><select data-need="${esc(p.need_key)}" aria-label="Source for ${esc(p.need_key)}">${alts.map((s) => `<option value="${esc(s)}" ${s === p.source ? "selected" : ""}>${esc(state.names[s] || s)}</option>`).join("")}</select></div>` : ""}
      </div>`;
  }).join("");

  document.querySelectorAll("#plan-rows .ds").forEach((row) => (row.onclick = (e) => {
    if (e.target.closest(".switch")) return;
    const d = document.querySelector(`.detail[data-for="${row.dataset.i}"]`); d.hidden = !d.hidden; row.classList.toggle("open", !d.hidden);
  }));
  document.querySelectorAll("#plan-rows .switch").forEach((cb) => (cb.onchange = total));
  document.querySelectorAll("#plan-rows select").forEach((sel) => (sel.onchange = () => {
    state.use[sel.dataset.need] = sel.value; state.replan = true;
    $("total-sub").textContent = `Re-planning with ${state.names[sel.value] || sel.value}…`; plan();
  }));
  total();
}

function checkedPlans() {
  return [...document.querySelectorAll("#plan-rows .ds")].filter((row) => row.querySelector(".switch").checked).map((row) => state.prep.plans[+row.dataset.i]);
}
function total() {
  const ps = checkedPlans();
  const bytes = ps.reduce((s, p) => s + (p.size_known ? p.bytes : 0), 0);
  const fromLib = ps.reduce((s, p) => s + (p.from_library || 0), 0);
  $("total").textContent = `${ps.length} dataset${ps.length === 1 ? "" : "s"} · ${size(bytes)}`;
  $("total-sub").textContent = "Only your area is downloaded. You can stop and resume at any time." + (fromLib ? ` ${fromLib} file${fromLib === 1 ? "" : "s"} come from your library.` : "");
  $("fetch-btn").disabled = ps.length === 0;
}

$("fetch-btn").onclick = async () => {
  const ps = checkedPlans();
  if (!ps.length) return setError("plan-error", "Select at least one dataset.");
  try {
    const j = await api("/api/run", { project: state.prep.project, request_id: state.prep.request_id, plan_ids: ps.map((p) => p.id) });
    state.project = state.prep.project; state.job = j.job;
    state.jobTitle = $("plan-title").textContent; state.jobSub = $("plan-sub").textContent.split(" · ")[0];
    startFetchView(); poll();
  } catch (e) { setError("plan-error", e.message); }
};

// ------------------------------------------------------------ Acquisition

function startFetchView() {
  $("fetch-empty").hidden = true; $("fetch-body").hidden = false;
  $("fetch-title").textContent = state.jobTitle || "Acquiring data"; $("fetch-sub").textContent = state.jobSub || "";
  $("fetch-state").textContent = "Starting…"; $("ring").classList.remove("done"); setRing(0, "");
  show("fetch");
}
function setRing(frac, sub) {
  $("ring").style.strokeDashoffset = String(326.7 * (1 - frac));
  $("ring-pct").textContent = `${Math.round(frac * 100)}%`; $("ring-sub").textContent = sub;
}

async function poll() {
  if (!state.job) return;
  let v;
  try { v = await api(`/api/job?id=${state.job}`); } catch (e) { $("fetch-note").textContent = e.message; return setTimeout(poll, 3000); }
  const tot = v.plans.reduce((s, p) => s + p.total, 0), got = v.plans.reduce((s, p) => s + p.present, 0);
  setRing(tot ? got / tot : 1, `${got} of ${tot} files`);
  $("progress").innerHTML = v.plans.map((p) => {
    const pct = p.total ? Math.round((100 * p.present) / p.total) : 100;
    const finished = p.present >= p.total;
    const label = finished ? "Done" : p.present ? `${p.present} of ${p.total}` : v.done ? "Stopped" : "Waiting";
    return `<div class="row"><div class="grow"><div class="headline" style="font-size:.98rem">${esc(state.names[p.source] || p.source)}</div>
      <div class="progress ${finished ? "done" : v.done ? "failed" : ""}"><i style="width:${pct}%"></i></div></div>
      <div class="size num" style="${finished ? "color:var(--green)" : ""}">${label}</div></div>`;
  }).join("");
  $("log").textContent = v.messages.join("\n");
  if (v.done) {
    const r = v.result;
    $("ring").classList.toggle("done", !v.error && r && !r.failed);
    $("fetch-state").textContent = v.error ? "Acquisition stopped" : r.failed ? `Finished with ${r.failed} failed file${r.failed === 1 ? "" : "s"}` : "Acquisition complete";
    $("fetch-note").textContent = v.error ? v.error :
      `${r.fetched} file${r.fetched === 1 ? "" : "s"} acquired${r.reused ? `, ${r.reused} from your library without downloading` : ""}${r.skipped ? `, ${r.skipped} already present` : ""}.`
      + (r.failed ? " Open the project and choose Acquire missing to retry." : "");
    state.job = null;
    return;
  }
  $("fetch-state").textContent = "Acquiring only your area…";
  setTimeout(poll, 1500);
}
$("to-project").onclick = () => openProject(state.project || state.prep?.project);

// ------------------------------------------------------------ Project

const MARK = { complete: ["d-green", "Complete"], partial: ["d-orange", "Partial"], missing: ["d-orange", "Not acquired"], skipped: ["d-gray", "Not possible"] };
function note(msg) { $("project-note").textContent = msg; $("project-note").hidden = !msg; }

async function openProject(path) {
  if (!path) return;
  state.project = path;
  $("project-empty").hidden = true; $("project-main").hidden = false;
  show("project");
  $("report-wrap").hidden = true; note("");
  try {
    const s = await api(`/api/status?project=${encodeURIComponent(path)}`);
    drawAoi("map2", s.aoi);
    const plans = s.requests.flatMap((r) => r.plans);
    const missing = plans.filter((p) => p.state === "missing" || p.state === "partial").length;
    $("p-title").textContent = s.name;
    $("p-sub").textContent = `${fmtDate(s.start)} – ${fmtDate(s.end)} · ${path.split(/[\\/]/).pop()}`;
    $("p-sub").title = path;
    $("m-sources").textContent = new Set(plans.map((p) => p.source)).size;
    $("m-items").textContent = s.items; $("m-size").textContent = size(s.bytes_on_disk);
    $("m-missing").textContent = missing; $("m-missing").style.color = missing ? "var(--orange)" : "";
    $("project-body").innerHTML = s.requests.slice().reverse().map((r) => `
      <p class="section-label" style="margin-top:0"><span class="request-ask">“${esc(r.ask)}”</span></p>
      <div class="group" style="margin-bottom:18px">
        ${r.plans.map((p) => `<div class="row"><span class="dot ${MARK[p.state][0]}"></span><div class="grow">${esc(state.names[p.source] || p.source)}
          <div class="footnote">${esc(p.needs.map(NEED).join("; "))}${p.reason ? ` — ${esc(p.reason)}` : ""}</div></div>
          <div class="value num">${p.state === "skipped" ? MARK.skipped[1] : `${MARK[p.state][1]} · ${p.present}/${p.total}`}</div></div>`).join("")}
        ${r.unmet.map((u) => `<div class="row"><span class="dot d-gray"></span><div class="grow">${esc(cap(u.theme))}<div class="footnote">${esc(u.why)}</div></div><div class="value">Unavailable</div></div>`).join("")}
      </div>`).join("");
    state.lastStatus = s;
  } catch (e) { $("project-body").innerHTML = `<p class="inline-error">${esc(e.message)}</p>`; }
}

$("open-qgis").onclick = async () => {
  note("Preparing layers for QGIS…");
  try { const r = await api("/api/open-qgis", { project: state.project }); note(r.ok ? `QGIS is opening with ${r.layers} layers.` : "QGIS isn't installed where Mapmise can find it. Install QGIS, or open the project folder in your GIS."); }
  catch (e) { note(e.message); }
};
$("open-folder").onclick = () => api("/api/open-folder", { project: state.project }).catch((e) => note(e.message));
$("show-report").onclick = async () => {
  try {
    const d = await api(`/api/report?project=${encodeURIComponent(state.project)}`);
    $("report").innerHTML = d.markdown ? md(d.markdown) : "<p class='footnote'>The report is written when an acquisition finishes.</p>";
    $("report-wrap").hidden = false; $("report-wrap").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { note(e.message); }
};
$("resume").onclick = async () => {
  const s = state.lastStatus; const req = s?.requests[s.requests.length - 1];
  if (!req) return;
  const ids = req.plans.filter((p) => p.state === "missing" || p.state === "partial").map((p) => p.plan);
  if (!ids.length) return note("Nothing is missing.");
  try {
    const j = await api("/api/run", { project: state.project, request_id: req.id, plan_ids: ids });
    state.job = j.job; state.jobTitle = s.name; state.jobSub = "Acquiring what is missing"; startFetchView(); poll();
  } catch (e) { note(e.message); }
};
$("share-recipe").onclick = async () => {
  try {
    const r = await api("/api/save-recipe", { project: state.project });
    note(`Recipe saved as ${r.path.split(/[\\/]/).pop()} in the project folder. Anyone can rebuild this dataset from it with: mapmise recipe run <recipe> <folder>`);
  } catch (e) { note(e.message); }
};
$("refresh").onclick = async () => {
  const req = state.lastStatus?.requests[state.lastStatus.requests.length - 1];
  if (!req) return;
  const today = new Date().toISOString().slice(0, 10);
  if (req.period && req.period[1] >= today) return note(`The latest question already runs to ${fmtDate(req.period[1])}.`);
  $("ask-text").value = req.ask; $("opt-project").value = state.project;
  $("opt-start").value = req.period ? req.period[0] : ""; $("opt-end").value = today; $("opt-event").value = req.event || "";
  note("Re-planning up to today; only new data will be acquired…");
  state.use = {}; state.skip = []; await plan();
};
$("ask-here").onclick = () => { $("opt-project").value = state.project; $("ask-text").value = ""; show("ask"); $("ask-text").focus(); };

// ------------------------------------------------------------ Library

async function loadLibrary() {
  try {
    const d = await api("/api/library");
    $("l-files").textContent = d.files; $("l-size").textContent = size(d.bytes); $("l-projects").textContent = d.by_project.length;
    $("library-body").innerHTML = !d.files
      ? `<div class="empty" style="padding:48px 0"><h2 class="headline">Your library is empty</h2><p class="footnote">Files are added as you acquire them.</p></div>`
      : `<p class="section-label">By source</p><div class="group">${d.by_source.map((r) => `<div class="row"><div class="grow">${esc(state.names[r.source] || r.source)}<div class="footnote">In ${r.projects} project${r.projects === 1 ? "" : "s"}</div></div><div class="value num">${r.files} files · ${size(r.bytes)}</div></div>`).join("")}</div>
         <p class="section-label">Projects</p><div class="group">${d.by_project.map((p) => `<div class="row ${p.exists ? "link" : ""}" data-path="${esc(p.project)}"><div class="grow">${esc(p.project.split(/[\\/]/).pop())}${p.exists ? "" : '<div class="footnote">Folder no longer exists</div>'}</div><div class="value num">${p.files} files · ${size(p.bytes)}</div>${p.exists ? CHEV : ""}</div>`).join("")}</div>`;
    document.querySelectorAll("#library-body .row.link").forEach((el) => (el.onclick = () => openProject(el.dataset.path)));
  } catch (e) { $("library-body").innerHTML = `<p class="inline-error">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------ Settings: NASA Earthdata token

function edMsg(t) { $("ed-msg").textContent = t || ""; $("ed-msg").hidden = !t; }
async function edRefresh() {
  try {
    const i = await api("/api/earthdata");
    $("ed-status").textContent = !i.present ? "Not set — NASA datasets that need a login are left out of plans."
      : i.expired ? "Expired — paste a new token." : `Saved${i.source === "environment" ? " (from the EARTHDATA_TOKEN variable)" : ""}; valid until ${fmtDate(i.expires)}.`;
    $("ed-remove").hidden = !i.present || i.source === "environment"; $("ed-check").hidden = !i.present;
  } catch (e) { $("ed-status").textContent = e.message; }
}
$("settings-btn").onclick = () => {
  edMsg(""); $("ed-token").value = ""; $("settings").hidden = false; edRefresh();
  api("/api/about").then((a) => { $("about-version").textContent = a.version; $("about-system").textContent = a.system; }).catch(() => {});
};
$("report-problem").onclick = () => api("/api/report-problem", {}).catch((e) => edMsg(e.message));
$("open-log").onclick = () => api("/api/open-log", {}).catch((e) => edMsg(e.message));
$("settings-close").onclick = () => { $("ed-token").value = ""; $("settings").hidden = true; };
$("settings").addEventListener("keydown", (e) => { if (e.key === "Escape") $("settings-close").click(); });
$("ed-save").onclick = async () => {
  const t = $("ed-token").value.trim();
  if (!t) return edMsg("Paste your token first.");
  try { await api("/api/earthdata", { token: t }); $("ed-token").value = ""; edMsg("Token saved."); edRefresh(); }
  catch (e) { edMsg(e.message); }
};
$("ed-remove").onclick = async () => { await api("/api/earthdata", { remove: true }); edMsg("Token removed from this computer."); edRefresh(); };
$("ed-check").onclick = async () => {
  edMsg("Checking with NASA…");
  try { const r = await api("/api/earthdata", { check: true }); edMsg(r.ok ? `Works: ${r.detail}.` : r.detail); }
  catch (e) { edMsg(e.message); }
};

// Small renderer for REPORT.md: headings, tables, bullets, emphasis, code
function md(src) {
  const inline = (t) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/_([^_]+)_/g, "<em>$1</em>");
  const out = []; const lines = src.split("\n"); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^#{1,3} /.test(l)) { const n = l.match(/^#+/)[0].length; out.push(`<h${n + 1}>${inline(l.replace(/^#+ /, ""))}</h${n + 1}>`); i++; }
    else if (l.startsWith("|")) {
      const rows = []; while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i++; }
      const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim());
      out.push("<table>" + rows.filter((r) => !/^\|[-| :]+\|$/.test(r)).map((r, k) => `<tr>${cells(r).map((c) => (k ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`)).join("")}</tr>`).join("") + "</table>");
    } else if (l.startsWith("- ")) {
      const items = []; while (i < lines.length && lines[i].startsWith("- ")) { items.push(`<li>${inline(lines[i].slice(2))}</li>`); i++; }
      out.push(`<ul>${items.join("")}</ul>`);
    } else { if (l.trim()) out.push(`<p>${inline(l)}</p>`); i++; }
  }
  return out.join("");
}

show("ask");
api("/api/sources").then((n) => Object.assign(state.names, n)).catch(() => {}).finally(loadProjects);
window.MAPMISE_READY = true;
