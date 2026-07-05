"use strict";

// Reuses globals from app.js: el, chip, apiFetch, escapeHtml, PALETTE, PLOT_CONFIG, baseLayout.
// app.js calls onCdsShown() the first time the CDS tab is opened.

const cdsState = { loaded: false, results: [], expanded: new Set() };

function cdsStatus(message, kind) {
  const node = el("cds-status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

// --- lazy load + hooks ------------------------------------------------------

async function onCdsShown() {
  if (cdsState.loaded) return;
  cdsState.loaded = true;
  el("cds-trade-date").value = new Date().toISOString().slice(0, 10);
  await loadCdsRateCurves();
  await loadIssuerFilter();
}

function onRateCurvesChanged() {
  if (typeof onCreditRateCurvesChanged === "function") onCreditRateCurvesChanged();
  if (cdsState.loaded) loadCdsRateCurves();
}

window.onRateCurvesChanged = onRateCurvesChanged;

async function loadCdsRateCurves() {
  const curves = await apiFetch("GET", "/api/rate-curves");
  const select = el("cds-rate-curve");
  select.innerHTML = curves
    .map((c) => `<option value="${c.id}">${escapeHtml(c.rate_index)} @ ${c.trade_date}</option>`)
    .join("");
}

async function loadIssuerFilter() {
  const issuers = await apiFetch("GET", "/api/issuers");
  const select = el("cds-issuer-filter");
  select.innerHTML = issuers
    .map((i) => `<option value="${i.id}">${escapeHtml(i.name)}</option>`)
    .join("");
}

// --- pricing ----------------------------------------------------------------

function formatTenor(months, isStandard) {
  const y = Math.floor(months / 12);
  const m = months % 12;
  let label = "";
  if (y > 0) label += y + "y";
  if (m > 0) label += (label ? m + "m" : m + "m");
  if (isStandard) label += " \u2605";
  return label;
}

async function priceCds() {
  const rateCurveId = el("cds-rate-curve").value;
  if (!rateCurveId) { cdsStatus("Select a saved rate curve", "error"); return; }
  const tradeDate = el("cds-trade-date").value;
  if (!tradeDate) return;

  const btn = el("cds-refresh");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Pricing\u2026';
  cdsStatus("Pricing CDS contracts\u2026");

  const selected = [...el("cds-issuer-filter").selectedOptions].filter((o) => o.value).map((o) => o.value);
  const params = new URLSearchParams({ rate_curve_id: rateCurveId, trade_date: tradeDate });
  if (selected.length === 1) params.append("issuer_id", selected[0]);

  try {
    const data = await apiFetch("GET", "/api/cds-pricing?" + params.toString());
    cdsState.results = data.results;
    cdsState.expanded.clear();
    renderCdsTable();
    el("cds-empty").hidden = true;
    el("cds-results-card").hidden = false;
    cdsStatus(data.results.length + " contracts priced", "ok");
    renderCdsSummary();
  } catch (err) {
    cdsStatus(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Price CDS";
  }
}

function renderCdsSummary() {
  const r = cdsState.results;
  if (!r.length) { el("cds-summary").innerHTML = ""; return; }
  const issuerSet = new Set(r.map((x) => x.issuer_name));
  const std = r.filter((x) => x.is_standard);
  const spreads = r.map((x) => x.par_spread);
  const curveOpt = el("cds-rate-curve").selectedOptions[0];
  el("cds-summary").innerHTML = [
    chip("Rate curve", curveOpt ? escapeHtml(curveOpt.textContent) : "\u2014"),
    chip("Base date", r[0].expiry_date ? "" : el("cds-trade-date").value, ""),
    chip("Contracts", String(r.length)),
    chip("Issuers", String(issuerSet.size)),
    chip("Standard", String(std.length)),
    chip("Non-standard", String(r.length - std.length)),
    chip("Spread range", spreads.length ? Math.min(...spreads).toFixed(1) + "\u2013" + Math.max(...spreads).toFixed(1) + " bp" : "\u2014"),
  ].join("");
}

// --- table rendering --------------------------------------------------------

function parClass(spread) {
  if (spread < 100) return "good";
  if (spread <= 200) return "warn";
  return "bad";
}

function signClass(val) { return val >= 0 ? "adj-pos" : "adj-neg"; }

function renderCdsTable() {
  const standardOnly = el("cds-standard-only").checked;
  const selected = [...el("cds-issuer-filter").selectedOptions].filter((o) => o.value).map((o) => o.value);
  let rows = cdsState.results;
  if (standardOnly) rows = rows.filter((r) => r.is_standard);
  if (selected.length > 0) rows = rows.filter((r) => selected.includes(String(r.issuer_id)));

  const tbody = el("cds-table").querySelector("tbody");
  tbody.innerHTML = "";
  let prevIssuer = null;
  rows.forEach((r) => {
    const key = String(r.issuer_id) + "_" + String(r.tenor_months);
    const isExpanded = cdsState.expanded.has(key);
    const tr = document.createElement("tr");
    if (isExpanded) tr.classList.add("expanded");
    if (r.issuer_name !== prevIssuer) { tr.classList.add("issuer-break"); prevIssuer = r.issuer_name; }
    tr.dataset.key = key;
    const pc = parClass(r.par_spread);
    const uc = signClass(r.upfront);
    const nc = signClass(r.net_premium);
    tr.innerHTML =
      "<td>" + escapeHtml(r.issuer_name) + "</td>" +
      '<td><span class="v ' + pc + '">' + formatTenor(r.tenor_months, r.is_standard) + "</span></td>" +
      "<td>" + r.expiry_date + "</td>" +
      '<td><span class="v ' + pc + '">' + Number(r.par_spread).toFixed(2) + "</span></td>" +
      '<td class="' + uc + '">' + (r.upfront >= 0 ? "+" : "") + Number(r.upfront).toFixed(4) + "</td>" +
      "<td>" + Number(r.rebate).toFixed(4) + "</td>" +
      '<td class="' + nc + '">' + (r.net_premium >= 0 ? "+" : "") + Number(r.net_premium).toFixed(4) + "</td>" +
      "<td>" + Number(r.dv01).toFixed(6) + "</td>" +
      "<td>" + Number(r.credit_dv01).toFixed(6) + "</td>";
    tr.addEventListener("click", () => toggleCdsRow(tr, r));
    tbody.appendChild(tr);
    if (isExpanded) appendDetailRow(tr, r);
  });

  const tfoot = el("cds-table").querySelector("tfoot") || (() => {
    const tf = document.createElement("tfoot");
    el("cds-table").appendChild(tf);
    return tf;
  })();
  const spreads = rows.map((r) => r.par_spread);
  tfoot.innerHTML = rows.length
    ? "<tr><td colspan=\"9\">" + rows.length + " contracts \u00b7 " +
      (standardOnly ? "Standard only \u00b7 " : "") +
      "Par spread range: " + (spreads.length ? Math.min(...spreads).toFixed(1) + "\u2013" + Math.max(...spreads).toFixed(1) + " bp" : "\u2014") +
      "</td></tr>"
    : "";
}

// --- accordion detail -------------------------------------------------------

function toggleCdsRow(tr, row) {
  const key = tr.dataset.key;
  if (cdsState.expanded.has(key)) {
    cdsState.expanded.delete(key);
    tr.classList.remove("expanded");
    const detail = tr.nextElementSibling;
    if (detail && detail.classList.contains("cds-detail")) detail.remove();
  } else {
    cdsState.expanded.add(key);
    tr.classList.add("expanded");
    appendDetailRow(tr, row);
  }
}

async function appendDetailRow(tr, row) {
  const key = tr.dataset.key;
  const detail = document.createElement("tr");
  detail.classList.add("cds-detail");
  const td = document.createElement("td");
  td.colSpan = 9;

  const url =
    "/api/cds-pricing/" + row.issuer_id + "/" + row.tenor_months +
    "/breakdown?rate_curve_id=" + el("cds-rate-curve").value +
    "&trade_date=" + el("cds-trade-date").value;

  let data = null;
  try {
    data = await apiFetch("GET", url);
  } catch (_) { /* render partial */ }

  if (!data) {
    td.innerHTML = '<div class="detail-card">Could not load breakdown.</div>';
    detail.appendChild(td);
    tr.parentNode.insertBefore(detail, tr.nextSibling);
    return;
  }

  const summaryHtml =
    '<div class="detail-summary">' +
    chip("Protection leg", (data.protection_leg || 0).toFixed(8)) +
    chip("Premium leg", (data.premium_leg || 0).toFixed(8)) +
    chip("RPV01", (data.rpv01 || 0).toFixed(8)) +
    chip("Recovery", ((data.recovery_rate || 0) * 100).toFixed(0) + "%") +
    chip("Base date", data.base_date || "\u2014") +
    "</div>";

  const chartIdBase = "cds-chart-" + key.replace(/[^a-zA-Z0-9]/g, "_");
  const irId = chartIdBase + "-ir";
  const hzId = chartIdBase + "-hz";

  const chartHtml =
    '<div class="detail-charts">' +
    '<div id="' + irId + '" class="detail-chart"></div>' +
    '<div id="' + hzId + '" class="detail-chart"></div>' +
    "</div>";

  const breakdown = data.breakdown || [];
  const periodHtml =
    '<table><thead><tr><th>Date</th><th>&Delta;</th><th>DF</th><th>Q</th>' +
    '<th>Premium PV</th><th>Protection PV</th></tr></thead><tbody>' +
    breakdown.map((b) =>
      "<tr><td>" + b.date + "</td>" +
      "<td>" + Number(b.year_fraction).toFixed(8) + "</td>" +
      "<td>" + Number(b.df).toFixed(6) + "</td>" +
      "<td>" + Number(b.survival).toFixed(6) + "</td>" +
      "<td>" + Number(b.premium_pv).toFixed(8) + "</td>" +
      "<td>" + Number(b.protection_pv).toFixed(8) + "</td></tr>"
    ).join("") + "</tbody></table>";

  const xlsxUrl =
    "/api/cds-pricing/" + row.issuer_id + "/" + row.tenor_months +
    "/breakdown.xlsx?rate_curve_id=" + el("cds-rate-curve").value +
    "&trade_date=" + el("cds-trade-date").value;

  td.innerHTML =
    '<div class="detail-card">' +
    '<div class="detail-card-head">' +
    "<h4>" + escapeHtml(row.issuer_name) + " \u2014 " + formatTenor(row.tenor_months, row.is_standard) + "</h4>" +
    '<button class="btn-secondary detail-download" type="button" data-href="' + xlsxUrl + '">Download detail (.xlsx)</button>' +
    "</div>" +
    summaryHtml + chartHtml + periodHtml +
    '<div class="leg-summary">' +
    "Par spread: <b>" + Number(row.par_spread).toFixed(2) + " bp</b> \u00b7 " +
    "Upfront: <b>" + (row.upfront >= 0 ? "+" : "") + Number(row.upfront).toFixed(4) + "</b> \u00b7 " +
    "Rebate: <b>" + Number(row.rebate).toFixed(4) + "</b> \u00b7 " +
    "Net premium: <b>" + (row.net_premium >= 0 ? "+" : "") + Number(row.net_premium).toFixed(4) + "</b> \u00b7 " +
    "DV01: <b>" + Number(row.dv01).toFixed(6) + "</b> \u00b7 " +
    "Credit DV01: <b>" + Number(row.credit_dv01).toFixed(6) + "</b>" +
    "</div></div>";

  detail.appendChild(td);
  tr.parentNode.insertBefore(detail, tr.nextSibling);

  const dlBtn = td.querySelector(".detail-download");
  if (dlBtn) dlBtn.addEventListener("click", () => { window.location.href = dlBtn.dataset.href; });

  if (data.discount_nodes && data.discount_nodes.length) {
    const ax = { gridcolor: PALETTE.grid, zerolinecolor: PALETTE.grid, color: PALETTE.muted };
    const irLayout = {
      margin: { l: 45, r: 10, t: 5, b: 30 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: PALETTE.text, size: 10 },
      xaxis: { type: "date", title: "date", ...ax },
      yaxis: { title: "%", ...ax },
      hovermode: "x unified",
      showlegend: false,
    };
    Plotly.newPlot(irId, [{
      x: data.discount_nodes.map((n) => n.date),
      y: data.discount_nodes.map((n) => n.zero_rate_pct),
      mode: "lines", line: { color: PALETTE.cont, width: 1.5 },
    }], irLayout, PLOT_CONFIG);
  }
  if (data.hazard_nodes && data.hazard_nodes.length) {
    const ax = { gridcolor: PALETTE.grid, zerolinecolor: PALETTE.grid, color: PALETTE.muted };
    const hzLayout = {
      margin: { l: 45, r: 10, t: 5, b: 30 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: PALETTE.text, size: 10 },
      xaxis: { type: "date", title: "date", ...ax },
      yaxis: { title: "%", ...ax },
      hovermode: "x unified",
      showlegend: false,
    };
    Plotly.newPlot(hzId, [{
      x: data.hazard_nodes.map((n) => n.date),
      y: data.hazard_nodes.map((n) => n.hazard_pct),
      mode: "lines", line: { color: PALETTE.forward, width: 1.5, shape: "hv" },
    }], hzLayout, PLOT_CONFIG);
  }
}

// --- Excel export -----------------------------------------------------------

function downloadCdsExcel() {
  const rateCurveId = el("cds-rate-curve").value;
  if (!rateCurveId) return;
  const params = new URLSearchParams({ rate_curve_id: rateCurveId });
  const tradeDate = el("cds-trade-date").value;
  if (tradeDate) params.append("trade_date", tradeDate);
  const selected = [...el("cds-issuer-filter").selectedOptions].filter((o) => o.value).map((o) => o.value);
  if (selected.length === 1) params.append("issuer_id", selected[0]);
  window.location.href = "/api/cds-pricing.xlsx?" + params.toString();
}

// --- init -------------------------------------------------------------------

function initCds() {
  el("cds-refresh").addEventListener("click", priceCds);
  el("cds-standard-only").addEventListener("change", () => { cdsState.expanded.clear(); renderCdsTable(); });
  el("cds-issuer-filter").addEventListener("change", () => { cdsState.expanded.clear(); renderCdsTable(); });
  el("cds-download-excel").addEventListener("click", downloadCdsExcel);
}

initCds();
