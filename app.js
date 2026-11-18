/* NeuroRadar — globe with clickable countries, companies dashboard, company
   profiles and jobs board. Static site: everything comes from data/*.json. */

const CATEGORIES = {
  bci:             { label: "Brain-Computer Interface",   color: "#818cf8" },
  consumer:        { label: "Consumer Neurotech",         color: "#f472b6" },
  diagnostics:     { label: "Diagnostics & Assessment",   color: "#34d399" },
  neuroimaging:    { label: "Neuroimaging",               color: "#22d3ee" },
  neuromodulation: { label: "Neuromodulation",            color: "#fbbf24" },
  tools:           { label: "Tools & Infrastructure",     color: "#e5e7eb" },
};
const TEX = "https://cdn.jsdelivr.net/npm/three-globe@2.45.6/example/img/";
const NEW_WINDOW_H = 72; // a job counts as "new" for 3 days after first sighting
const HOME_POV = { lat: 38, lng: -30, altitude: 2.3 };
const GEO_NAME = { USA: "United States of America", UK: "United Kingdom" }; // our name -> GeoJSON name

const TECH_FIELDS = [
  ["modality", "Modality"], ["form_factor", "Form factor"], ["interface_depth", "Invasiveness"],
  ["indication", "Indication"], ["target_user", "Users"], ["regulatory_stage", "Regulatory"],
  ["founded", "Founded"], ["funding_stage", "Funding"],
];
const DASH_FILTERS = [
  ["country", "All countries"], ["city", "All cities"], ["category", "All categories"],
  ["modality", "Any modality"], ["form_factor", "Any form factor"], ["interface_depth", "Any invasiveness"],
  ["regulatory_stage", "Any regulatory stage"], ["funding_stage", "Any funding"],
];
const DOMAINS = {
  hardware_engineering: "Hardware eng.", software_engineering: "Software eng.", ml_ai: "ML / AI",
  research_science: "Research", clinical: "Clinical", regulatory_quality: "Regulatory & QA",
  manufacturing_operations: "Manufacturing & ops", product_design: "Product & design",
  sales_marketing: "Sales & marketing", business_operations: "Business ops", other: "Other",
};
const SENIORITY = ["intern", "junior", "mid", "senior", "lead", "director", "executive"];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
));
const cap = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const plural = (n, w) => `${n} ${n === 1 ? w : w.endsWith("y") ? w.slice(0, -1) + "ies" : w + "s"}`;
const initials = (name) => name.replace(/\(.*?\)/g, "").split(/[\s.-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

const state = {
  companies: [], byId: new Map(), jobs: [], jobsByCompany: new Map(), reqs: {},
  countries: [], countryStats: new Map(),
  active: new Set(Object.keys(CATEGORIES)), country: "", query: "", onlyHiring: false,
  selected: null, hoverCountry: null, view: "globe",
  board: { query: "", onlyNew: false, remote: "", company: "", seniority: "", domain: "" },
  dash: { query: "", hiring: false, sort: "name", country: "", city: "", category: "", modality: "",
          form_factor: "", interface_depth: "", regulatory_stage: "", funding_stage: "" },
};

let globe;

/* ================= boot ================= */
async function init() {
  const get = (url, fallback) => fetch(url, { cache: "no-cache" })
    .then((r) => (r.ok ? r.json() : fallback)).catch(() => fallback);
  const [companies, jobs, reqs, geo] = await Promise.all([
    fetch("data/companies.json", { cache: "no-cache" }).then((r) => r.json()),
    get("data/jobs.json", []), get("data/requirements.json", {}), get("data/countries.geojson", { features: [] }),
  ]);
  state.reqs = reqs;
  state.companies = companies;
  companies.forEach((c) => state.byId.set(c.id, c));
  state.jobs = jobs.filter((j) => state.byId.has(j.company_id));
  state.jobs.forEach((j) => {
    if (!state.jobsByCompany.has(j.company_id)) state.jobsByCompany.set(j.company_id, []);
    state.jobsByCompany.get(j.company_id).push(j);
  });
  computeCountryStats();
  state.countries = geo.features;

  const newCount = state.jobs.filter(isNew).length;
  $("stats").textContent =
    `${companies.length} startups · ${state.countryStats.size} countries · ${plural(state.jobs.length, "open role")}` +
    (newCount ? ` · ${newCount} new` : "");
  $("tabJobs").textContent = `Jobs (${state.jobs.length})`;
  $("tabCompanies").textContent = `Companies (${companies.length})`;

  buildGlobe();
  buildChips();
  buildCountrySelect();
  buildBoardFilters();
  buildDashFilters();
  bindEvents();
  render();
  openFromHash();
}

/* ================= helpers ================= */
function isNew(j) {
  if (j.baseline) return false;
  return (Date.now() - Date.parse(j.first_seen_at)) / 36e5 < NEW_WINDOW_H;
}
const jobsOf = (c) => state.jobsByCompany.get(c.id) || [];
const hasNewJobs = (c) => jobsOf(c).some(isNew);
const reqOf = (j) => { const r = state.reqs[j.key]; return r && r.seniority ? r : null; };
const prof = (c) => c.profile || {};
const geoName = (country) => GEO_NAME[country] || country;

function relTime(iso) {
  if (!iso) return "";
  const d = (Date.now() - Date.parse(iso)) / 864e5;
  if (d < 1) return "today";
  if (d < 2) return "yesterday";
  if (d < 30) return `${Math.floor(d)} days ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function reqChips(j, max = 3) {
  const r = reqOf(j);
  if (!r) return "";
  const chips = [];
  if (r.seniority !== "unknown") chips.push(`<span class="rchip lvl">${esc(cap(r.seniority))}</span>`);
  if (r.domain) chips.push(`<span class="rchip">${esc(DOMAINS[r.domain] || r.domain)}</span>`);
  if (r.min_years_experience >= 0) chips.push(`<span class="rchip">${r.min_years_experience}+ yrs</span>`);
  if (r.degree_required && !["unknown", "none"].includes(r.degree_required))
    chips.push(`<span class="rchip">${esc(r.degree_required === "phd" ? "PhD" : r.degree_required === "md" ? "MD" : cap(r.degree_required))}</span>`);
  (r.required_skills || []).slice(0, max).forEach((k) => chips.push(`<span class="rchip skill">${esc(k)}</span>`));
  if (r.visa_sponsorship === "yes") chips.push(`<span class="rchip ok">Visa sponsorship</span>`);
  return `<div class="rchips">${chips.join("")}</div>`;
}

function avatar(c, size = "") {
  return `<span class="avatar ${size}" style="--c:${CATEGORIES[c.category].color}">${esc(initials(c.name))}</span>`;
}

function locationText(c) {
  return `${c.city ? c.city + ", " : ""}${c.country}`;
}

/* ================= countries ================= */
function computeCountryStats() {
  state.countryStats = new Map();
  for (const c of state.companies) {
    const s = state.countryStats.get(c.country) || { n: 0, roles: 0, byCat: {}, ids: [] };
    s.n += 1;
    s.roles += jobsOf(c).length;
    s.byCat[c.category] = (s.byCat[c.category] || 0) + 1;
    s.ids.push(c.id);
    state.countryStats.set(c.country, s);
  }
}
const ourCountryOf = (feat) => {
  const n = feat.properties.name;
  return [...state.countryStats.keys()].find((k) => geoName(k) === n) || null;
};
const statsOfFeat = (feat) => {
  const k = ourCountryOf(feat);
  return k ? state.countryStats.get(k) : null;
};

function countryColor(feat) {
  const s = statsOfFeat(feat);
  const ours = ourCountryOf(feat);
  if (!s) return "rgba(255,255,255,0.015)";
  const t = Math.min(1, Math.log(1 + s.n) / Math.log(180)); // 1 → faint, USA (179) → strong
  const a = 0.14 + t * 0.5 + (feat === state.hoverCountry ? 0.18 : 0) + (ours === state.country ? 0.2 : 0);
  return `rgba(129,140,248,${a.toFixed(3)})`;
}

/* ================= globe ================= */
function buildGlobe() {
  globe = Globe()($("globe"))
    .globeImageUrl(TEX + "earth-dark.jpg")
    .bumpImageUrl(TEX + "earth-topology.png")
    .backgroundImageUrl(TEX + "night-sky.png")
    .atmosphereColor("#818cf8")
    .atmosphereAltitude(0.18)
    // countries
    .polygonsData(state.countries)
    .polygonAltitude((f) => (f === state.hoverCountry ? 0.012 : 0.006))
    .polygonCapColor(countryColor)
    .polygonSideColor(() => "rgba(129,140,248,0.10)")
    .polygonStrokeColor((f) => (ourCountryOf(f) === state.country && state.country ? "#e0e7ff"
      : statsOfFeat(f) ? "rgba(165,180,252,0.45)" : "rgba(148,163,184,0.12)"))
    .polygonsTransitionDuration(250)
    .polygonLabel((f) => {
      const s = statsOfFeat(f);
      if (!s) return `<div class="tooltip"><b>${esc(f.properties.name)}</b><span>No startups on the map</span></div>`;
      const cats = Object.entries(s.byCat).sort((a, b) => b[1] - a[1]).slice(0, 3)
        .map(([k, n]) => `${n} ${CATEGORIES[k].label}`).join("<br>");
      return `<div class="tooltip"><b>${esc(ourCountryOf(f))}</b>
        <span>${plural(s.n, "startup")} · ${plural(s.roles, "open role")}</span>
        <span class="tt-cats">${cats}</span><span class="tt-hint">Click to explore</span></div>`;
    })
    .onPolygonHover((f) => {
      state.hoverCountry = f && statsOfFeat(f) ? f : null;
      globe.polygonAltitude(globe.polygonAltitude()).polygonCapColor(globe.polygonCapColor());
      $("globe").style.cursor = state.hoverCountry ? "pointer" : "";
    })
    .onPolygonClick((f) => { const k = ourCountryOf(f); if (k) selectCountry(k, true); })
    // companies
    .pointLat("lat").pointLng("lng")
    .pointColor((c) => CATEGORIES[c.category].color)
    .pointAltitude((c) => 0.016 + Math.min(jobsOf(c).length, 40) * 0.005 + (state.selected === c ? 0.05 : 0))
    .pointRadius((c) => (state.selected === c ? 0.32 : 0.2))
    .pointResolution(10)
    .pointsTransitionDuration(300)
    .pointLabel((c) => {
      const n = jobsOf(c).length;
      return `<div class="tooltip"><b>${esc(c.name)}</b>
        <span>${esc(CATEGORIES[c.category].label)} · ${esc(locationText(c))}</span>
        ${n ? `<span class="tt-jobs">${plural(n, "open role")}${hasNewJobs(c) ? " · new!" : ""}</span>` : ""}
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

function refreshPolygons() {
  globe.polygonCapColor(globe.polygonCapColor()).polygonStrokeColor(globe.polygonStrokeColor());
}

/* ================= globe side panel ================= */
function buildChips() {
  const counts = {};
  state.companies.forEach((c) => (counts[c.category] = (counts[c.category] || 0) + 1));
  $("chips").innerHTML = Object.entries(CATEGORIES).map(([key, cat]) => `
    <button class="chip" data-cat="${key}" aria-pressed="true" style="--c:${cat.color}">
      <span class="dot"></span>${esc(cat.label)} <span class="n">${counts[key] || 0}</span>
    </button>`).join("");
}

function buildCountrySelect() {
  const list = [...state.countryStats.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  $("country").innerHTML = `<option value="">All countries</option>` +
    list.map(([c, s]) => `<option value="${esc(c)}">${esc(c)} (${s.n})</option>`).join("");
}

function filtered() {
  const q = state.query;
  return state.companies.filter((c) =>
    state.active.has(c.category) &&
    (!state.country || c.country === state.country) &&
    (!state.onlyHiring || jobsOf(c).length) &&
    (!q || searchText(c).includes(q)));
}

function searchText(c) {
  const p = prof(c);
  return [c.name, c.description, c.country, c.city, c.modality, c.form_factor, c.indication, c.interface_depth,
          p.ceo, p.technology, (p.founders || []).join(" "), (p.products || []).map((x) => x.name).join(" "),
          (p.conditions || []).join(" ")].join(" ").toLowerCase();
}

function render() {
  const items = filtered();
  document.querySelectorAll(".chip").forEach((chip) =>
    chip.setAttribute("aria-pressed", String(state.active.has(chip.dataset.cat))));
  $("country").value = state.country;

  $("resultCount").textContent = `${items.length} companies`;
  $("list").innerHTML = items.slice().sort((a, b) => a.name.localeCompare(b.name)).map((c) => {
    const n = jobsOf(c).length;
    return `<li data-id="${c.id}" aria-selected="${state.selected === c}" style="--c:${CATEGORIES[c.category].color}">
        <span class="dot"></span>
        <div class="txt">
          <div class="name">${esc(c.name)}</div>
          <div class="sub">${esc(locationText(c))} · ${esc(c.modality || c.description)}</div>
        </div>
        ${n ? `<span class="count${hasNewJobs(c) ? " new" : ""}" title="Open roles">${n}</span>` : ""}
      </li>`;
  }).join("");

  globe.pointsData(items);
  const rings = items.filter(hasNewJobs);
  if (state.selected && items.includes(state.selected) && !rings.includes(state.selected)) rings.push(state.selected);
  globe.ringsData(rings);
  refreshPolygons();
}

function flyToFiltered() {
  const items = filtered();
  if (!items.length) return;
  const lat = items.reduce((s, c) => s + c.lat, 0) / items.length;
  const lng = items.reduce((s, c) => s + c.lng, 0) / items.length;
  globe.controls().autoRotate = false;
  const spread = Math.max(...items.map((c) => Math.abs(c.lat - lat) + Math.abs(c.lng - lng)));
  globe.pointOfView({ lat, lng, altitude: state.country ? Math.min(1.6, 0.5 + spread / 25) : 2.3 }, 1200);
}

/* ================= country panel ================= */
function selectCountry(country, fly = false) {
  state.country = country || "";
  state.selected = null;
  render();
  if (fly) flyToFiltered();
  if (!country) { $("card").hidden = true; return; }
  history.replaceState(null, "", `#country/${encodeURIComponent(country)}`);
  const s = state.countryStats.get(country);
  const companies = s.ids.map((id) => state.byId.get(id))
    .sort((a, b) => jobsOf(b).length - jobsOf(a).length || a.name.localeCompare(b.name));
  const bars = Object.entries(s.byCat).sort((a, b) => b[1] - a[1]).map(([k, n]) => `
    <div class="bar-row" data-cat="${k}">
      <span class="bar-label"><span class="dot" style="--c:${CATEGORIES[k].color}"></span>${esc(CATEGORIES[k].label)}</span>
      <span class="bar"><span style="width:${(n / s.n) * 100}%;background:${CATEGORIES[k].color}"></span></span>
      <span class="bar-n">${n}</span>
    </div>`).join("");
  const cities = {};
  companies.forEach((c) => c.city && (cities[c.city] = (cities[c.city] || 0) + 1));
  const topCities = Object.entries(cities).sort((a, b) => b[1] - a[1]).slice(0, 6);

  const card = $("card");
  card.innerHTML = `
    <button class="close" aria-label="Close">✕</button>
    <span class="badge" style="--c:#a5b4fc">● Country</span>
    <h2>${esc(country)}</h2>
    <div class="kpis">
      <div><b>${s.n}</b><span>startups</span></div>
      <div><b>${s.roles}</b><span>open roles</span></div>
      <div><b>${companies.filter((c) => jobsOf(c).length).length}</b><span>hiring</span></div>
    </div>
    <h3>By category</h3>
    <div class="bars">${bars}</div>
    ${topCities.length ? `<h3>Top cities</h3><div class="pills">${topCities.map(([c, n]) =>
      `<span class="pill">${esc(c)} <b>${n}</b></span>`).join("")}</div>` : ""}
    <h3>Companies</h3>
    <ul class="mini-list">${companies.map((c) => `
      <li data-id="${c.id}">${avatar(c, "sm")}
        <div class="txt"><div class="name">${esc(c.name)}</div>
          <div class="sub">${esc(CATEGORIES[c.category].label)}${c.city ? " · " + esc(c.city) : ""}</div></div>
        ${jobsOf(c).length ? `<span class="count${hasNewJobs(c) ? " new" : ""}">${jobsOf(c).length}</span>` : ""}
      </li>`).join("")}</ul>
    <div class="card-actions">
      <button class="ghost-btn small" data-dash-country>Open in dashboard</button>
      <button class="ghost-btn small" data-jobs-country>See ${plural(s.roles, "role")}</button>
    </div>`;
  card.hidden = false;
  card.querySelector(".close").addEventListener("click", () => { selectCountry(""); globe.pointOfView(HOME_POV, 1000); });
  card.querySelector(".mini-list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) select(state.byId.get(li.dataset.id), true);
  });
  card.querySelector(".bars").addEventListener("click", (e) => {
    const row = e.target.closest("[data-cat]");
    if (row) { state.active = new Set([row.dataset.cat]); render(); }
  });
  card.querySelector("[data-dash-country]").addEventListener("click", () => {
    Object.assign(state.dash, { country, city: "" });
    setView("companies");
  });
  card.querySelector("[data-jobs-country]").addEventListener("click", () => setView("jobs"));
}

/* ================= company card (globe) ================= */
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
  card.querySelector("[data-profile]").addEventListener("click", () => openProfile(company));
  card.querySelector("[data-all-jobs]")?.addEventListener("click", () => {
    state.board.company = company.id;
    setView("jobs");
  });
}

function linksHtml(c, withProfile = false) {
  const site = safeUrl(c.website), careers = safeUrl(c.careers_url), li = safeUrl(prof(c).linkedin_url);
  const nfUrl = `https://www.neurofounders.co/startups/${encodeURIComponent(c.neurofounders_slug)}`;
  return `<div class="links">
      ${withProfile ? `<button class="primary-btn" data-profile>Full profile</button>` : ""}
      ${site ? `<a href="${esc(site)}" target="_blank" rel="noopener">Website ↗</a>` : ""}
      ${careers ? `<a href="${esc(careers)}" target="_blank" rel="noopener">Careers ↗</a>` : ""}
      ${li ? `<a href="${esc(li)}" target="_blank" rel="noopener">LinkedIn ↗</a>` : ""}
      <a href="${nfUrl}" target="_blank" rel="noopener">Neurofounders ↗</a>
    </div>`;
}

function jobsMessage(c) {
  return {
    ats: "No open roles right now.",
    custom_page: "No roles listed on the careers page right now.",
    ats_unsupported: `Careers on ${esc((c.ats || {}).type)}: reader coming soon.`,
    no_website: "Website not found.",
    not_found: "No careers page found on the website.",
  }[c.careers_status] || (c.careers_status ? "Website unreachable at the last check." : "Not checked yet.");
}

function cardHtml(c) {
  const cat = CATEGORIES[c.category];
  const p = prof(c);
  const jobs = jobsOf(c).slice().sort((a, b) => isNew(b) - isNew(a) || a.title.localeCompare(b.title));
  return `
    <button class="close" aria-label="Close card">✕</button>
    <div class="card-head">${avatar(c, "lg")}
      <div><span class="badge" style="--c:${cat.color}">● ${esc(cat.label)}</span>
      <h2>${esc(c.name)}</h2>
      <div class="loc">${esc(locationText(c))}${c.founded ? ` · founded ${esc(c.founded)}` : ""}</div></div>
    </div>
    <p class="desc">${esc(p.technology || cap(c.description) + ".")}</p>
    ${p.ceo ? `<p class="ceo"><span>CEO</span> ${esc(p.ceo)}</p>` : ""}
    ${c.status === "acquired" ? `<p class="note">Acquired by <b>${esc(c.parent)}</b>: look for roles at the parent company.</p>` : ""}
    ${linksHtml(c, true)}
    <div class="tags">${[c.modality, c.form_factor, c.interface_depth, c.regulatory_stage, c.funding_stage]
      .filter(Boolean).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    <h3>Open roles${jobs.length ? ` · ${jobs.length}` : ""}</h3>
    ${jobs.length ? `<ul class="jobs">${jobs.slice(0, 6).map(jobRowSmall).join("")}</ul>` +
      (jobs.length > 6 ? `<button class="link-btn" data-all-jobs>See all ${jobs.length} roles →</button>` : "")
      : `<div class="placeholder">${jobsMessage(c)}</div>`}
    ${c.location_precision === "country" ? `<p class="note small">Globe position approximated to the country.</p>` : ""}
  `;
}

function jobRowSmall(j) {
  const url = safeUrl(j.url);
  return `<li>
    <a href="${esc(url || "#")}" target="_blank" rel="noopener">${esc(j.title)}</a>
    ${isNew(j) ? `<span class="new-badge">new</span>` : ""}
    <div class="sub">${esc([j.location, j.remote].filter(Boolean).join(" · "))}</div>
    ${reqChips(j, 2)}
  </li>`;
}

const isNewCompany = (c) => c.added_at && Date.now() - new Date(c.added_at) < 30 * 864e5;

/* People: CEO, founders and leadership, each with their LinkedIn.
   A direct link only when the company's own website links that profile;
   otherwise a LinkedIn search for "name + company". */
const personKey = (n) => n.split(",")[0].toLowerCase().replace(/\b(dr|prof|mr|ms|mrs)\.?\s+/g, "").replace(/\s+/g, " ").trim();
function peopleHtml(c) {
  const p = prof(c), li = p.people_linkedin || {};
  const people = new Map();
  const add = (name, role) => {
    if (!name) return;
    const k = personKey(name), cur = people.get(k);
    if (cur) { if (role && !cur.roles.includes(role)) cur.roles.push(role); }
    else people.set(k, { name, roles: role ? [role] : [], url: null });
  };
  if (p.ceo) add(p.ceo, "CEO");
  (p.founders || []).forEach((n) => add(n, "Founder"));
  (p.leadership || []).forEach((x) => add(x.name, x.role));
  for (const [n, url] of Object.entries(li)) { const e = people.get(personKey(n)); if (e && safeUrl(url)) e.url = url; }
  if (!people.size) return "";
  // "CEO" + "Co-founder & CEO" -> keep the most descriptive roles only
  const roles = (rs) => rs.filter((r) => !rs.some((o) => o !== r && o.toLowerCase().includes(r.toLowerCase()))).join(" · ");
  return `<section><h3>People</h3><ul class="people">${[...people.values()].map((x) => {
    const q = encodeURIComponent(`${x.name.split(",")[0]} ${c.name}`);
    const link = x.url
      ? `<a class="li-btn" href="${esc(x.url)}" target="_blank" rel="noopener" title="LinkedIn profile (linked on the company website)">in</a>`
      : `<a class="li-btn search" href="https://www.linkedin.com/search/results/people/?keywords=${q}" target="_blank" rel="noopener" title="Search on LinkedIn">in<i>⌕</i></a>`;
    return `<li><div><b>${esc(x.name)}</b><span>${esc(roles(x.roles))}</span></div>${link}</li>`;
  }).join("")}</ul></section>`;
}

/* ================= full profile (modal) ================= */
function openProfile(c) {
  const p = prof(c), cat = CATEGORIES[c.category];
  const jobs = jobsOf(c).slice().sort((a, b) => isNew(b) - isNew(a) || a.title.localeCompare(b.title));
  const facts = [
    ["Headquarters", locationText(c)],
    ["CEO", p.ceo],
    ["Founders", (p.founders || []).join(", ")],
    ["Founded", c.founded], ["Funding stage", c.funding_stage], ["Team size", p.team_size],
    ...TECH_FIELDS.filter(([k]) => !["founded", "funding_stage"].includes(k)).map(([k, l]) => [l, c[k]]),
  ].filter(([, v]) => v);
  const related = (c.related || []).map((n) => state.companies.find((x) => x.name === n)).filter(Boolean);
  const updated = p.updated_at ? new Date(p.updated_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";

  $("profileBody").innerHTML = `
    <div class="pf-head">
      ${avatar(c, "xl")}
      <div class="pf-title">
        <span class="badge" style="--c:${cat.color}">● ${esc(cat.label)}</span>
        <h2 id="profileTitle">${esc(c.name)}</h2>
        <div class="loc">${esc(locationText(c))}</div>
        ${linksHtml(c)}
      </div>
    </div>
    ${c.status === "acquired" ? `<p class="note">Acquired by <b>${esc(c.parent)}</b>.</p>` : ""}
    <div class="pf-grid">
      <div class="pf-main">
        <section><h3>Technology</h3><p class="pf-lead">${esc(p.technology || cap(c.description) + ".")}</p></section>
        ${(p.products || []).length ? `<section><h3>Products</h3><ul class="pf-products">${p.products.map((x) =>
          `<li><b>${esc(x.name)}</b><span>${esc(x.description)}</span></li>`).join("")}</ul></section>` : ""}
        ${(p.conditions || []).length ? `<section><h3>Conditions & use cases</h3><div class="pills">${p.conditions.map((x) =>
          `<span class="pill">${esc(x)}</span>`).join("")}</div></section>` : ""}
        ${(p.milestones || []).length ? `<section><h3>Milestones</h3><ul class="pf-milestones">${p.milestones.map((x) =>
          `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
        <section><h3>Open roles${jobs.length ? ` · ${jobs.length}` : ""}</h3>
          ${jobs.length ? `<ul class="jobs">${jobs.map(jobRowSmall).join("")}</ul>` : `<div class="placeholder">${jobsMessage(c)}</div>`}
        </section>
      </div>
      <aside class="pf-side">
        <section><h3>Key facts</h3><dl class="facts one">${facts.map(([k, v]) =>
          `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></section>
        ${peopleHtml(c)}
        ${related.length ? `<section><h3>Related companies</h3><div class="pills">${related.map((r) =>
          `<button class="pill link" data-related="${r.id}">${esc(r.name)}</button>`).join("")}</div></section>` : ""}
        <p class="note small">${p.updated_at
          ? `Profile extracted by Claude from the company website on ${esc(updated)}. Technical data from Neurofounders.`
          : "Profile from Neurofounders data; website details coming soon."}</p>
        <button class="ghost-btn small" data-show-globe>Show on globe</button>
      </aside>
    </div>`;
  const dlg = $("profile");
  if (!dlg.open) dlg.showModal();
  $("profileBody").scrollTop = 0;
  history.replaceState(null, "", `#profile/${c.id}`);
  $("profileBody").querySelectorAll("[data-related]").forEach((b) =>
    b.addEventListener("click", () => openProfile(state.byId.get(b.dataset.related))));
  $("profileBody").querySelector("[data-show-globe]").addEventListener("click", () => {
    dlg.close();
    setView("globe");
    select(c, true);
  });
}

/* ================= views ================= */
function setView(view) {
  state.view = view;
  for (const [tab, v] of [["tabGlobe", "globe"], ["tabCompanies", "companies"], ["tabJobs", "jobs"]])
    $(tab).setAttribute("aria-pressed", String(view === v));
  $("board").hidden = view !== "jobs";
  $("dash").hidden = view !== "companies";
  if (view === "jobs") renderBoard();
  if (view === "companies") renderDash();
}

/* ================= companies dashboard ================= */
function buildDashFilters() {
  $("dashFilters").innerHTML = DASH_FILTERS.map(([k, label]) =>
    `<select data-f="${k}" aria-label="${esc(label)}"><option value="">${esc(label)}</option></select>`).join("");
  $("dashFilters").addEventListener("change", (e) => {
    const k = e.target.dataset.f;
    if (!k) return;
    state.dash[k] = e.target.value;
    if (k === "country") state.dash.city = "";
    renderDash();
  });
}

function dashMatches(c, except = null) {
  const d = state.dash;
  for (const [k] of DASH_FILTERS) {
    if (k === except || !d[k]) continue;
    const v = k === "category" ? c.category : c[k];
    if (v !== d[k]) return false;
  }
  if (d.hiring && !jobsOf(c).length) return false;
  if (d.query && !searchText(c).includes(d.query)) return false;
  return true;
}

function fillDashOptions() {
  // each dropdown shows the values (with counts) still possible under the other filters
  for (const [k, label] of DASH_FILTERS) {
    const sel = $("dashFilters").querySelector(`[data-f="${k}"]`);
    const counts = {};
    state.companies.filter((c) => dashMatches(c, k)).forEach((c) => {
      const v = k === "category" ? c.category : c[k];
      if (v) counts[v] = (counts[v] || 0) + 1;
    });
    const opts = Object.entries(counts).sort((a, b) => k === "category" ? 0 : a[0].localeCompare(b[0]));
    sel.innerHTML = `<option value="">${esc(label)}</option>` + opts.map(([v, n]) =>
      `<option value="${esc(v)}">${esc(k === "category" ? CATEGORIES[v].label : v)} (${n})</option>`).join("");
    sel.value = state.dash[k] && counts[state.dash[k]] ? state.dash[k] : "";
    if (!sel.value) state.dash[k] = "";
    sel.classList.toggle("active", !!sel.value);
  }
}

function renderDash() {
  fillDashOptions();
  const d = state.dash;
  const items = state.companies.filter((c) => dashMatches(c));
  const sorters = {
    name: (a, b) => a.name.localeCompare(b.name),
    roles: (a, b) => jobsOf(b).length - jobsOf(a).length || a.name.localeCompare(b.name),
    newest: (a, b) => (b.founded || 0) - (a.founded || 0) || a.name.localeCompare(b.name),
    oldest: (a, b) => (a.founded || 9999) - (b.founded || 9999) || a.name.localeCompare(b.name),
  };
  items.sort(sorters[d.sort] || sorters.name);
  const roles = items.reduce((s, c) => s + jobsOf(c).length, 0);
  $("dashCount").textContent = `${plural(items.length, "company")} · ${plural(roles, "open role")}`;
  $("dashGrid").innerHTML = items.length ? items.map((c) => {
    const cat = CATEGORIES[c.category], n = jobsOf(c).length, p = prof(c);
    const site = safeUrl(c.website), careers = safeUrl(c.careers_url);
    return `<article class="co" style="--c:${cat.color}">
      <header>${avatar(c)}
        <div class="co-title"><h3>${esc(c.name)}${isNewCompany(c) ? ` <span class="new-co" title="Added from the Neurofounders start-up map on ${esc(c.added_at)}">New</span>` : ""}</h3>
          <div class="sub">${esc(locationText(c))}${c.founded ? " · " + esc(c.founded) : ""}</div></div>
        ${n ? `<span class="count${hasNewJobs(c) ? " new" : ""}" title="Open roles">${n} role${n === 1 ? "" : "s"}</span>` : ""}
      </header>
      <span class="badge" style="--c:${cat.color}">● ${esc(cat.label)}</span>
      <p>${esc(p.technology ? p.technology.split(". ")[0].replace(/\.$/, "") + "." : cap(c.description) + ".")}</p>
      ${p.ceo ? `<div class="sub ceo-line">CEO · ${esc(p.ceo)}</div>` : ""}
      <div class="tags">${[c.modality, c.form_factor, c.interface_depth].filter(Boolean).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      <footer>
        <button class="primary-btn small" data-open="${c.id}">Profile</button>
        ${site ? `<a href="${esc(site)}" target="_blank" rel="noopener">Website ↗</a>` : ""}
        ${careers ? `<a href="${esc(careers)}" target="_blank" rel="noopener">Careers ↗</a>` : ""}
        <button class="link-btn" data-globe="${c.id}">Globe</button>
      </footer>
    </article>`;
  }).join("") : `<p class="empty">No companies match these filters.</p>`;
}

/* ================= jobs board ================= */
function buildBoardFilters() {
  $("boardSeniority").innerHTML = `<option value="">Any level</option>` +
    SENIORITY.map((k) => `<option value="${k}">${cap(k)}</option>`).join("");
  $("boardDomain").innerHTML = `<option value="">Any area</option>` +
    Object.entries(DOMAINS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
}

function boardJobs() {
  const b = state.board, q = b.query;
  return state.jobs.filter((j) => {
    const c = state.byId.get(j.company_id);
    if (!state.active.has(c.category)) return false;
    if (state.country && c.country !== state.country) return false;
    if (b.company && j.company_id !== b.company) return false;
    if (b.onlyNew && !isNew(j)) return false;
    const r = reqOf(j);
    const mode = j.remote || (r && ["remote", "hybrid"].includes(r.work_mode) ? r.work_mode : null);
    if (b.remote && mode !== b.remote) return false;
    if (b.seniority && (!r || r.seniority !== b.seniority)) return false;
    if (b.domain && (!r || r.domain !== b.domain)) return false;
    const skills = r ? [...r.required_skills, ...r.preferred_skills].join(" ") : "";
    if (q && ![j.title, j.location, j.department, c.name, c.modality, c.city, skills].join(" ").toLowerCase().includes(q)) return false;
    return true;
  }).sort((a, b) => isNew(b) - isNew(a) || Date.parse(b.first_seen_at) - Date.parse(a.first_seen_at)
    || state.byId.get(a.company_id).name.localeCompare(state.byId.get(b.company_id).name));
}

function renderBoard() {
  const jobs = boardJobs();
  const comp = state.board.company && state.byId.get(state.board.company);
  $("boardCompany").hidden = !comp;
  if (comp) $("boardCompanyName").textContent = comp.name;
  const filters = [state.active.size < 6 && "categories", state.country].filter(Boolean);
  $("boardCount").textContent = plural(jobs.length, "role") +
    (filters.length ? ` (globe filters active: ${filters.join(", ")})` : "");
  $("boardList").innerHTML = jobs.length ? jobs.map((j) => {
    const c = state.byId.get(j.company_id);
    const url = safeUrl(j.url);
    return `<li style="--c:${CATEGORIES[c.category].color}">
      <div class="job-main">
        <a class="job-title" href="${esc(url || "#")}" target="_blank" rel="noopener">${esc(j.title)}</a>
        ${isNew(j) ? `<span class="new-badge">new</span>` : ""}
        <div class="sub">
          <a href="#profile/${c.id}" data-company="${c.id}" class="company"><span class="dot"></span>${esc(c.name)}</a>
          ${esc([j.location, j.remote, j.department].filter(Boolean).join(" · "))}
        </div>
        ${reqChips(j)}
        ${reqOf(j)?.summary ? `<div class="rsummary">${esc(reqOf(j).summary)}</div>` : ""}
      </div>
      <div class="job-meta">${esc(relTime(j.first_seen_at))}</div>
    </li>`;
  }).join("") : `<li class="empty">No roles match these filters.</li>`;
}

/* ================= events ================= */
function bindEvents() {
  // globe panel
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
  $("country").addEventListener("change", (e) => {
    if (e.target.value) selectCountry(e.target.value, true);
    else { selectCountry(""); globe.pointOfView(HOME_POV, 1000); }
  });
  $("search").addEventListener("input", (e) => { state.query = e.target.value.trim().toLowerCase(); render(); });
  $("onlyHiring").addEventListener("change", (e) => { state.onlyHiring = e.target.checked; render(); });
  $("reset").addEventListener("click", () => {
    Object.assign(state, { active: new Set(Object.keys(CATEGORIES)), country: "", query: "", onlyHiring: false });
    $("search").value = ""; $("onlyHiring").checked = false;
    select(null);
    globe.pointOfView(HOME_POV, 1000);
  });
  $("list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    select(state.byId.get(li.dataset.id), true);
    if (window.matchMedia("(max-width: 760px)").matches) togglePanel(false);
  });
  $("togglePanel").addEventListener("click", () => togglePanel($("panel").classList.contains("collapsed")));

  // tabs
  $("tabGlobe").addEventListener("click", () => setView("globe"));
  $("tabCompanies").addEventListener("click", () => setView("companies"));
  $("tabJobs").addEventListener("click", () => setView("jobs"));

  // dashboard
  $("dashSearch").addEventListener("input", (e) => { state.dash.query = e.target.value.trim().toLowerCase(); renderDash(); });
  $("dashHiring").addEventListener("change", (e) => { state.dash.hiring = e.target.checked; renderDash(); });
  $("dashSort").addEventListener("change", (e) => { state.dash.sort = e.target.value; renderDash(); });
  $("dashReset").addEventListener("click", () => {
    Object.keys(state.dash).forEach((k) => (state.dash[k] = k === "sort" ? "name" : k === "hiring" ? false : ""));
    $("dashSearch").value = ""; $("dashHiring").checked = false; $("dashSort").value = "name";
    renderDash();
  });
  $("dashGrid").addEventListener("click", (e) => {
    const open = e.target.closest("[data-open]");
    if (open) return openProfile(state.byId.get(open.dataset.open));
    const g = e.target.closest("[data-globe]");
    if (g) { setView("globe"); select(state.byId.get(g.dataset.globe), true); }
  });

  // jobs board
  $("boardSearch").addEventListener("input", (e) => { state.board.query = e.target.value.trim().toLowerCase(); renderBoard(); });
  $("boardNew").addEventListener("change", (e) => { state.board.onlyNew = e.target.checked; renderBoard(); });
  $("boardRemote").addEventListener("change", (e) => { state.board.remote = e.target.value; renderBoard(); });
  $("boardSeniority").addEventListener("change", (e) => { state.board.seniority = e.target.value; renderBoard(); });
  $("boardDomain").addEventListener("change", (e) => { state.board.domain = e.target.value; renderBoard(); });
  $("boardClearCompany").addEventListener("click", () => { state.board.company = ""; renderBoard(); });
  $("board").addEventListener("click", (e) => {
    const a = e.target.closest("[data-company]");
    if (!a) return;
    e.preventDefault();
    openProfile(state.byId.get(a.dataset.company));
  });

  // profile modal
  $("profileClose").addEventListener("click", () => $("profile").close());
  $("profile").addEventListener("click", (e) => { if (e.target === $("profile")) $("profile").close(); });
  $("profile").addEventListener("close", () => {
    if (location.hash.startsWith("#profile/")) history.replaceState(null, "", location.pathname);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || $("profile").open) return; // the dialog handles its own Escape
    if (state.view !== "globe") setView("globe"); else select(null);
  });
  window.addEventListener("hashchange", openFromHash);
}

function togglePanel(open) {
  $("panel").classList.toggle("collapsed", !open);
  $("togglePanel").setAttribute("aria-expanded", String(open));
}

function openFromHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("profile/")) {
    const c = state.byId.get(h.slice(8));
    if (c) openProfile(c);
  } else if (h.startsWith("country/")) {
    const k = h.slice(8);
    if (state.countryStats.has(k)) { setView("globe"); selectCountry(k, true); }
  } else {
    const c = h && state.byId.get(h);
    if (c && c !== state.selected) { setView("globe"); select(c, true); }
  }
}

init().catch((err) => {
  console.error(err);
  $("stats").textContent = "Could not load the data (serve it over HTTP, not file://).";
});
