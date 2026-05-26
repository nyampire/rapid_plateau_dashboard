/* Renders dashboard data (from /api/dashboard, or bundled data.js fallback) into the coverage-rate UI. */
function renderDashboard(D) {
  const $ = (s) => document.querySelector(s);

  // --- rate color bins ---
  function rateColor(rate) {
    if (rate == null) return null;
    if (rate >= 75) return getCss("--r-high");
    if (rate >= 50) return getCss("--r-mid");
    if (rate >= 25) return getCss("--r-low");
    return getCss("--r-min");
  }
  function getCss(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
  const fmt = (n) => n == null ? "—" : Number(n).toLocaleString("ja-JP");
  // escape DB/API-derived strings before innerHTML interpolation (defense-in-depth)
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- KPIs ----------
  const s = D.summary;
  $("#updated").textContent = s.computed_at || "—";
  $("#kpi-rate").textContent = s.overall_rate != null ? s.overall_rate : "—";
  $("#f-rate").innerHTML =
    `<span class="op">=</span>` +
    `<span class="term"><span class="tlabel">OSMに存在する建物</span><span class="tval">${fmt(s.total_intersecting)}</span></span>` +
    `<span class="op">/</span>` +
    `<span class="term"><span class="tlabel">Plateau建物の総数</span><span class="tval">${fmt(s.total_plateau)}</span></span>` +
    `<span class="fnote">計測済 ${s.cities_measured} 都市の合計</span>`;
  // delta vs last week: use API prev_rate; fall back to bundled trend_sample (mock)
  let prev = (s.prev_rate != null) ? s.prev_rate
    : (Array.isArray(s.trend_sample) && s.trend_sample.length > 1 ? s.trend_sample[s.trend_sample.length - 2] : null);
  if (prev != null && s.overall_rate != null) {
    const d = s.overall_rate - prev;
    $("#kpi-delta").innerHTML = `<span class="dlabel">先週比</span> ${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}pt`;
  }
  $("#kpi-cities").innerHTML = `${s.cities_in_db} / ${s.cities_total}<span class="unit">都市</span>`;
  $("#f-cities").innerHTML = `<span class="ratelabel">割合</span> ${pct(s.cities_in_db, s.cities_total)}<span class="fnote">Rapid Plateau作業対象都市数 / Plateauデータセット数</span>`;
  $("#kpi-done").innerHTML = `${s.cities_osm_done} / ${s.cities_total}<span class="unit">都市</span>`;
  $("#f-done").innerHTML = `<span class="ratelabel">割合</span> ${pct(s.cities_osm_done, s.cities_total)}<span class="fnote">インポート完了都市数 / Plateauデータセット数</span>`;
  function pct(a, b) { return b ? (100 * a / b).toFixed(0) + "%" : "—"; }

  // ---------- region order ----------
  // No official region-ID exists, but city_code's leading 2 digits are the JIS
  // prefecture code (≈ north→south). Order regions by their min city_code so
  // 北海道 is first and 沖縄県 last, without a hardcoded list.
  const regionMinCode = {};
  D.cities.forEach((c) => {
    if (!(c.region in regionMinCode) || c.city_code < regionMinCode[c.region])
      regionMinCode[c.region] = c.city_code;
  });
  const regionRank = (r) => regionMinCode[r] || "99";
  D.regions.sort((a, b) => regionRank(a.region).localeCompare(regionRank(b.region)));

  // ---------- region cards ----------
  let regionFilter = "";
  const regionsEl = $("#regions");
  D.regions.forEach((r) => {
    const card = document.createElement("div");
    card.className = "region-card";
    card.dataset.region = r.region;
    const rate = r.rate;
    const color = rateColor(rate) || getCss("--r-none");
    card.innerHTML =
      `<div class="rname">${esc(r.region)}</div>` +
      `<div class="rrate" style="color:${color}">${rate != null ? rate + "%" : "<span class='na'>未計測</span>"}</div>` +
      `<div class="bar"><i style="width:${rate != null ? rate : 0}%;background:${color}"></i></div>` +
      `<div class="rsub" title="Rapid対象＝Rapid Plateau作業対象都市（Plateau建物データを保有する都市）">Rapid対象 ${r.cities_in_db}/${r.cities_total}都市 ・ インポート完了 ${r.cities_done}都市</div>`;
    card.onclick = () => {
      regionFilter = (regionFilter === r.region) ? "" : r.region;
      document.querySelectorAll(".region-card").forEach((c) =>
        c.classList.toggle("active", c.dataset.region === regionFilter));
      $("#f-region").value = regionFilter;
      renderTable();
    };
    regionsEl.appendChild(card);
  });

  // region filter dropdown
  const fRegion = $("#f-region");
  fRegion.innerHTML = `<option value="">地方: すべて</option>` +
    D.regions.map((r) => `<option value="${esc(r.region)}">${esc(r.region)}</option>`).join("");

  // ---------- table ----------
  let sortKey = "city_code", sortDir = 1;   // default: 5-digit municipality code, ascending
  const body = $("#cities-body");

  function statusBadge(c) {
    if (c.osm_import_status === "done")
      return `<span class="badge done">完了</span>${c.osm_validated ? '<span class="chk">✓検証</span>' : ""}`;
    if (c.osm_import_status === "in_progress") return `<span class="badge prog">作業中</span>`;
    return `<span class="badge none">未着手</span>`;
  }
  function rateCell(c) {
    if (c.import_rate != null) {
      const col = rateColor(c.import_rate);
      return `<div class="ratecell"><div class="bar"><i style="width:${c.import_rate}%;background:${col}"></i></div>` +
        `<span class="pct">${c.import_rate}%</span></div>`;
    }
    return c.in_local_db
      ? `<span class="na" title="Rapid対象（作業対象に登録済み）だが、OSM建物との交差率は未計算（全国一括計算で算出予定）">未計測</span>`
      : `<span class="na" title="Rapid Plateauの作業対象ではない（Plateauデータが無い）ため率を算出できない">Rapid対象外</span>`;
  }

  function passFilter(c) {
    if (regionFilter && c.region !== regionFilter) return false;
    const st = $("#f-status").value;
    if (st === "measured" && c.import_rate == null) return false;
    if (st === "in_db" && !c.in_local_db) return false;
    if (st === "not_in_db" && c.in_local_db) return false;
    if (st === "done" && c.osm_import_status !== "done") return false;
    const q = $("#f-search").value.trim();
    if (q && !(c.city_name || "").includes(q) && !(c.prefecture || "").includes(q)) return false;
    return true;
  }

  function renderTable() {
    const rows = D.cities.filter(passFilter).sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (va == null) return 1; if (vb == null) return -1;
      if (typeof va === "string") return sortDir * va.localeCompare(vb, "ja");
      return sortDir * (va - vb);
    });
    body.innerHTML = rows.map((c) => `
      <tr data-code="${c.city_code}">
        <td class="code">${c.city_code}</td>
        <td>${esc(c.city_name)}<div class="muted">${esc(c.prefecture)}</div></td>
        <td>${esc(c.region)}</td>
        <td>${rateCell(c)}</td>
        <td class="num">${fmt(c.intersecting_count)}</td>
        <td class="num">${fmt(c.plateau_count)}</td>
        <td>${statusBadge(c)}</td>
      </tr>`).join("");
    $("#row-count").textContent = `${rows.length} 都市を表示 / 全国 ${D.cities.length}`;
    body.querySelectorAll("tr").forEach((tr) =>
      tr.onclick = () => openDrawer(tr.dataset.code));
  }

  function applySortIndicator() {
    document.querySelectorAll("th[data-sort]").forEach((h) => {
      const arr = h.querySelector(".arr");
      if (!arr) return;
      const active = h.dataset.sort === sortKey;
      arr.textContent = active ? (sortDir < 0 ? "▾" : "▴") : "⇅";
      arr.classList.toggle("active", active);
    });
  }
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.sort;
      sortDir = (sortKey === k) ? -sortDir : (k === "import_rate" || k.endsWith("count") ? -1 : 1);
      sortKey = k;
      applySortIndicator();
      renderTable();
    };
  });
  $("#f-status").onchange = renderTable;
  $("#f-search").oninput = renderTable;
  fRegion.onchange = () => {
    regionFilter = fRegion.value;
    document.querySelectorAll(".region-card").forEach((c) =>
      c.classList.toggle("active", c.dataset.region === regionFilter));
    renderTable();
  };
  applySortIndicator();
  renderTable();

  // ---------- detail drawer ----------
  const byCode = Object.fromEntries(D.cities.map((c) => [c.city_code, c]));
  function openDrawer(code) {
    const c = byCode[code]; if (!c) return;
    const col = rateColor(c.import_rate) || getCss("--muted");
    const rateLine = c.import_rate != null
      ? `<div class="d-rate" style="color:${col}">${c.import_rate}%</div>
         <div class="d-row"><span class="k">OSMに存在する / Plateau総数</span><span>${fmt(c.intersecting_count)} / ${fmt(c.plateau_count)}</span></div>`
      : `<div class="d-rate na">${c.in_local_db ? "未計測" : "Rapid対象外"}</div>`;
    $("#drawer-body").innerHTML = `
      <div class="d-title">${esc(c.city_name)} <span class="muted">(${c.city_code})</span></div>
      <div class="d-sub">${esc(c.prefecture)} ・ ${esc(c.region)}</div>
      ${rateLine}
      <div class="d-row"><span class="k">OSM建物総数</span><span>${fmt(c.osm_count)}</span></div>
      <div class="d-row"><span class="k">建築物LOD / 仕様</span><span>${esc(c.building_lods || "—")} / ${esc(c.spec_versions || "—")}</span></div>
      <div class="d-row"><span class="k">Rapid Plateau 作業対象</span><span>${c.in_local_db ? "Rapid対象" : "Rapid対象外"}</span></div>
      <div class="d-row"><span class="k">OSMインポート(wiki)</span><span>${statusBadge(c)} ${c.osm_import_date || ""}</span></div>
      <div class="d-links">
        <a href="https://www.openstreetmap.org/search?query=${encodeURIComponent(c.city_name)}" target="_blank">OSMで開く</a>
        <a href="https://rapid.nyampire.info/" target="_blank">Rapidで開く</a>
      </div>`;
    $("#drawer").classList.remove("hidden");
    $("#scrim").classList.remove("hidden");
  }
  function closeDrawer() { $("#drawer").classList.add("hidden"); $("#scrim").classList.add("hidden"); }
  $("#drawer-close").onclick = closeDrawer;
  $("#scrim").onclick = closeDrawer;

  // ---------- small map ----------
  const map = L.map("map", { zoomControl: false }).setView([37.5, 137.5], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 18, opacity: 0.5,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
  L.geoJSON(D.geojson, {
    style: (f) => {
      const r = f.properties.import_rate;
      return { color: "#0c1117", weight: 0.5, fillColor: rateColor(r) || getCss("--r-none"),
               fillOpacity: r != null ? 0.85 : 0.45 };
    },
    onEachFeature: (f, layer) => {
      layer.on("click", () => openDrawer(f.properties.city_code));
      const r = f.properties.import_rate;
      layer.bindTooltip(`${esc(f.properties.city_name || f.properties.city_code)}${r != null ? " " + r + "%" : ""}`);
    }
  }).addTo(map);

  // legend
  $("#legend").innerHTML = [
    ["75–100%", "--r-high"], ["50–75%", "--r-mid"], ["25–50%", "--r-low"],
    ["0–25%", "--r-min"], ["未計測", "--r-none"]
  ].map(([t, v]) => `<span><i style="background:${getCss(v)}"></i>${t}</span>`).join("");
}

/* Bootstrap: load live data from the API; fall back to bundled data.js if unavailable. */
(function () {
  const base = (typeof window.DASH_API_BASE === "string" && window.DASH_API_BASE) || "/api/dashboard";
  const get = (p) => fetch(base + p).then((r) => { if (!r.ok) throw new Error(p + " HTTP " + r.status); return r.json(); });
  Promise.all([get("/summary"), get("/regions"), get("/cities"), get("/cities.geojson")])
    .then(([summary, regions, cities, geojson]) => renderDashboard({ summary, regions, cities, geojson }))
    .catch((e) => {
      if (window.DASH) {
        console.warn("dashboard API unavailable; using bundled data.js (" + e.message + ")");
        renderDashboard(window.DASH);
      } else {
        console.error("dashboard load failed", e);
        document.body.insertAdjacentHTML("afterbegin",
          '<div style="padding:14px;color:#e74c3c">データの読み込みに失敗しました。</div>');
      }
    });
})();
