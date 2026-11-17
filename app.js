/* NeuroRadar — globe, filters, company cards, jobs board (static, no build). */

const CATEGORIES = {
  bci:             { label: "Brain-Computer Interface",   color: "#818cf8" },
  consumer:        { label: "Consumer Neurotech",         color: "#f472b6" },
  diagnostics:     { label: "Diagnostics & Assessment",   color: "#34d399" },
  neuroimaging:    { label: "Neuroimaging",               color: "#22d3ee" },
  neuromodulation: { label: "Neuromodulation",            color: "#fbbf24" },
  tools:           { label: "Tools & Infrastructure",     color: "#e5e7eb" },
};
const TEX = "https://cdn.jsdelivr.net/npm/three-globe@2.45.6/example/img/";
const NEW_WINDOW_H = 72; // a job counts as "nuova" for 3 days after first sighting
const HOME_POV = { lat: 38, lng: -30, altitude: 2.3 };

const PROFILE_FIELDS = [
  ["modality", "Modalità"], ["form_factor", "Form factor"], ["interface_depth", "Invasività"],
  ["indication", "Indicazione"], ["target_user", "Utenti"], ["regulatory_stage", "Regolatorio"],
  ["founded", "Fondata"], ["funding_stage", "Funding"],
];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
));
const cap = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

const state = {
  companies: [], byId: new Map(), jobs: [], jobsByCompany: new Map(),
  active: new Set(Object.keys(CATEGORIES)), country: "", query: "", onlyHiring: false,
  selected: null, view: "globe",
  board: { query: "", onlyNew: false, remote: "", company: "" },
};

let globe;

async function init() {
  const [companies, jobs] = await Promise.all([
    fetch("data/companies.json", { cache: "no-cache" }).then((r) => r.json()),
    fetch("data/jobs.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : [])).catch(() => []),
  ]);
  state.companies = companies;
  companies.forEach((c) => state.byId.set(c.id, c));
  state.jobs = jobs.filter((j) => state.byId.has(j.company_id));
  state.jobs.forEach((j) => {
    if (!state.jobsByCompany.has(j.company_id)) state.jobsByCompany.set(j.company_id, []);
    state.jobsByCompany.get(j.company_id).push(j);
  });

  const countries = new Set(companies.map((c) => c.country));
  const newCount = state.jobs.filter(isNew).length;
  $("stats").textContent =
    `${companies.length} startup · ${countries.size} paesi · ${state.jobs.length} posizioni aperte` +
    (newCount ? ` · ${newCount} nuove` : "");
  $("tabJobs").textContent = `Posizioni (${state.jobs.length})`;

  buildGlobe();
  buildChips();
  buildCountrySelect([...countries].sort());
  bindEvents();
  render();
  openFromHash();
}

/* ---------- helpers ---------- */
function isNew(j) {
  if (j.baseline) return false;
  return (Date.now() - Date.parse(j.first_seen_at)) / 36e5 < NEW_WINDOW_H;
}
const jobsOf = (c) => state.jobsByCompany.get(c.id) || [];
const hasNewJobs = (c) => jobsOf(c).some(isNew);
function relTime(iso) {
  if (!iso) return "";
  const d = (Date.now() - Date.parse(iso)) / 864e5;
  if (d < 1) return "oggi";
  if (d < 2) return "ieri";
  if (d < 30) return `${Math.floor(d)} giorni fa`;
  return new Date(iso).toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" });
}

/* ---------- globe ---------- */
function buildGlobe() {
  globe = Globe()($("globe"))
    .globeImageUrl(TEX + "earth-dark.jpg")
    .bumpImageUrl(TEX + "earth-topology.png")
    .backgroundImageUrl(TEX + "night-sky.png")
    .atmosphereColor("#818cf8")
    .atmosphereAltitude(0.18)
    .pointLat("lat").pointLng("lng")
    .pointColor((c) => CATEGORIES[c.category].color)
    .pointAltitude((c) => 0.012 + Math.min(jobsOf(c).length, 40) * 0.005 + (state.selected === c ? 0.05 : 0))
    .pointRadius((c) => (state.selected === c ? 0.32 : 0.2))
    .pointResolution(10)
    .pointsTransitionDuration(300)
    .pointLabel((c) => {
      const n = jobsOf(c).length;
      return `<div class="tooltip"><b>${esc(c.name)}</b>
        <span>${esc(CATEGORIES[c.category].label)} · ${esc(c.city ? c.city + ", " : "")}${esc(c.country)}</span>
        ${n ? `<span class="tt-jobs">${n} posizion${n === 1 ? "e" : "i"} apert${n === 1 ? "a" : "e"}${hasNewJobs(c) ? " · nuove!" : ""}</span>` : ""}
      </div>`;
    })
    .onPointClick((c) => select(c, true))
    .ringLat("lat").ringLng("lng")
    .ringColor((c) => (t) => (c === state.selected ? `rgba(165,180,252,${1 - t})` : `rgba(52,211,153,${1 - t})`))
    .ringMaxRadius(3).ringPropagationSpeed(2.5).ringRepeatPeriod(1100);

  const controls = globe.controls();
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.35;
  ["mousedown", "touchstart", "wheel"].forEach((ev) =>
    $("globe").addEventListener(ev, () => (controls.autoRotate = false), { passive: true }));

  globe.pointOfView(HOME_POV, 0);
  const resize = () => globe.width(window.innerWidth).height(window.innerHeight);
  window.addEventListener("resize", resize);
  resize();
}

/* ---------- side panel ---------- */
function buildChips() {
  const counts = {};
  state.companies.forEach((c) => (counts[c.category] = (counts[c.category] || 0) + 1));
  $("chips").innerHTML = Object.entries(CATEGORIES).map(([key, cat]) => `
    <button class="chip" data-cat="${key}" aria-pressed="true" style="--c:${cat.color}">
      <span class="dot"></span>${esc(cat.label)} <span class="n">${counts[key] || 0}</span>
    </button>`).join("");
}

function buildCountrySelect(countries) {
  $("country").innerHTML = `<option value="">Tutti i paesi</option>` +
    countries.map((c) => {
      const n = state.companies.filter((x) => x.country === c).length;
      return `<option value="${esc(c)}">${esc(c)} (${n})</option>`;
    }).join("");
}

function bindEvents() {
  $("chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const k = chip.dataset.cat;
    if (e.altKey) state.active = new Set([k]);
    else state.active.has(k) ? state.active.delete(k) : state.active.add(k);
    render();
  });
  $("chips").addEventListener("dblclick", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) { state.active = new Set([chip.dataset.cat]); render(); }
  });
  $("country").addEventListener("change", (e) => { state.country = e.target.value; render(); flyToFiltered(); });
  $("search").addEventListener("input", (e) => { state.query = e.target.value.trim().toLowerCase(); render(); });
  $("onlyHiring").addEventListener("change", (e) => { state.onlyHiring = e.target.checked; render(); });
  $("reset").addEventListener("click", () => {
    Object.assign(state, { active: new Set(Object.keys(CATEGORIES)), country: "", query: "", onlyHiring: false });
    $("country").value = ""; $("search").value = ""; $("onlyHiring").checked = false;
    render();
    globe.pointOfView(HOME_POV, 1000);
  });
  $("list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    select(state.byId.get(li.dataset.id), true);
    if (window.matchMedia("(max-width: 760px)").matches) togglePanel(false);
  });
  $("togglePanel").addEventListener("click", () => togglePanel($("panel").classList.contains("collapsed")));
  $("tabGlobe").addEventListener("click", () => setView("globe"));
  $("tabJobs").addEventListener("click", () => setView("jobs"));

  // jobs board
  $("boardSearch").addEventListener("input", (e) => { state.board.query = e.target.value.trim().toLowerCase(); renderBoard(); });
  $("boardNew").addEventListener("change", (e) => { state.board.onlyNew = e.target.checked; renderBoard(); });
  $("boardRemote").addEventListener("change", (e) => { state.board.remote = e.target.value; renderBoard(); });
  $("boardClearCompany").addEventListener("click", () => { state.board.company = ""; renderBoard(); });
  $("board").addEventListener("click", (e) => {
    const a = e.target.closest("[data-company]");
    if (!a) return;
    e.preventDefault();
    setView("globe");
    select(state.byId.get(a.dataset.company), true);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (state.view === "jobs") setView("globe"); else select(null);
  });
  window.addEventListener("hashchange", openFromHash);
}

function togglePanel(open) {
  $("panel").classList.toggle("collapsed", !open);
  $("togglePanel").setAttribute("aria-expanded", String(open));
}

function filtered() {
  const q = state.query;
  return state.companies.filter((c) =>
    state.active.has(c.category) &&
    (!state.country || c.country === state.country) &&
    (!state.onlyHiring || jobsOf(c).length) &&
    (!q || [c.name, c.description, c.country, c.city, c.modality, c.form_factor, c.indication]
      .join(" ").toLowerCase().includes(q)));
}

function render() {
  const items = filtered();
  document.querySelectorAll(".chip").forEach((chip) =>
    chip.setAttribute("aria-pressed", String(state.active.has(chip.dataset.cat))));

  $("resultCount").textContent = `${items.length} aziende`;
  $("list").innerHTML = items.slice().sort((a, b) => a.name.localeCompare(b.name)).map((c) => {
    const n = jobsOf(c).length;
    return `<li data-id="${c.id}" aria-selected="${state.selected === c}" style="--c:${CATEGORIES[c.category].color}">
        <span class="dot"></span>
        <div class="txt">
          <div class="name">${esc(c.name)}</div>
          <div class="sub">${esc(c.country)} · ${esc(c.modality || c.description)}</div>
        </div>
        ${n ? `<span class="count${hasNewJobs(c) ? " new" : ""}" title="Posizioni aperte">${n}</span>` : ""}
      </li>`;
  }).join("");

  globe.pointsData(items);
  const rings = items.filter(hasNewJobs);
  if (state.selected && items.includes(state.selected) && !rings.includes(state.selected)) rings.push(state.selected);
  globe.ringsData(rings);
}

function flyToFiltered() {
  const items = filtered();
  if (!items.length) return;
  const lat = items.reduce((s, c) => s + c.lat, 0) / items.length;
  const lng = items.reduce((s, c) => s + c.lng, 0) / items.length;
  globe.controls().autoRotate = false;
  globe.pointOfView({ lat, lng, altitude: state.country ? 1.1 : 2.3 }, 1200);
}

/* ---------- company card ---------- */
function select(company, fly = false) {
  state.selected = company || null;
  render();
  globe.pointAltitude(globe.pointAltitude()).pointRadius(globe.pointRadius());

  const card = $("card");
  if (!company) {
    card.hidden = true;
    if (location.hash) history.replaceState(null, "", location.pathname);
    return;
  }
  history.replaceState(null, "", `#${company.id}`);
  if (fly) {
    globe.controls().autoRotate = false;
    globe.pointOfView({ lat: company.lat, lng: company.lng, altitude: 0.9 }, 1100);
  }
  card.innerHTML = cardHtml(company);
  card.hidden = false;
  card.querySelector(".close").addEventListener("click", () => select(null));
  card.querySelector("[data-all-jobs]")?.addEventListener("click", () => {
    state.board.company = company.id;
    setView("jobs");
  });
}

function cardHtml(c) {
  const cat = CATEGORIES[c.category];
  const site = safeUrl(c.website);
  const careers = safeUrl(c.careers_url);
  const nfUrl = `https://www.neurofounders.co/startups/${encodeURIComponent(c.neurofounders_slug)}`;
  const jobs = jobsOf(c).slice().sort((a, b) => isNew(b) - isNew(a) || a.title.localeCompare(b.title));
  const facts = PROFILE_FIELDS.filter(([k]) => c[k]).map(([k, label]) =>
    `<div><dt>${label}</dt><dd>${esc(c[k])}</dd></div>`).join("");

  let jobsBlock;
  if (jobs.length) {
    jobsBlock = `<ul class="jobs">${jobs.slice(0, 8).map(jobRowSmall).join("")}</ul>` +
      (jobs.length > 8 || jobs.length > 2
        ? `<button class="link-btn" data-all-jobs>Vedi tutte le ${jobs.length} posizioni →</button>` : "");
  } else {
    const msg = {
      ats: "Nessuna posizione aperta al momento.",
      custom_page: "Pagina carriere personalizzata: la leggerà Claude nella fase 3.",
      ats_unsupported: `Usa ${esc((c.ats || {}).type)}: lettore in arrivo.`,
      no_website: "Sito non trovato.",
      not_found: "Pagina carriere non trovata sul sito.",
    }[c.careers_status] || (c.careers_status ? "Sito non raggiungibile al controllo di oggi." : "Non ancora controllata.");
    jobsBlock = `<div class="placeholder">${msg}</div>`;
  }

  return `
    <button class="close" aria-label="Chiudi scheda">✕</button>
    <span class="badge" style="--c:${cat.color}">● ${esc(cat.label)}</span>
    <h2>${esc(c.name)}</h2>
    <div class="loc">${esc(c.city ? c.city + ", " : "")}${esc(c.country)}</div>
    <p class="desc">${esc(cap(c.description))}.</p>
    ${c.status === "acquired" ? `<p class="note">Acquisita da <b>${esc(c.parent)}</b>: le posizioni vanno cercate presso la società madre.</p>` : ""}
    <div class="links">
      ${site ? `<a href="${esc(site)}" target="_blank" rel="noopener">Sito ↗</a>` : ""}
      ${careers ? `<a href="${esc(careers)}" target="_blank" rel="noopener">Carriere ↗</a>` : ""}
      <a href="${nfUrl}" target="_blank" rel="noopener">Neurofounders ↗</a>
    </div>
    ${facts ? `<dl class="facts">${facts}</dl>` : ""}
    ${c.analysis ? `<p class="analysis">${esc(c.analysis)}</p>` : ""}
    <h3>Posizioni aperte${jobs.length ? ` · ${jobs.length}` : ""}</h3>
    ${jobsBlock}
    <h3>News</h3>
    <div class="placeholder">In arrivo con la fase 4: notizie del giorno collegate a questa azienda.</div>
    ${c.location_precision === "country" ? `<p class="note small">Posizione sul globo approssimata al paese.</p>` : ""}
  `;
}

function jobRowSmall(j) {
  const url = safeUrl(j.url);
  return `<li>
    <a href="${esc(url || "#")}" target="_blank" rel="noopener">${esc(j.title)}</a>
    ${isNew(j) ? `<span class="new-badge">nuova</span>` : ""}
    <div class="sub">${esc([j.location, j.remote].filter(Boolean).join(" · "))}</div>
  </li>`;
}

/* ---------- jobs board ---------- */
function setView(view) {
  state.view = view;
  $("tabGlobe").setAttribute("aria-pressed", String(view === "globe"));
  $("tabJobs").setAttribute("aria-pressed", String(view === "jobs"));
  $("board").hidden = view !== "jobs";
  if (view === "jobs") renderBoard();
}

function boardJobs() {
  const b = state.board, q = b.query;
  return state.jobs.filter((j) => {
    const c = state.byId.get(j.company_id);
    if (!state.active.has(c.category)) return false;
    if (state.country && c.country !== state.country) return false;
    if (b.company && j.company_id !== b.company) return false;
    if (b.onlyNew && !isNew(j)) return false;
    if (b.remote && j.remote !== b.remote) return false;
    if (q && ![j.title, j.location, j.department, c.name, c.modality].join(" ").toLowerCase().includes(q)) return false;
    return true;
  }).sort((a, b) => isNew(b) - isNew(a) || Date.parse(b.first_seen_at) - Date.parse(a.first_seen_at)
    || state.byId.get(a.company_id).name.localeCompare(state.byId.get(b.company_id).name));
}

function renderBoard() {
  const jobs = boardJobs();
  const comp = state.board.company && state.byId.get(state.board.company);
  $("boardCompany").hidden = !comp;
  if (comp) $("boardCompanyName").textContent = comp.name;
  const filters = [state.active.size < 6 && "categorie", state.country].filter(Boolean);
  $("boardCount").textContent = `${jobs.length} posizion${jobs.length === 1 ? "e" : "i"}` +
    (filters.length ? ` (filtri del globo attivi: ${filters.join(", ")})` : "");
  $("boardList").innerHTML = jobs.length ? jobs.map((j) => {
    const c = state.byId.get(j.company_id);
    const url = safeUrl(j.url);
    return `<li style="--c:${CATEGORIES[c.category].color}">
      <div class="job-main">
        <a class="job-title" href="${esc(url || "#")}" target="_blank" rel="noopener">${esc(j.title)}</a>
        ${isNew(j) ? `<span class="new-badge">nuova</span>` : ""}
        <div class="sub">
          <a href="#${c.id}" data-company="${c.id}" class="company"><span class="dot"></span>${esc(c.name)}</a>
          ${esc([j.location, j.remote, j.department].filter(Boolean).join(" · "))}
        </div>
      </div>
      <div class="job-meta">${esc(relTime(j.first_seen_at))}</div>
    </li>`;
  }).join("") : `<li class="empty">Nessuna posizione con questi filtri.</li>`;
}

function openFromHash() {
  const id = decodeURIComponent(location.hash.slice(1));
  const c = id && state.byId.get(id);
  if (c && c !== state.selected) { setView("globe"); select(c, true); }
}

init().catch((err) => {
  console.error(err);
  $("stats").textContent = "Errore nel caricamento dei dati (serve un server locale, non file://).";
});
