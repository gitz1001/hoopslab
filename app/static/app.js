/* Hoops Lab front end: a hash-routed single page app over the Flask JSON API. */
"use strict";

const S = { meta: null, season: null, charts: [], navId: 0 };
const view = document.getElementById("view");

// ------------------------------------------------------------------ utilities

const cache = new Map();
// A view that is still awaiting data when the user navigates away must not render over the
// new page, so responses that arrive after a navigation never resolve for the stale view.
async function api(path) {
  const id = S.navId;
  const data = await fetchCached(path);
  return id === S.navId ? data : new Promise(() => {});
}
function fetchCached(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch("/api/" + path).then(r => {
    if (!r.ok) throw new Error(`${r.status} on ${path}`);
    return r.json();
  });
  cache.set(path, p);
  p.catch(() => cache.delete(path));
  return p;
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmt(v, f) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (typeof v !== "number") return esc(v);
  switch (f) {
    case "i": return Math.round(v).toLocaleString();
    case "p": return (v * 100).toFixed(1);
    case "2": return v.toFixed(2);
    case "3": return v.toFixed(3).replace(/^0\./, ".");
    default: return v.toFixed(1);
  }
}
const pstat = k => S.meta.player_stats[k] || { label: k, fmt: "1" };
const tstat = k => S.meta.team_stats[k] || { label: k, fmt: "1" };
const qs = o => Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const series = i => css(`--s${(i % 8) + 1}`);
const playerLink = (id, name, season) =>
  `<a href="#/player/${id}${season ? "?season=" + season : ""}">${esc(name)}</a>`;
const teamLink = (id, abbr, season) =>
  id ? `<a href="#/team/${id}${season ? "?season=" + season : ""}">${esc(abbr)}</a>` : esc(abbr);

function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; }
function setView(html) { destroyCharts(); view.innerHTML = html; window.scrollTo(0, 0); }
function destroyCharts() { S.charts.forEach(c => c.destroy()); S.charts = []; }

function rolling(arr, n) {
  return arr.map((_, i) => {
    const w = arr.slice(Math.max(0, i - n + 1), i + 1).filter(x => x !== null);
    return w.length ? w.reduce((a, b) => a + b, 0) / w.length : null;
  });
}

// ------------------------------------------------------------------ charts

function baseOptions(extra = {}) {
  const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid");
  return Object.assign({
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: {
      legend: { labels: { color: ink2, boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 3 } },
      tooltip: { backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: ink2,
        borderColor: css("--axis"), borderWidth: 1, padding: 10, boxPadding: 4 },
    },
    scales: {
      x: { ticks: { color: muted }, grid: { color: grid, drawTicks: false }, border: { color: css("--axis") } },
      y: { ticks: { color: muted }, grid: { color: grid, drawTicks: false }, border: { display: false } },
    },
  }, extra);
}

function chart(canvasId, config) {
  const el = document.getElementById(canvasId);
  if (!el) return null;
  const c = new Chart(el, config);
  S.charts.push(c);
  return c;
}

function lineDs(label, data, i, extra = {}) {
  const col = series(i);
  return Object.assign({ label, data, borderColor: col, backgroundColor: col, borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 5, tension: 0.25, spanGaps: true }, extra);
}

// scatter with a label on selected points
const labelPlugin = {
  id: "pointLabels",
  afterDatasetsDraw(c) {
    const opts = c.options.plugins.pointLabels;
    if (!opts || !opts.enabled) return;
    const ctx = c.ctx;
    ctx.save();
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = css("--ink-2");
    c.data.datasets.forEach((ds, di) => {
      const meta = c.getDatasetMeta(di);
      if (meta.hidden) return;
      ds.data.forEach((d, i) => {
        if (!d.label || (opts.filter && !opts.filter(d))) return;
        const p = meta.data[i];
        ctx.fillText(d.label, p.x + 6, p.y - 5);
      });
    });
    ctx.restore();
  },
};
Chart.register(labelPlugin);

// ------------------------------------------------------------------ table component

/**
 * cols: [{key, label, fmt, cls, render(row), sortVal(row)}]
 * opts: {sort, asc, page, rank}
 */
function table(container, rows, cols, opts = {}) {
  if (!container) return;
  const st = { sort: opts.sort ?? null, asc: opts.asc ?? false, page: 0, size: opts.page || 0 };
  function sorted() {
    if (!st.sort) return rows;
    const col = cols.find(c => c.key === st.sort) || {};
    const val = col.sortVal || (r => r[st.sort]);
    return [...rows].sort((a, b) => {
      const x = val(a), y = val(b);
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      const r = typeof x === "string" ? x.localeCompare(y) : x - y;
      return st.asc ? r : -r;
    });
  }
  function draw() {
    const data = sorted();
    const pages = st.size ? Math.ceil(data.length / st.size) : 1;
    const slice = st.size ? data.slice(st.page * st.size, (st.page + 1) * st.size) : data;
    const head = (opts.rank ? `<th class="l">#</th>` : "") + cols.map(c =>
      `<th data-k="${c.key}" class="${c.cls || ""} ${st.sort === c.key ? "sorted" + (st.asc ? " asc" : "") : ""}"
        title="${esc(c.title || c.label)}">${esc(c.label)}</th>`).join("");
    const body = slice.map((r, i) => `<tr>${opts.rank ? `<td class="rank l">${st.page * st.size + i + 1}</td>` : ""}${cols.map(c =>
      `<td class="${c.cls || ""}">${c.render ? c.render(r) : fmt(r[c.key], c.fmt)}</td>`).join("")}</tr>`).join("");
    container.innerHTML = `<div class="table-wrap ${opts.short ? "short" : ""}"><table><thead><tr>${head}</tr></thead>
      <tbody>${body || `<tr><td class="empty" colspan="${cols.length + 1}">No rows</td></tr>`}</tbody></table></div>
      ${pages > 1 ? `<div class="pager"><button data-p="-1">Prev</button>
      <span class="muted small">Page ${st.page + 1} of ${pages} · ${data.length} rows</span><button data-p="1">Next</button></div>` : ""}`;
    container.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
      const k = th.dataset.k;
      if (st.sort === k) st.asc = !st.asc; else { st.sort = k; st.asc = typeof rows[0]?.[k] === "string"; }
      st.page = 0; draw();
    });
    container.querySelectorAll("[data-p]").forEach(b => b.onclick = () => {
      st.page = Math.min(Math.max(0, st.page + +b.dataset.p), pages - 1); draw();
    });
  }
  draw();
}

const statCol = (k, cat = "p") => {
  const m = cat === "p" ? pstat(k) : tstat(k);
  return { key: k, label: m.label, fmt: m.fmt, title: `${m.label} (${m.group || ""})` };
};

const PRESETS = {
  Basic: ["gp", "min_pg", "pts_pg", "reb_pg", "ast_pg", "stl_pg", "blk_pg", "tov_pg", "fg3m_pg", "fg_pct", "fg3_pct", "ft_pct", "plus_minus"],
  Shooting: ["gp", "fga_pg", "fg_pct", "fg2_pct", "fg3a_pg", "fg3_pct", "fta_pg", "ft_pct", "efg_pct", "ts_pct", "fg3a_rate", "fta_rate"],
  Advanced: ["gp", "min", "usg_pct", "ts_pct", "ast_pct", "oreb_pct", "dreb_pct", "reb_pct", "tm_tov_pct", "off_rating", "def_rating", "net_rating", "pie", "per_calc", "gmsc_avg"],
  "Per 36": ["gp", "min", "pts_p36", "reb_p36", "ast_p36", "stl_p36", "blk_p36", "tov_p36"],
  "Per 100": ["gp", "min", "pts_p100", "reb_p100", "ast_p100", "stl_p100", "blk_p100", "tov_p100", "fg3a_p100", "fta_p100"],
  Impact: ["gp", "min", "per", "ws", "ws_per_48", "obpm", "dbpm", "bpm", "vorp", "on_net_rating", "off_net_rating", "net_diff"],
  Totals: ["gp", "min", "pts", "reb", "ast", "stl", "blk", "fg3m", "ftm", "dd2", "td3"],
  "Shot zones": ["gp", "fga_pg", "ra_share", "ra_fg_pct", "paint_share", "paint_fg_pct", "mid_share", "mid_fg_pct", "c3_share", "c3_fg_pct", "atb3_share", "atb3_fg_pct"],
  Clutch: ["gp", "clutch_min", "clutch_pts", "clutch_pts_p36", "clutch_ts_pct", "clutch_plus_minus"],
  Hustle: ["gp", "min", "deflections_p36", "contested_shots_p36", "screen_assists_p36", "loose_balls_recovered_p36", "box_outs_p36", "charges_drawn"],
};
const ZONES = [["ra", "Restricted area"], ["paint", "Paint (non-RA)"], ["mid", "Mid-range"], ["c3", "Corner 3"], ["atb3", "Above-break 3"]];
const height = i => i ? `${Math.floor(i / 12)}'${i % 12}"` : "";
const TEAM_PRESETS = {
  Ratings: ["w", "l", "w_pct", "off_rating", "def_rating", "net_rating", "pace", "pyth_w", "luck"],
  "Four factors": ["w", "l", "efg_pct", "tm_tov_pct", "oreb_pct", "fta_rate", "opp_efg_pct", "opp_tov_pct", "opp_oreb_pct", "opp_fta_rate"],
  Scoring: ["w", "l", "pts_pg", "opp_pts_pg", "reb_pg", "ast_pg", "tov_pg", "fg3a_pg", "fg_pct", "fg3_pct", "ts_pct", "fg3a_rate"],
};

function pills(container, options, current, onPick) {
  container.innerHTML = options.map(o => `<button class="${o === current ? "on" : ""}" data-o="${esc(o)}">${esc(o)}</button>`).join("");
  container.querySelectorAll("button").forEach(b => b.onclick = () => {
    container.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    onPick(b.dataset.o);
  });
}

function statOptions(catalog, selected, filter = () => true) {
  const groups = {};
  Object.entries(catalog).filter(([k]) => filter(k)).forEach(([k, m]) => (groups[m.group || "Stats"] ||= []).push([k, m]));
  return Object.entries(groups).map(([g, items]) => `<optgroup label="${esc(g)}">${items.map(([k, m]) =>
    `<option value="${k}" ${k === selected ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</optgroup>`).join("");
}

// ------------------------------------------------------------------ views

async function vDashboard() {
  const d = await api(`dashboard?season=${S.season}`);
  const lg = d.league[0] || {}, pv = d.prev[0];
  const kpi = (k, label, f, better = true) => {
    const v = lg[k], p = pv ? pv[k] : null;
    let delta = "";
    if (p !== null && p !== undefined && v !== undefined) {
      const diff = v - p, dir = diff === 0 || better === null ? "" : (diff > 0) === better ? "up" : "down";
      delta = `<div class="d"><span class="${dir}">${diff > 0 ? "▲" : "▼"} ${f === "p" ? (Math.abs(diff) * 100).toFixed(1) + " pts" : Math.abs(diff).toFixed(1)}</span> vs ${pv.season}</div>`;
    }
    return `<div class="tile"><div class="k">${label}</div><div class="v">${fmt(v, f)}${f === "p" ? "%" : ""}</div>${delta}</div>`;
  };
  const leaderCard = (k) => {
    const m = pstat(k);
    return `<div class="card"><h3>${esc(m.label)}</h3><ul class="leader-list">${(d.leaders[k] || []).map(r =>
      `<li><span>${playerLink(r.player_id, r.player_name, S.season)} <span class="muted small">${esc(r.team_abbreviation)}</span></span>
       <span class="val">${fmt(r.v, m.fmt)}</span></li>`).join("")}</ul></div>`;
  };
  setView(`
    <div class="hero"><div><h1>${esc(S.season)} season at a glance</h1>
      <p class="sub">League-wide numbers, the top performers and how every team rated on both ends.</p></div></div>
    <div class="tiles">
      ${kpi("pts_pg", "Points per team game", "1")}${kpi("pace", "Pace (poss / 48)", "1", null)}
      ${kpi("ortg", "Offensive rating", "1")}${kpi("ts_pct", "True shooting", "p")}
      ${kpi("fg3a_rate", "3-point attempt rate", "p", null)}${kpi("fg3_pct", "3P%", "p")}
    </div>
    <div class="grid g4 section">${["pts_pg", "reb_pg", "ast_pg", "ts_pct", "per", "bpm", "ws", "net_diff"].map(leaderCard).join("")}</div>
    <p class="note">Per-game leaders need 58 games; rate and impact leaders need 1,500 minutes.</p>
    <div class="grid g2 section">
      <div class="card"><div class="card-head"><h2>Team ratings</h2><span class="muted small">Up and right is better</span></div>
        <div class="chart-box tall"><canvas id="c-teams"></canvas></div>
        <p class="note">Defensive rating axis is flipped so better defenses sit higher. Dashed lines are league averages.</p></div>
      <div class="card flush"><div class="card-head"><h2>Net rating ranking</h2></div><div id="t-teams"></div></div>
    </div>`);
  const teams = d.teams;
  const avgO = teams.reduce((a, t) => a + t.off_rating, 0) / teams.length;
  const avgD = teams.reduce((a, t) => a + t.def_rating, 0) / teams.length;
  const byConf = ["East", "West"];
  chart("c-teams", {
    type: "scatter",
    data: { datasets: byConf.map((cf, i) => ({
      label: cf, backgroundColor: series(i), borderColor: css("--surface"), borderWidth: 2, pointRadius: 7, pointHoverRadius: 9,
      data: teams.filter(t => t.conference === cf).map(t => ({ x: t.off_rating, y: t.def_rating, label: t.team_abbr, t })),
    })) },
    options: baseOptions({
      plugins: { ...baseOptions().plugins, pointLabels: { enabled: true },
        tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: c => {
          const t = c.raw.t; return `${t.team_name}: ${t.w}-${t.l}, ORtg ${t.off_rating}, DRtg ${t.def_rating}, Net ${t.net_rating > 0 ? "+" : ""}${t.net_rating}`; } } },
        annotationLines: { x: avgO, y: avgD } },
      scales: { x: { ...baseOptions().scales.x, title: { display: true, text: "Offensive rating", color: css("--muted") } },
        y: { ...baseOptions().scales.y, reverse: true, title: { display: true, text: "Defensive rating", color: css("--muted") } } },
      onClick: (e, els) => { if (els[0]) { const t = teams.filter(x => x.conference === byConf[els[0].datasetIndex])[els[0].index]; location.hash = `#/team/${t.team_id}?season=${S.season}`; } },
    }),
    plugins: [avgLines],
  });
  table(document.getElementById("t-teams"), teams, [
    { key: "team_abbr", label: "Team", cls: "l", render: r => teamLink(r.team_id, r.team_name, S.season) },
    { key: "w", label: "W", fmt: "i" }, { key: "l", label: "L", fmt: "i" },
    { key: "off_rating", label: "ORtg", fmt: "1" }, { key: "def_rating", label: "DRtg", fmt: "1" },
    { key: "net_rating", label: "Net", fmt: "1", render: r => `<span class="${r.net_rating >= 0 ? "up" : "down"}">${r.net_rating > 0 ? "+" : ""}${fmt(r.net_rating, "1")}</span>` },
    { key: "pace", label: "Pace", fmt: "1" },
  ], { sort: "net_rating", rank: true, short: false });
}

const avgLines = {
  id: "avgLines",
  afterDraw(c) {
    const o = c.options.plugins.annotationLines;
    if (!o) return;
    const { ctx, chartArea: a, scales } = c;
    ctx.save();
    ctx.strokeStyle = css("--axis"); ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
    if (o.x !== undefined) { const x = scales.x.getPixelForValue(o.x); ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke(); }
    if (o.y !== undefined) { const y = scales.y.getPixelForValue(o.y); ctx.beginPath(); ctx.moveTo(a.left, y); ctx.lineTo(a.right, y); ctx.stroke(); }
    ctx.restore();
  },
};

// ---------------- players list
async function vPlayers(params) {
  const preset = params.get("view") || "Basic";
  setView(`
    <div class="hero"><div><h1>Players</h1><p class="sub">Every player in ${esc(S.season)}. Click a column to sort; switch views for shooting, advanced and impact metrics.</p></div></div>
    <div class="toolbar">
      <div class="pills" id="presets"></div>
      <label>Min games <input type="number" id="mingp" value="${params.get("min_gp") || 20}"></label>
      <label>Min minutes <input type="number" id="minmin" value="${params.get("min_min") || 0}"></label>
      <label>Team <select id="team"><option value="">All</option></select></label>
      <input type="text" id="filter" placeholder="Filter by name">
    </div>
    <div class="card flush" id="tbl"><div class="loading">Loading…</div></div>`);
  const d = await api(`players?season=${S.season}`);
  const teams = [...new Set(d.rows.map(r => r.team_abbreviation))].sort();
  document.getElementById("team").innerHTML += teams.map(t => `<option>${t}</option>`).join("");
  let current = preset;
  const draw = () => {
    const mingp = +document.getElementById("mingp").value || 0;
    const minmin = +document.getElementById("minmin").value || 0;
    const team = document.getElementById("team").value;
    const f = document.getElementById("filter").value.toLowerCase();
    const rows = d.rows.filter(r => r.gp >= mingp && r.min >= minmin && (!team || r.team_abbreviation === team)
      && (!f || r.player_name.toLowerCase().includes(f)));
    const cols = [
      { key: "player_name", label: "Player", cls: "l", render: r => playerLink(r.player_id, r.player_name, S.season) },
      { key: "team_abbreviation", label: "Team", cls: "l", render: r => teamLink(r.team_id, r.team_abbreviation, S.season) },
      { key: "pos", label: "Pos", cls: "l" }, { key: "age", label: "Age", fmt: "i" },
      ...PRESETS[current].map(k => statCol(k)),
    ];
    const sortKey = PRESETS[current].find(k => ["pts_pg", "ts_pct", "usg_pct", "pts_p36", "pts_p100", "bpm", "pts"].includes(k)) || "min";
    table(document.getElementById("tbl"), rows, cols, { sort: sortKey, page: 50, rank: true });
  };
  pills(document.getElementById("presets"), Object.keys(PRESETS), current, p => { current = p; draw(); });
  ["mingp", "minmin", "team", "filter"].forEach(id => document.getElementById(id).oninput = draw);
  draw();
}

// ---------------- player page
async function vPlayer(pid, params) {
  setView(`<div class="loading">Loading player…</div>`);
  const d = await api(`player/${pid}?${qs({ season: params.get("season") })}`);
  const bio = d.bio[0] || {};
  const seasons = d.seasons;
  const cur = seasons.find(s => s.season === d.season) || seasons[seasons.length - 1] || {};
  const name = bio.name || cur.player_name || "Player";
  const tile = (k, label) => `<div class="tile"><div class="k">${label || pstat(k).label}</div><div class="v">${fmt(cur[k], pstat(k).fmt)}</div></div>`;
  setView(`
    <div class="hero">
      <div><h1>${esc(name)}</h1>
        <p class="sub">${esc(cur.pos || "")} ${cur.team_abbreviation ? "· " + teamLink(cur.team_id, cur.team_abbreviation, d.season) : ""}
        ${cur.age ? "· age " + Math.round(cur.age) : ""} · ${bio.from_year ?? ""}–${bio.to_year ?? ""}
        ${cur.height_in ? `· ${height(cur.height_in)}, ${cur.weight ?? "?"} lb` : ""}
        ${cur.country ? "· " + esc(cur.country) : ""}${cur.college && cur.college !== "None" ? " · " + esc(cur.college) : ""}
        ${cur.draft_year ? `· drafted ${cur.draft_year}${cur.draft_number ? " #" + cur.draft_number : ""}` : cur.college ? "· undrafted" : ""}
        ${cur.awards ? `· <span class="chip">${esc(cur.awards)}</span>` : ""}</p></div>
      <div class="toolbar" style="margin:0"><label>Season <select id="psea">${seasons.map(s =>
        `<option ${s.season === d.season ? "selected" : ""}>${s.season}</option>`).reverse().join("")}</select></label>
        <a href="#/analysis/compare?ids=${pid}:${d.season}"><button>Compare</button></a></div>
    </div>
    <div class="tiles">${tile("pts_pg")}${tile("reb_pg")}${tile("ast_pg")}${tile("ts_pct")}${tile("usg_pct")}
      ${tile("per")}${tile("bpm")}${tile("ws")}${tile("net_diff")}</div>
    <div class="grid g2 section">
      <div class="card"><div class="card-head"><h2>Game log</h2><select id="gstat">${["pts", "reb", "ast", "game_score", "plus_minus", "fg3m", "min"]
        .map(k => `<option value="${k}">${k === "game_score" ? "Game Score" : k === "plus_minus" ? "+/-" : k.toUpperCase()}</option>`).join("")}</select></div>
        <div class="chart-box"><canvas id="c-games"></canvas></div>
        <p class="note">Bars are single games (playoff games in the second colour); the line is a 10-game rolling average.</p></div>
      <div class="card"><div class="card-head"><h2>Percentiles in ${esc(d.season)}</h2><span class="muted small">vs players with 500+ minutes</span></div>
        <div id="pct"></div></div>
      <div class="card"><div class="card-head"><h2>Career arc</h2><select id="arc">${statOptions(S.meta.player_stats, "pts_pg")}</select></div>
        <div class="chart-box"><canvas id="c-arc"></canvas></div></div>
      <div class="card flush"><div class="card-head"><h2>Most similar player-seasons</h2><span class="muted small">style profile, era-adjusted</span></div>
        <div id="sim"></div></div>
      <div class="card"><div class="card-head"><h2>Shot profile</h2><span class="muted small">share of field goal attempts by zone</span></div>
        <div class="chart-box short"><canvas id="c-zones"></canvas></div><div id="zt"></div></div>
      <div class="card"><div class="card-head"><h2>Clutch and hustle</h2><span class="muted small">clutch = last 5 min, within 5 pts</span></div>
        <div class="tiles">${["clutch_pts", "clutch_ts_pct", "clutch_plus_minus", "deflections_p36", "contested_shots_p36", "screen_assists_p36", "box_outs_p36", "charges_drawn"]
          .map(k => `<div class="tile"><div class="k">${esc(pstat(k).label)}</div><div class="v">${fmt(cur[k], pstat(k).fmt)}</div></div>`).join("")}</div>
        <p class="note">Hustle tracking starts in 2015-16.</p></div>
    </div>
    <div class="card flush section"><div class="card-head"><h2>Season by season</h2><div class="pills" id="ppre"></div></div><div id="seas"></div></div>
    ${d.career ? `<p class="note">Career in stored seasons: ${fmt(d.career.gp, "i")} games, ${fmt(d.career.pts_pg, "1")} pts, ${fmt(d.career.reb_pg, "1")} reb, ${fmt(d.career.ast_pg, "1")} ast per game, ${fmt(d.career.ts_pct, "p")} TS%, ${fmt(d.career.ws, "1")} win shares.</p>` : ""}
    ${d.playoffs.length ? `<div class="card flush section"><div class="card-head"><h2>Playoffs</h2></div><div id="po"></div></div>` : ""}
    ${d.history.length ? `<div class="card flush section"><div class="card-head"><h2>Before 1996-97 (Basketball Reference)</h2></div><div id="hist"></div></div>` : ""}
    <div class="card flush section"><div class="card-head"><h2>${esc(d.season)} games</h2></div><div id="glog"></div></div>`);

  document.getElementById("psea").onchange = e => location.hash = `#/player/${pid}?season=${e.target.value}`;

  // game log chart
  const drawGames = k => {
    const g = d.games;
    const vals = g.map(x => x[k]);
    const old = S.charts.find(c => c.canvas.id === "c-games"); if (old) { old.destroy(); S.charts = S.charts.filter(c => c !== old); }
    chart("c-games", {
      data: { labels: g.map(x => x.game_date.slice(5)), datasets: [
        { type: "bar", label: "Game", data: vals, backgroundColor: g.map(x => x.season_type === "Playoffs" ? series(1) : css("--seq-lo")), borderRadius: 3, order: 2 },
        lineDs("10-game average", rolling(vals, 10), 0, { type: "line", order: 1 }),
      ] },
      options: baseOptions({ interaction: { mode: "index", intersect: false },
        plugins: { ...baseOptions().plugins, tooltip: { ...baseOptions().plugins.tooltip, callbacks: {
          title: items => { const x = g[items[0].dataIndex]; return `${x.game_date} ${x.matchup} (${x.wl})`; },
          label: c => `${c.dataset.label}: ${c.parsed.y === null ? "–" : c.parsed.y.toFixed(1)}` } } },
        scales: { x: { ...baseOptions().scales.x, ticks: { color: css("--muted"), maxTicksLimit: 10 } }, y: baseOptions().scales.y } }),
    });
  };
  drawGames("pts");
  document.getElementById("gstat").onchange = e => drawGames(e.target.value);

  // percentiles
  const pct = d.percentiles || {};
  document.getElementById("pct").innerHTML = d.radar.map(k => {
    const v = pct[k];
    return `<div class="pbar"><span>${esc(pstat(k).label)}</span><div class="track"><div class="fill" style="width:${v ?? 0}%"></div></div>
      <span class="n">${v ?? "–"}</span></div>`;
  }).join("") + `<p class="note">Percentile rank among qualified players that season. USG% and 3PA rate describe role, not quality.</p>`;

  // career arc
  const drawArc = k => {
    const old = S.charts.find(c => c.canvas.id === "c-arc"); if (old) { old.destroy(); S.charts = S.charts.filter(c => c !== old); }
    const m = pstat(k);
    chart("c-arc", {
      type: "line",
      data: { labels: seasons.map(s => s.season), datasets: [lineDs(m.label, seasons.map(s => s[k] === null ? null : (m.fmt === "p" ? s[k] * 100 : s[k])), 0, { pointRadius: 4, tension: 0.2 })] },
      options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false } } }),
    });
  };
  drawArc("pts_pg");
  document.getElementById("arc").onchange = e => drawArc(e.target.value);

  if (cur.ra_share !== undefined && cur.ra_share !== null) {
    chart("c-zones", { type: "bar", data: { labels: ZONES.map(z => z[1]), datasets: [{ label: "Share of FGA",
      data: ZONES.map(([z]) => (cur[z + "_share"] ?? 0) * 100), backgroundColor: series(0), borderRadius: 4, barPercentage: 0.7 }] },
      options: baseOptions({ indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false }, tooltip: { ...baseOptions().plugins.tooltip, callbacks: {
        label: c => { const z = ZONES[c.dataIndex][0]; return `${c.parsed.x.toFixed(1)}% of shots, ${fmt(cur[z + "_fg_pct"], "p")}% FG (${fmt(cur[z + "_fgm"], "i")}/${fmt(cur[z + "_fga"], "i")})`; } } } },
        scales: { x: { ...baseOptions().scales.x, beginAtZero: true }, y: { ...baseOptions().scales.y, ticks: { color: css("--ink-2") } } } }) });
    document.getElementById("zt").innerHTML = `<p class="note">FG% by zone: ${ZONES.map(([z, l]) => `${l} ${fmt(cur[z + "_fg_pct"], "p")}%`).join(" · ")}</p>`;
  }
  table(document.getElementById("sim"), d.similar, [
    { key: "player_name", label: "Player", cls: "l", render: r => playerLink(r.player_id, r.player_name, r.season) },
    { key: "season", label: "Season", cls: "l" }, { key: "pts_pg", label: "PTS", fmt: "1" },
    { key: "reb_pg", label: "REB", fmt: "1" }, { key: "ast_pg", label: "AST", fmt: "1" },
    { key: "ts_pct", label: "TS%", fmt: "p" }, { key: "similarity", label: "Match", fmt: "i" },
  ], { short: true });

  const seasonCols = p => [
    { key: "season", label: "Season", cls: "l", render: r => `<a href="#/player/${pid}?season=${r.season}">${r.season}</a>` },
    { key: "team_abbreviation", label: "Team", cls: "l", render: r => teamLink(r.team_id, r.team_abbreviation, r.season) },
    { key: "age", label: "Age", fmt: "i" }, ...PRESETS[p].map(k => statCol(k))];
  const drawSeasons = p => table(document.getElementById("seas"), seasons, seasonCols(p), { sort: "season", asc: true });
  pills(document.getElementById("ppre"), Object.keys(PRESETS), "Basic", drawSeasons);
  drawSeasons("Basic");

  if (d.playoffs.length) {
    const po = d.playoffs.map(r => ({ ...r, pts_pg: r.pts / r.gp, reb_pg: r.reb / r.gp, ast_pg: r.ast / r.gp,
      ts_pct: r.pts / (2 * (r.fga + 0.44 * r.fta)), fg3_pct: r.fg3a ? r.fg3m / r.fg3a : null }));
    table(document.getElementById("po"), po, [{ key: "season", label: "Season", cls: "l" }, { key: "team_abbreviation", label: "Team", cls: "l" },
      { key: "gp", label: "GP", fmt: "i" }, { key: "pts_pg", label: "PTS", fmt: "1" }, { key: "reb_pg", label: "REB", fmt: "1" },
      { key: "ast_pg", label: "AST", fmt: "1" }, { key: "ts_pct", label: "TS%", fmt: "p" }, { key: "fg3_pct", label: "3P%", fmt: "p" },
      { key: "plus_minus", label: "+/-", fmt: "i" }], { sort: "season", asc: true });
  }
  if (d.history.length) {
    const hs = d.history.map(r => ({ ...r, pts_pg: r.pts / r.g, trb_pg: r.trb / r.g, ast_pg: r.ast / r.g,
      stl_pg: r.stl / r.g, blk_pg: r.blk / r.g, mp_pg: r.mp / r.g, ts_pct: r.pts / (2 * (r.fga + 0.44 * r.fta)) }));
    table(document.getElementById("hist"), hs, [{ key: "season", label: "Season", cls: "l" }, { key: "teams", label: "Team", cls: "l" },
      { key: "age", label: "Age", fmt: "i" }, { key: "g", label: "G", fmt: "i" }, { key: "mp_pg", label: "MIN", fmt: "1" },
      { key: "pts_pg", label: "PTS", fmt: "1" }, { key: "trb_pg", label: "REB", fmt: "1" }, { key: "ast_pg", label: "AST", fmt: "1" },
      { key: "stl_pg", label: "STL", fmt: "1" }, { key: "blk_pg", label: "BLK", fmt: "1" }, { key: "ts_pct", label: "TS%", fmt: "p" },
      { key: "per", label: "PER", fmt: "1" }, { key: "ws", label: "WS", fmt: "1" }, { key: "bpm", label: "BPM", fmt: "1" }, { key: "vorp", label: "VORP", fmt: "1" }],
      { sort: "season", asc: true });
  }
  table(document.getElementById("glog"), d.games, [
    { key: "game_date", label: "Date", cls: "l" }, { key: "matchup", label: "Matchup", cls: "l" }, { key: "wl", label: "W/L", cls: "l" },
    { key: "min", label: "MIN", fmt: "i" }, { key: "pts", label: "PTS", fmt: "i" }, { key: "reb", label: "REB", fmt: "i" },
    { key: "ast", label: "AST", fmt: "i" }, { key: "stl", label: "STL", fmt: "i" }, { key: "blk", label: "BLK", fmt: "i" },
    { key: "tov", label: "TOV", fmt: "i" }, { key: "fgm", label: "FG", render: r => `${r.fgm}-${r.fga}` },
    { key: "fg3m", label: "3P", render: r => `${r.fg3m}-${r.fg3a}` }, { key: "ftm", label: "FT", render: r => `${r.ftm}-${r.fta}` },
    { key: "plus_minus", label: "+/-", fmt: "i" }, { key: "game_score", label: "GmSc", fmt: "1" },
  ], { sort: "game_date", asc: false, short: true });
}

// ---------------- teams list
async function vTeams() {
  setView(`<div class="hero"><div><h1>Teams</h1><p class="sub">Ratings, four factors and scoring for every team in ${esc(S.season)}.</p></div></div>
    <div class="toolbar"><div class="pills" id="tp"></div></div><div class="card flush" id="tt"></div>`);
  const d = await api(`teams?season=${S.season}`);
  const draw = p => table(document.getElementById("tt"), d.rows, [
    { key: "team_name", label: "Team", cls: "l", render: r => teamLink(r.team_id, r.team_name, S.season) },
    { key: "conference", label: "Conf", cls: "l" }, ...TEAM_PRESETS[p].map(k => statCol(k, "t"))], { sort: "w", rank: true });
  pills(document.getElementById("tp"), Object.keys(TEAM_PRESETS), "Ratings", draw);
  draw("Ratings");
}

// ---------------- team page
async function vTeam(tid) {
  setView(`<div class="loading">Loading team…</div>`);
  const d = await api(`team/${tid}?season=${S.season}`);
  const r = d.row[0] || {}, info = d.info[0] || {};
  const fr = d.franchise.find(f => f.team_name === info.nickname) || d.franchise[0];
  const tile = (k, label) => `<div class="tile"><div class="k">${label || tstat(k).label}</div><div class="v">${fmt(r[k], tstat(k).fmt)}</div></div>`;
  setView(`
    <div class="hero"><div><h1>${esc(info.name || r.team_name)}</h1>
      <p class="sub">${esc(S.season)}: ${r.w ?? "–"}-${r.l ?? "–"} · ${esc(r.conference || "")} seed ${r.playoffrank ?? "–"} · home ${esc(r.home || "–")}, road ${esc(r.road || "–")}
      ${fr ? ` · Franchise since ${fr.start_year}: ${fr.wins}-${fr.losses}, ${fr.league_titles} titles` : ""}</p></div></div>
    <div class="tiles">${tile("off_rating")}${tile("def_rating")}${tile("net_rating")}${tile("pace")}${tile("pyth_w")}${tile("luck")}${tile("efg_pct")}${tile("opp_efg_pct")}</div>
    <div class="grid g2 section">
      <div class="card"><div class="card-head"><h2>Season flow</h2><span class="muted small">regular season, game by game</span></div>
        <h3>Cumulative point differential</h3><div class="chart-box short"><canvas id="c-flow"></canvas></div>
        <h3 style="margin-top:12px">Margin in each game</h3><div class="chart-box short"><canvas id="c-margin"></canvas></div></div>
      <div class="card"><div class="card-head"><h2>Franchise ratings by season</h2></div>
        <div class="chart-box"><canvas id="c-hist"></canvas></div></div>
    </div>
    <div class="card flush section"><div class="card-head"><h2>Roster</h2><div class="pills" id="rp"></div></div><div id="roster"></div></div>
    <div class="card flush section"><div class="card-head"><h2>On/off court</h2><span class="muted small">team net rating with each player on vs off the floor</span></div><div id="onoff"></div></div>`);
  const rs = d.games.filter(g => g.season_type !== "Playoffs");
  let cum = 0;
  const gameTip = { ...baseOptions().plugins.tooltip, callbacks: {
    title: it => { const g = rs[it[0].dataIndex]; return `Game ${it[0].dataIndex + 1}: ${g.game_date} ${g.matchup} (${g.wl})`; } } };
  const flowOpts = () => baseOptions({ interaction: { mode: "index", intersect: false },
    plugins: { ...baseOptions().plugins, legend: { display: false }, tooltip: gameTip } });
  chart("c-flow", { type: "line", data: { labels: rs.map((g, i) => i + 1),
    datasets: [lineDs("Cumulative differential", rs.map(g => (cum += g.plus_minus)), 0)] }, options: flowOpts() });
  chart("c-margin", { type: "bar", data: { labels: rs.map((g, i) => i + 1), datasets: [{ label: "Margin",
    data: rs.map(g => g.plus_minus), backgroundColor: rs.map(g => g.plus_minus >= 0 ? series(0) : series(7)), borderRadius: 2 }] },
    options: flowOpts() });
  chart("c-hist", {
    type: "line",
    data: { labels: d.history.map(h => h.season), datasets: [
      lineDs("ORtg", d.history.map(h => h.off_rating), 0, { pointRadius: 3 }),
      lineDs("DRtg", d.history.map(h => h.def_rating), 1, { pointRadius: 3 }),
    ] },
    options: baseOptions({ plugins: { ...baseOptions().plugins, tooltip: { ...baseOptions().plugins.tooltip, callbacks: {
      afterBody: it => { const h = d.history[it[0].dataIndex]; return `Record ${h.w}-${h.l}, net ${h.net_rating > 0 ? "+" : ""}${h.net_rating}`; } } } } }),
  });
  const drawRoster = p => table(document.getElementById("roster"), d.roster, [
    { key: "player_name", label: "Player", cls: "l", render: x => playerLink(x.player_id, x.player_name, S.season) },
    { key: "pos", label: "Pos", cls: "l" }, { key: "age", label: "Age", fmt: "i" }, ...PRESETS[p].map(k => statCol(k))], { sort: "min" });
  pills(document.getElementById("rp"), Object.keys(PRESETS), "Basic", drawRoster);
  drawRoster("Basic");
  table(document.getElementById("onoff"), d.onoff.filter(o => o.on_min > 0), [
    { key: "player_name", label: "Player", cls: "l", render: x => playerLink(x.player_id, x.player_name, S.season) },
    { key: "on_min", label: "On MIN", fmt: "i" }, { key: "on_net_rating", label: "On net", fmt: "1" },
    { key: "off_net_rating", label: "Off net", fmt: "1" },
    { key: "net_diff", label: "On − off", render: x => `<span class="${x.net_diff >= 0 ? "up" : "down"}">${x.net_diff > 0 ? "+" : ""}${fmt(x.net_diff, "1")}</span>` },
    { key: "ortg_diff", label: "ORtg Δ", fmt: "1" }, { key: "drtg_diff", label: "DRtg Δ", fmt: "1" },
  ], { sort: "on_min" });
}

// ---------------- standings
async function vStandings() {
  setView(`<div class="hero"><div><h1>Standings</h1><p class="sub">${esc(S.season)} regular season. Pythag W is the win total expected from points scored and allowed; luck is the gap.</p></div></div>
    <div class="grid g2"><div class="card flush"><div class="card-head"><h2>East</h2></div><div id="se"></div></div>
    <div class="card flush"><div class="card-head"><h2>West</h2></div><div id="sw"></div></div></div>`);
  const d = await api(`standings?season=${S.season}`);
  const cols = [
    { key: "playoffrank", label: "#", fmt: "i", cls: "l" },
    { key: "team_name", label: "Team", cls: "l", render: r => teamLink(r.team_id, r.team_name, S.season) + ` <span class="muted small">${esc((r.clinchindicator || "").replace(" - ", ""))}</span>` },
    { key: "w", label: "W", fmt: "i" }, { key: "l", label: "L", fmt: "i" }, { key: "w_pct", label: "Pct", fmt: "3" },
    { key: "conferencegamesback", label: "GB", fmt: "1" }, { key: "home", label: "Home" }, { key: "road", label: "Road" },
    { key: "l10", label: "L10" }, { key: "strcurrentstreak", label: "Strk" },
    { key: "net_rating", label: "Net", fmt: "1" }, { key: "pyth_w", label: "Pythag W", fmt: "1" }, { key: "luck", label: "Luck", fmt: "1" },
  ];
  table(document.getElementById("se"), d.rows.filter(r => r.conference === "East"), cols, { sort: "playoffrank", asc: true });
  table(document.getElementById("sw"), d.rows.filter(r => r.conference === "West"), cols, { sort: "playoffrank", asc: true });
}

// ---------------- leaders
async function vLeaders(params) {
  const stat = params.get("stat") || "pts_pg";
  const scope = params.get("scope") || S.season;
  setView(`<div class="hero"><div><h1>Leaderboards</h1><p class="sub">Any stat, one season or every stored season at once.</p></div></div>
    <div class="toolbar">
      <label>Stat <select id="ls">${statOptions(S.meta.player_stats, stat)}</select></label>
      <label>Season <select id="lsc"><option value="all" ${scope === "all" ? "selected" : ""}>All seasons</option>${S.meta.seasons.map(s => `<option ${s === scope ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Min games <input type="number" id="lg" value="${params.get("min_gp") || 40}"></label>
      <label>Min minutes <input type="number" id="lm" value="${params.get("min_min") || 1000}"></label>
    </div>
    <div class="grid g2"><div class="card"><div class="chart-box tall"><canvas id="c-lead"></canvas></div></div>
    <div class="card flush" id="lt"></div></div>`);
  const draw = async () => {
    const st = document.getElementById("ls").value, sc = document.getElementById("lsc").value;
    const d = await api(`leaders?${qs({ stat: st, season: sc, min_gp: document.getElementById("lg").value, min_min: document.getElementById("lm").value, limit: 50 })}`);
    const m = pstat(st);
    const top = d.rows.slice(0, 20);
    destroyCharts();
    chart("c-lead", {
      type: "bar",
      data: { labels: top.map(r => sc === "all" ? `${r.player_name} ${r.season.slice(2)}` : r.player_name),
        datasets: [{ label: m.label, data: top.map(r => m.fmt === "p" ? r.v * 100 : r.v), backgroundColor: series(0), borderRadius: 4, barPercentage: 0.75 }] },
      options: baseOptions({ indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } },
        scales: { x: { ...baseOptions().scales.x, beginAtZero: m.fmt !== "p" && st !== "def_rating" }, y: { ...baseOptions().scales.y, ticks: { color: css("--ink-2"), autoSkip: false } } } }),
    });
    table(document.getElementById("lt"), d.rows, [
      { key: "player_name", label: "Player", cls: "l", render: r => playerLink(r.player_id, r.player_name, r.season) },
      { key: "season", label: "Season", cls: "l" }, { key: "team_abbreviation", label: "Team", cls: "l" },
      { key: "gp", label: "GP", fmt: "i" }, { key: "v", label: m.label, fmt: m.fmt, sortVal: r => m.better === false ? -r.v : r.v }],
      { rank: true, short: false });
  };
  ["ls", "lsc", "lg", "lm"].forEach(id => document.getElementById(id).onchange = draw);
  draw();
}

// ---------------- records
async function vRecords(params) {
  const scope = params.get("scope") || "all";
  const stype = params.get("type") || "Regular Season";
  setView(`<div class="hero"><div><h1>Records</h1><p class="sub">Single-game bests from every stored box score, the best teams by net rating and NBA.com all-time career leaders.</p></div></div>
    <div class="toolbar"><label>Season <select id="rs"><option value="all">All stored seasons</option>${S.meta.seasons.map(s => `<option ${s === scope ? "selected" : ""}>${s}</option>`).join("")}</select></label>
    <label>Type <select id="rt"><option ${stype === "Regular Season" ? "selected" : ""}>Regular Season</option><option ${stype === "Playoffs" ? "selected" : ""}>Playoffs</option></select></label></div>
    <div id="rec"><div class="loading">Loading…</div></div>`);
  const go = () => location.hash = `#/records?${qs({ scope: document.getElementById("rs").value, type: document.getElementById("rt").value })}`;
  document.getElementById("rs").onchange = go; document.getElementById("rt").onchange = go;
  const d = await api(`records?${qs({ season: scope, season_type: stype })}`);
  const stats = Object.keys(d.labels);
  const at = d.alltime;
  const atStats = [...new Set(at.map(r => r.stat))];
  document.getElementById("rec").innerHTML = `
    <h2>Single-game highs</h2>
    <div class="grid g3">${stats.map(s => `<div class="card"><h3>${esc(d.labels[s])}</h3><ul class="leader-list">${d.single_game[s].map(r =>
      `<li><span>${playerLink(r.player_id, r.player_name)} <span class="muted small">${esc(r.matchup)} · ${esc(r.game_date)}</span></span><span class="val">${fmt(r.v, s === "game_score" ? "1" : "i")}</span></li>`).join("")}</ul></div>`).join("")}</div>
    <div class="grid g2 section">
      <div class="card"><h3>Most triple-doubles</h3><ul class="leader-list">${d.triple_doubles.map(r => `<li><span>${playerLink(r.player_id, r.player_name)}</span><span class="val">${r.n}</span></li>`).join("")}</ul></div>
      <div class="card"><h3>Best teams by net rating (all stored seasons)</h3><ul class="leader-list">${d.best_teams.map(r => `<li><span>${esc(r.season)} ${esc(r.team_abbr)} <span class="muted small">${r.w}-${r.l}</span></span><span class="val">+${fmt(r.net_rating, "1")}</span></li>`).join("")}</ul></div>
    </div>
    <div class="grid g3 section">${Object.entries(d.team_games).map(([k, rows]) => `<div class="card"><h3>Team: ${esc(k)}</h3><ul class="leader-list">${rows.map(r =>
      `<li><span>${esc(r.team_abbreviation)} <span class="muted small">${esc(r.matchup)} · ${esc(r.game_date)}</span></span><span class="val">${fmt(r.v, "i")}</span></li>`).join("")}</ul></div>`).join("")}</div>
    <div class="card section"><div class="card-head"><h2>All-time career leaders</h2><select id="ats">${atStats.map(s => `<option value="${s}">${s.toUpperCase()}</option>`).join("")}</select></div><div id="att"></div>
      <p class="note">From NBA.com's all-time leaders, covering the whole history of the league.</p></div>`;
  const drawAt = s => table(document.getElementById("att"), at.filter(r => r.stat === s), [
    { key: "rank", label: "#", fmt: "i", cls: "l" }, { key: "name", label: "Player", cls: "l", render: r => playerLink(r.player_id, r.name) },
    { key: "value", label: s.toUpperCase(), fmt: s.includes("pct") ? "3" : "i" }, { key: "is_active", label: "Active", render: r => r.is_active === "Y" ? "Yes" : "" }],
    { sort: "rank", asc: true, short: true });
  document.getElementById("ats").value = atStats.includes("pts") ? "pts" : atStats[0];
  document.getElementById("ats").onchange = e => drawAt(e.target.value);
  drawAt(document.getElementById("ats").value);
}

// ---------------- trends
async function vTrends() {
  setView(`<div class="hero"><div><h1>How the game has changed</h1><p class="sub">League-wide trends across every stored season. Shooting trends reach back before 1996-97 using Basketball Reference totals when the history has been loaded.</p></div></div>
    <div class="card" id="zcard"><div class="card-head"><h2>Where shots come from</h2><span class="muted small">share of all field goal attempts</span></div>
      <div class="chart-box tall"><canvas id="c-zmix"></canvas></div><p class="note">The mid-range shot has been traded for threes and shots at the rim.</p></div>
    <div class="grid g2 section" id="tr"></div>
    <div class="card section"><div class="card-head"><h2>Team spread over time</h2><select id="tts">${statOptions(S.meta.team_stats, "net_rating")}</select></div>
      <div class="chart-box tall"><canvas id="c-tspread"></canvas></div><p class="note">Each dot is one team-season; hover for the team.</p></div>`);
  const d = await api("league");
  const rows = d.rows, hist = d.history || [];
  const z = d.zones || [];
  if (z.length) {
    const tot = r => ZONES.reduce((a, [k]) => a + (r[k] || 0), 0);
    chart("c-zmix", { type: "line", data: { labels: z.map(r => r.season), datasets: ZONES.map(([k, l], i) =>
      lineDs(l, z.map(r => r[k] / tot(r) * 100), i, { pointRadius: 2 })) },
      options: baseOptions({ interaction: { mode: "index", intersect: false }, plugins: { ...baseOptions().plugins, tooltip: { ...baseOptions().plugins.tooltip, callbacks: {
        label: c => { const r = z[c.dataIndex], k = ZONES[c.datasetIndex][0]; return `${c.dataset.label}: ${c.parsed.y.toFixed(1)}% of shots, ${(r[k + "_m"] / r[k] * 100).toFixed(1)}% FG`; } } } },
        scales: { x: baseOptions().scales.x, y: { ...baseOptions().scales.y, beginAtZero: true, ticks: { color: css("--muted"), callback: v => v + "%" } } } }) });
  } else document.getElementById("zcard").remove();
  const charts = [["fg3a_rate", true], ["ts_pct", true], ["pace", false], ["ortg", false], ["pts_pg", false], ["fg3_pct", true], ["fta_rate", true], ["ast_pg", false]];
  document.getElementById("tr").innerHTML = charts.map(([k]) =>
    `<div class="card"><h3>${esc(S.meta.league_stats[k].label)}</h3><div class="chart-box short"><canvas id="c-tr-${k}"></canvas></div></div>`).join("");
  charts.forEach(([k, useHist]) => {
    const f = S.meta.league_stats[k].fmt;
    const mapv = v => v === null || v === undefined ? null : (f === "p" ? v * 100 : v);
    const h = useHist ? hist.filter(x => !rows.some(r => r.season === x.season)) : [];
    const labels = [...h.map(x => x.season), ...rows.map(r => r.season)];
    const data = [...h.map(x => mapv(x[k])), ...rows.map(r => mapv(r[k]))];
    chart(`c-tr-${k}`, { type: "line", data: { labels, datasets: [lineDs(S.meta.league_stats[k].label, data, 0, { pointRadius: 2 })] },
      options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false } } }) });
  });
  const drawSpread = async st => {
    const t = await api(`analysis/team_trend?stat=${st}`);
    const old = S.charts.find(c => c.canvas.id === "c-tspread"); if (old) { old.destroy(); S.charts = S.charts.filter(c => c !== old); }
    const seasons = [...new Set(t.rows.map(r => r.season))];
    const f = tstat(st).fmt;
    chart("c-tspread", { type: "scatter", data: { datasets: [{ label: tstat(st).label, backgroundColor: series(0) + "99", pointRadius: 4,
      data: t.rows.map(r => ({ x: seasons.indexOf(r.season) + (Math.random() - 0.5) * 0.3, y: f === "p" ? r.v * 100 : r.v, r })) }] },
      options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false }, tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: c => `${c.raw.r.season} ${c.raw.r.team_abbr}: ${fmt(c.raw.r.v, f)}` } } },
        scales: { x: { ...baseOptions().scales.x, ticks: { color: css("--muted"), callback: v => seasons[Math.round(v)] || "", stepSize: 1 } }, y: baseOptions().scales.y } }) });
  };
  document.getElementById("tts").onchange = e => drawSpread(e.target.value);
  drawSpread("net_rating");
}

// ---------------- analysis hub
const ANALYSES = {
  compare: ["Compare players", "Side by side stats and percentile profiles for up to four player-seasons."],
  explorer: ["Stat explorer", "Plot any stat against any other for players or teams."],
  archetypes: ["Archetypes", "K-means clustering of playing style into player types."],
  projections: ["Projections", "Marcel-style forecasts for next season."],
  aging: ["Aging curves", "How stats change with age, using the delta method."],
  wins: ["What wins", "Which of the four factors explain team success."],
  draft: ["Draft value", "What each draft slot is worth in career win shares, plus the biggest steals."],
  origins: ["Origins", "The league going global: countries, colleges, height and age over time."],
};

async function vAnalysis(sub, params) {
  if (!sub) {
    setView(`<div class="hero"><div><h1>Analysis</h1><p class="sub">From basic comparisons to models. Pick a tool.</p></div></div>
      <div class="grid g3">${Object.entries(ANALYSES).map(([k, [t, dsc]]) => `<a class="card" href="#/analysis/${k}" style="color:inherit;text-decoration:none"><h2>${t}</h2><p class="muted" style="margin:0">${dsc}</p></a>`).join("")}</div>`);
    return;
  }
  const nav = `<div class="toolbar">${Object.entries(ANALYSES).map(([k, [t]]) => `<a href="#/analysis/${k}"><button class="${k === sub ? "primary" : ""}">${t}</button></a>`).join("")}</div>`;
  const fn = { compare: aCompare, explorer: aExplorer, archetypes: aArchetypes, projections: aProjections, aging: aAging, wins: aWins, draft: aDraft, origins: aOrigins }[sub];
  if (fn) await fn(nav, params);
}

async function aCompare(nav, params) {
  const ids = (params.get("ids") || "").split(",").filter(Boolean);
  setView(`${nav}<h1>Compare players</h1><p class="sub">Add up to four player-seasons. The radar shows percentile ranks within each player's own season.</p>
    <div class="toolbar"><div class="search"><input type="text" id="cq" placeholder="Add a player…" autocomplete="off"><div class="results" id="cres"></div></div>
      <label>Season <select id="cs">${S.meta.seasons.map(s => `<option ${s === S.season ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <span id="chips"></span></div>
    <div class="grid g2"><div class="card"><div class="chart-box tall"><canvas id="c-radar"></canvas></div></div><div class="card flush" id="ct"></div></div>`);
  const setIds = list => location.hash = `#/analysis/compare?ids=${list.join(",")}`;
  wireSearch(document.getElementById("cq"), document.getElementById("cres"), p => {
    if (ids.length >= 4) return;
    setIds([...ids, `${p.player_id}:${document.getElementById("cs").value}`]);
  });
  if (!ids.length) { document.getElementById("ct").innerHTML = `<div class="empty">Search for a player to start.</div>`; return; }
  const d = await api(`analysis/compare?ids=${ids.join(",")}`);
  document.getElementById("chips").innerHTML = d.players.map((p, i) => `<span class="chip"><span style="color:${series(i)}">●</span> ${esc(p.player_name)} ${p.season}<button data-i="${i}" title="Remove">✕</button></span>`).join("");
  document.querySelectorAll("#chips button").forEach(b => b.onclick = () => setIds(ids.filter((_, i) => i !== +b.dataset.i)));
  chart("c-radar", {
    type: "radar",
    data: { labels: d.radar.map(k => pstat(k).label), datasets: d.players.map((p, i) => ({
      label: `${p.player_name} ${p.season}`, data: d.radar.map(k => p.percentiles?.[k] ?? 0),
      borderColor: series(i), backgroundColor: series(i) + "22", borderWidth: 2, pointRadius: 3 })) },
    options: baseOptions({ scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 }, grid: { color: css("--grid") }, angleLines: { color: css("--grid") }, pointLabels: { color: css("--ink-2") } } } }),
  });
  const keys = ["gp", "min_pg", "pts_pg", "reb_pg", "ast_pg", "stl_pg", "blk_pg", "tov_pg", "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "usg_pct", "ast_pct", "reb_pct", "per", "ws", "bpm", "vorp", "net_diff"];
  const el = document.getElementById("ct");
  el.innerHTML = `<div class="table-wrap"><table><thead><tr><th class="l">Stat</th>${d.players.map((p, i) => `<th><span style="color:${series(i)}">●</span> ${esc(p.player_name)}<br><span class="muted">${p.season}</span></th>`).join("")}</tr></thead>
    <tbody>${keys.map(k => { const m = pstat(k); const vals = d.players.map(p => p[k]);
      const best = m.better === null ? null : vals.reduce((b, v) => v === null ? b : b === null ? v : (m.better === false ? Math.min(b, v) : Math.max(b, v)), null);
      return `<tr><td class="l">${esc(m.label)}</td>${vals.map(v => `<td style="${v === best && d.players.length > 1 ? "font-weight:700" : ""}">${fmt(v, m.fmt)}</td>`).join("")}</tr>`; }).join("")}</tbody></table></div>`;
}

async function aExplorer(nav, params) {
  const kind = params.get("kind") || "players";
  const cat = kind === "players" ? S.meta.player_stats : S.meta.team_stats;
  const x = params.get("x") || (kind === "players" ? "usg_pct" : "off_rating");
  const y = params.get("y") || (kind === "players" ? "ts_pct" : "def_rating");
  setView(`${nav}<h1>Stat explorer</h1><p class="sub">Any stat against any other. The correlation (r) tells you how tightly they move together.</p>
    <div class="toolbar"><div class="pills" id="ek"></div>
      <label>X <select id="ex">${statOptions(cat, x)}</select></label><label>Y <select id="ey">${statOptions(cat, y)}</select></label>
      ${kind === "players" ? `<label>Min minutes <input type="number" id="em" value="${params.get("min_min") || 1000}"></label>` : ""}
      <input type="text" id="ehl" placeholder="Highlight player"> <span id="er" class="muted"></span></div>
    <div class="card"><div class="chart-box tall" style="height:560px"><canvas id="c-ex"></canvas></div></div>`);
  pills(document.getElementById("ek"), ["players", "teams"], kind, k => location.hash = `#/analysis/explorer?kind=${k}`);
  const draw = async () => {
    const xx = document.getElementById("ex").value, yy = document.getElementById("ey").value;
    const d = await api(`analysis/scatter?${qs({ kind, season: S.season, x: xx, y: yy, min_min: document.getElementById("em")?.value })}`);
    const cm = kind === "players" ? pstat : tstat, fx = cm(xx), fy = cm(yy);
    const hl = document.getElementById("ehl").value.toLowerCase();
    document.getElementById("er").textContent = d.r === null ? "" : `r = ${d.r.toFixed(2)} · ${d.rows.length} ${kind}`;
    destroyCharts();
    const pt = r => ({ x: fx.fmt === "p" ? r.x * 100 : r.x, y: fy.fmt === "p" ? r.y * 100 : r.y, label: kind === "teams" || (hl && r.name.toLowerCase().includes(hl)) ? r.name : null, r });
    const hit = d.rows.filter(r => hl && r.name.toLowerCase().includes(hl));
    chart("c-ex", { type: "scatter", data: { datasets: [
      { label: "Highlighted", data: hit.map(pt), backgroundColor: series(1), borderColor: css("--surface"), borderWidth: 2, pointRadius: 8 },
      { label: kind, data: d.rows.map(pt), backgroundColor: series(0) + (kind === "teams" ? "" : "aa"), borderColor: css("--surface"), borderWidth: 1, pointRadius: kind === "teams" ? 7 : 4, pointHoverRadius: 8 },
    ] }, options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false }, pointLabels: { enabled: true },
      tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: c => `${c.raw.r.name} (${c.raw.r.team}): ${fx.label} ${fmt(c.raw.r.x, fx.fmt)}, ${fy.label} ${fmt(c.raw.r.y, fy.fmt)}` } } },
      scales: { x: { ...baseOptions().scales.x, title: { display: true, text: fx.label, color: css("--muted") } }, y: { ...baseOptions().scales.y, title: { display: true, text: fy.label, color: css("--muted") } } },
      onClick: (e, els) => { if (els[0]) { const r = els[0].element.$context.raw.r; location.hash = kind === "players" ? `#/player/${r.id}?season=${S.season}` : `#/team/${r.id}?season=${S.season}`; } } }) });
  };
  ["ex", "ey", "em", "ehl"].forEach(id => { const el = document.getElementById(id); if (el) el.onchange = draw; });
  document.getElementById("ehl").oninput = draw;
  draw();
}

async function aArchetypes(nav, params) {
  const k = +(params.get("k") || 8);
  setView(`${nav}<h1>Player archetypes</h1><p class="sub">K-means clustering of ${esc(S.season)} players (800+ minutes) on eleven style features: scoring volume, usage, assist and rebound rates, steals, blocks, turnovers, 3-point and free-throw rates, and true shooting. The map squeezes those eleven dimensions into two with PCA, so nearby dots play alike.</p>
    <div class="toolbar"><label>Clusters <select id="ak">${[4, 5, 6, 7, 8, 9, 10, 12].map(n => `<option ${n === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    <span class="muted small">Click a type to highlight it.</span></div>
    <div class="grid g2"><div class="card"><div class="chart-box tall" style="height:560px"><canvas id="c-cl"></canvas></div></div><div class="grid g2" id="cards" style="align-content:start"></div></div>`);
  document.getElementById("ak").onchange = e => location.hash = `#/analysis/archetypes?k=${e.target.value}`;
  const d = await api(`analysis/clusters?season=${S.season}&k=${k}`);
  let focus = null;
  const colorOf = i => series(i);
  const draw = () => {
    destroyCharts();
    chart("c-cl", { type: "scatter", data: { datasets: d.clusters.map(c => ({
      label: c.label, pointRadius: 5, pointHoverRadius: 8, borderColor: css("--surface"), borderWidth: 1,
      backgroundColor: focus === null || focus === c.id ? colorOf(c.id) : css("--grid"),
      order: focus === c.id ? 0 : 1,
      data: d.players.filter(p => p.cluster === c.id).map(p => ({ x: p.x, y: p.y, p, label: focus === c.id ? p.player_name : null })),
    })) }, options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false }, pointLabels: { enabled: true },
      tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: c => `${c.raw.p.player_name} (${c.raw.p.team_abbreviation}) · ${c.dataset.label}` } } },
      scales: { x: { ...baseOptions().scales.x, ticks: { display: false }, title: { display: true, text: "Style component 1", color: css("--muted") } },
        y: { ...baseOptions().scales.y, ticks: { display: false }, title: { display: true, text: "Style component 2", color: css("--muted") } } },
      onClick: (e, els) => { if (els[0]) location.hash = `#/player/${els[0].element.$context.raw.p.player_id}?season=${S.season}`; } }) });
    document.getElementById("cards").innerHTML = d.clusters.map(c => `<div class="cluster-card ${focus === c.id ? "on" : ""}" data-c="${c.id}">
      <div><span class="sw" style="background:${colorOf(c.id)}"></span><b>${esc(c.label)}</b></div>
      <div class="muted small">${c.size} players · mostly ${esc(c.position)} · avg BPM ${c.avg_bpm ?? "–"}</div>
      <div class="small" style="margin-top:4px">${c.examples.map(esc).join(", ")}</div></div>`).join("");
    document.querySelectorAll(".cluster-card").forEach(el => el.onclick = () => { focus = focus === +el.dataset.c ? null : +el.dataset.c; draw(); });
  };
  draw();
}

async function aProjections(nav) {
  const next = S.season.slice(0, 4) * 1 + 1;
  setView(`${nav}<h1>Projections for ${next}-${String(next + 1).slice(2)}</h1>
    <p class="sub">A Marcel-style forecast built from ${esc(S.season)} and the two seasons before it: 5/4/3 weights, regressed toward league average by 1,000 minutes, then adjusted for age (young players improve, players past 28 decline). Reliability shows how much is the player's own track record.</p>
    <div class="toolbar"><input type="text" id="pf" placeholder="Filter by name"></div><div class="card flush" id="pt"><div class="loading">Projecting…</div></div>`);
  const d = await api(`analysis/projections?season=${S.season}`);
  const draw = () => {
    const f = document.getElementById("pf").value.toLowerCase();
    table(document.getElementById("pt"), d.rows.filter(r => !f || r.player_name.toLowerCase().includes(f)), [
      { key: "player_name", label: "Player", cls: "l", render: r => playerLink(r.player_id, r.player_name, S.season) },
      { key: "team_abbreviation", label: "Team", cls: "l" }, { key: "age", label: "Age", fmt: "i" },
      { key: "pts_pg", label: "PTS", fmt: "1" }, { key: "last_pts_pg", label: "last", fmt: "1", title: "Points per game this season" },
      { key: "reb_pg", label: "REB", fmt: "1" }, { key: "ast_pg", label: "AST", fmt: "1" }, { key: "fg3m_pg", label: "3PM", fmt: "1" },
      { key: "ts_pct", label: "TS%", fmt: "p" }, { key: "last_ts_pct", label: "last", fmt: "p" },
      { key: "pts_p36", label: "PTS/36", fmt: "1" }, { key: "proj_min", label: "Proj MIN", fmt: "i" },
      { key: "reliability", label: "Reliability", fmt: "2" },
    ], { sort: "pts_pg", page: 50, rank: true });
  };
  document.getElementById("pf").oninput = draw;
  draw();
}

async function aAging(nav, params) {
  const stat = params.get("stat") || "bpm";
  setView(`${nav}<h1>Aging curves</h1><p class="sub">For every player with back-to-back seasons of 500+ minutes, the change from one age to the next, weighted by minutes and chained together. The curve is relative to age 19-20, so read its shape, not its level. Survivor bias applies: players who decline fast drop out.</p>
    <div class="toolbar"><label>Stat <select id="as">${statOptions(S.meta.player_stats, stat, k => !["gp", "min", "dd2", "td3", "pts", "reb", "ast", "stl", "blk", "fg3m", "ftm"].includes(k))}</select></label></div>
    <div class="grid g2"><div class="card"><h3>Cumulative change from youngest age</h3><div class="chart-box tall"><canvas id="c-age"></canvas></div></div>
    <div class="card"><h3>Year-over-year change at each age</h3><div class="chart-box tall"><canvas id="c-aged"></canvas></div></div></div>`);
  document.getElementById("as").onchange = e => location.hash = `#/analysis/aging?stat=${e.target.value}`;
  const d = await api(`analysis/aging?stat=${stat}`);
  const m = pstat(stat), sc = m.fmt === "p" ? 100 : 1;
  const rows = d.rows;
  if (!rows.length) { document.querySelector(".grid.g2").innerHTML = `<div class="empty">Not enough seasons stored yet for aging curves.</div>`; return; }
  const labels = [rows[0].age, ...rows.map(r => r.to_age)];
  chart("c-age", { type: "line", data: { labels, datasets: [lineDs(m.label, [0, ...rows.map(r => r.cumulative * sc)], 0, { pointRadius: 4 })] },
    options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: { ...baseOptions().scales.x, title: { display: true, text: "Age", color: css("--muted") } }, y: baseOptions().scales.y } }) });
  chart("c-aged", { type: "bar", data: { labels: rows.map(r => `${r.age}→${r.to_age}`), datasets: [{ label: `Change in ${m.label}`,
    data: rows.map(r => r.delta * sc), backgroundColor: rows.map(r => r.delta >= 0 ? series(0) : series(7)), borderRadius: 4 }] },
    options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false }, tooltip: { ...baseOptions().plugins.tooltip, callbacks: { afterLabel: c => `${rows[c.dataIndex].n} player pairs` } } } }) });
}

async function aWins(nav) {
  setView(`${nav}<h1>What wins</h1><p class="sub">Dean Oliver's four factors (shooting, turnovers, offensive rebounding, free throws) on offense and defense, regressed against win percentage across every stored team-season.</p>
    <div class="grid g2"><div class="card"><h3>Standardized regression weight on win%</h3><div class="chart-box tall"><canvas id="c-w"></canvas></div><p class="note" id="wn"></p></div>
    <div class="card"><h3>Luck: actual wins vs Pythagorean wins, ${esc(S.season)}</h3><div class="chart-box tall"><canvas id="c-l"></canvas></div><p class="note">Teams above the line won more than their point differential suggests, usually through close games.</p></div></div>`);
  const [w, t] = await Promise.all([api("analysis/winmodel"), api(`teams?season=${S.season}`)]);
  if (w) {
    const keys = Object.keys(w.coefficients);
    chart("c-w", { type: "bar", data: { labels: keys.map(k => tstat(k).label), datasets: [{ label: "Weight", data: keys.map(k => w.coefficients[k] * 100),
      backgroundColor: keys.map(k => w.coefficients[k] >= 0 ? series(0) : series(7)), borderRadius: 4 }] },
      options: baseOptions({ indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false }, tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: c => `${c.parsed.x.toFixed(1)} win% points per standard deviation` } } } }) });
    document.getElementById("wn").textContent = `${w.n} team-seasons, R² = ${w.r2.toFixed(3)}. Bars show how many percentage points of win% one standard deviation of each factor is worth, holding the others fixed. Shooting (eFG%) dominates on both ends.`;
  }
  const rows = t.rows.filter(r => r.pyth_w !== null);
  const lo = Math.min(...rows.map(r => Math.min(r.w, r.pyth_w))) - 2, hi = Math.max(...rows.map(r => Math.max(r.w, r.pyth_w))) + 2;
  chart("c-l", { data: { datasets: [
    { type: "line", label: "Expected", data: [{ x: lo, y: lo }, { x: hi, y: hi }], borderColor: css("--axis"), borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
    { type: "scatter", label: "Teams", data: rows.map(r => ({ x: r.pyth_w, y: r.w, label: r.team_abbr, r })), backgroundColor: series(0), borderColor: css("--surface"), borderWidth: 2, pointRadius: 6 },
  ] }, options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false }, pointLabels: { enabled: true },
    tooltip: { ...baseOptions().plugins.tooltip, filter: c => c.datasetIndex === 1, callbacks: { label: c => `${c.raw.r.team_name}: ${c.raw.r.w} wins, ${c.raw.r.pyth_w.toFixed(1)} expected (${c.raw.r.luck > 0 ? "+" : ""}${c.raw.r.luck.toFixed(1)})` } } },
    scales: { x: { ...baseOptions().scales.x, type: "linear", title: { display: true, text: "Pythagorean wins", color: css("--muted") } }, y: { ...baseOptions().scales.y, title: { display: true, text: "Actual wins", color: css("--muted") } } } }) });
}

async function aDraft(nav, params) {
  setView(`${nav}<h1>Draft value</h1><p class="sub">Career win shares (Basketball Reference) earned in the stored seasons by every player drafted since the first stored season. Only drafts at least six years old count toward the pick averages, so careers have time to develop.</p>
    <div class="grid g2"><div class="card span2"><h3>Average career win shares by overall pick</h3><div class="chart-box tall"><canvas id="c-dp"></canvas></div><p class="note" id="dn"></p></div>
    <div class="card flush"><div class="card-head"><h2>Biggest steals</h2><span class="muted small">picked 15th or later</span></div><div id="dst"></div></div>
    <div class="card flush"><div class="card-head"><h2>Draft class</h2><select id="dy"></select></div><div id="dcl"></div></div></div>`);
  const d = await api("analysis/draft");
  if (!d.picks.length) { document.getElementById("dn").textContent = "Load more seasons to see draft value."; return; }
  chart("c-dp", { type: "bar", data: { labels: d.picks.map(p => p.overall_pick), datasets: [
    { label: "Average career WS", data: d.picks.map(p => p.avg_ws), backgroundColor: series(0), borderRadius: 3 }] },
    options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false }, tooltip: { ...baseOptions().plugins.tooltip, callbacks: {
      title: it => `Pick ${d.picks[it[0].dataIndex].overall_pick}`,
      label: c => { const p = d.picks[c.dataIndex]; return [`Average ${p.avg_ws.toFixed(1)} WS, median ${p.median_ws.toFixed(1)} (${p.n} players)`,
        `${(p.hit_rate * 100).toFixed(0)}% lasted 5+ seasons`, `Best: ${p.best_name} (${p.best_year}), ${p.best_ws.toFixed(1)} WS`]; } } } },
      scales: { x: { ...baseOptions().scales.x, title: { display: true, text: "Overall pick", color: css("--muted") } }, y: baseOptions().scales.y } }) });
  document.getElementById("dn").textContent = `Drafts through ${d.mature_through}. Hover a bar for the best player taken at that pick.`;
  table(document.getElementById("dst"), d.steals, [
    { key: "player_name", label: "Player", cls: "l", render: r => playerLink(r.player_id, r.player_name) },
    { key: "draft_year", label: "Year", render: r => r.draft_year }, { key: "overall_pick", label: "Pick", fmt: "i" },
    { key: "team_abbreviation", label: "By", cls: "l" }, { key: "ws", label: "WS", fmt: "1" }], { sort: "ws", short: true });
  const years = [...new Set(d.draft.map(r => r.draft_year))].sort((a, b) => b - a);
  const sel = document.getElementById("dy");
  sel.innerHTML = years.map(y => `<option>${y}</option>`).join("");
  sel.value = params.get("year") || years[Math.min(6, years.length - 1)];
  const drawClass = () => table(document.getElementById("dcl"), d.draft.filter(r => r.draft_year === +sel.value), [
    { key: "overall_pick", label: "Pick", fmt: "i", cls: "l" },
    { key: "player_name", label: "Player", cls: "l", render: r => r.seasons ? playerLink(r.player_id, r.player_name) : esc(r.player_name) },
    { key: "organization", label: "From", cls: "l" }, { key: "seasons", label: "Seasons", fmt: "i" },
    { key: "ws", label: "WS", fmt: "1" }, { key: "vorp", label: "VORP", fmt: "1" }], { sort: "overall_pick", asc: true, short: true });
  sel.onchange = drawClass;
  drawClass();
}

async function aOrigins(nav) {
  setView(`${nav}<h1>Origins</h1><p class="sub">Where players come from and how the player pool has changed. Shares are weighted by minutes played.</p>
    <div class="grid g2"><div class="card"><h3>International players' share of minutes</h3><div class="chart-box"><canvas id="c-intl"></canvas></div></div>
    <div class="card"><h3>Minutes-weighted height (inches)</h3><div class="chart-box"><canvas id="c-ht"></canvas></div></div>
    <div class="card flush"><div class="card-head"><h2>Countries, ${esc(S.season)}</h2></div><div id="oc"></div></div>
    <div class="card flush"><div class="card-head"><h2>Colleges, ${esc(S.season)}</h2></div><div id="ocl"></div></div></div>`);
  const d = await api(`analysis/origins?season=${S.season}`);
  const t = d.trend;
  const one = (id, label, vals, extra = {}) => chart(id, { type: "line", data: { labels: t.map(r => r.season), datasets: [lineDs(label, vals, 0, { pointRadius: 3 })] },
    options: baseOptions({ plugins: { ...baseOptions().plugins, legend: { display: false } }, ...extra }) });
  one("c-intl", "International share of minutes (%)", t.map(r => r.intl_min_share * 100));
  one("c-ht", "Average height", t.map(r => r.avg_height));
  const cols = key => [{ key, label: key === "country" ? "Country" : "College", cls: "l" }, { key: "players", label: "Players", fmt: "i" },
    { key: "minutes", label: "Minutes", fmt: "i" }, { key: "names", label: "Top players", cls: "l" }];
  table(document.getElementById("oc"), d.countries, cols("country"), { sort: "minutes", short: true });
  table(document.getElementById("ocl"), d.colleges, cols("college"), { sort: "minutes", short: true });
}

// ------------------------------------------------------------------ search

function wireSearch(input, box, onPick) {
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    const v = input.value.trim();
    if (v.length < 2) { box.classList.remove("open"); return; }
    timer = setTimeout(async () => {
      const d = await api(`search?q=${encodeURIComponent(v)}`);
      box.innerHTML = [...d.players.map(p => `<a href="#" data-p="${p.player_id}"><span>${esc(p.name)}</span><span class="muted small">${p.from_year ?? ""}–${p.to_year ?? ""}</span></a>`),
        ...(onPick ? [] : d.teams.map(t => `<a href="#/team/${t.team_id}"><span>${esc(t.name)}</span><span class="muted small">Team</span></a>`))].join("")
        || `<div class="empty">No matches</div>`;
      box.classList.add("open");
      box.querySelectorAll("[data-p]").forEach(a => a.onclick = e => {
        e.preventDefault(); box.classList.remove("open"); input.value = "";
        const p = d.players.find(x => String(x.player_id) === a.dataset.p);
        if (onPick) onPick(p); else location.hash = `#/player/${p.player_id}`;
      });
      box.querySelectorAll("a[href^='#/team']").forEach(a => a.onclick = () => { box.classList.remove("open"); input.value = ""; });
    }, 180);
  };
  input.onblur = () => setTimeout(() => box.classList.remove("open"), 200);
}

// ------------------------------------------------------------------ router

async function route() {
  S.navId++;
  const [path, query] = location.hash.slice(2).split("?");
  const params = new URLSearchParams(query || "");
  const parts = (path || "").split("/");
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.r === (parts[0] === "player" ? "players" : parts[0] === "team" ? "teams" : parts[0])));
  try {
    switch (parts[0]) {
      case "": case undefined: return await vDashboard();
      case "players": return await vPlayers(params);
      case "player": return await vPlayer(parts[1], params);
      case "teams": return await vTeams();
      case "team": if (params.get("season")) setSeason(params.get("season"), false); return await vTeam(parts[1]);
      case "standings": return await vStandings();
      case "leaders": return await vLeaders(params);
      case "records": return await vRecords(params);
      case "trends": return await vTrends();
      case "analysis": return await vAnalysis(parts[1], params);
      default: setView(`<div class="empty">Page not found.</div>`);
    }
  } catch (e) {
    console.error(e);
    setView(`<div class="empty">Something went wrong: ${esc(e.message)}</div>`);
  }
}

function setSeason(s, rerender = true) {
  if (!S.meta.seasons.includes(s)) return;
  S.season = s;
  document.getElementById("season").value = s;
  try { localStorage.setItem("season", s); } catch (e) { /* storage may be blocked */ }
  if (rerender) route();
}

async function init() {
  S.meta = await api("meta");
  let saved = null;
  try { saved = localStorage.getItem("season"); } catch (e) { /* ignore */ }
  S.season = S.meta.seasons.includes(saved) ? saved : S.meta.seasons[0];
  const sel = document.getElementById("season");
  sel.innerHTML = S.meta.seasons.map(s => `<option>${s}</option>`).join("");
  sel.value = S.season;
  sel.onchange = () => setSeason(sel.value);
  wireSearch(document.getElementById("q"), document.getElementById("qres"));
  window.addEventListener("hashchange", route);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", route);
  route();
}

init();
