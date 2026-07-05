"use strict";

// Reuses globals from app.js: el, chip, apiFetch, escapeHtml, PALETTE.
// app.js calls onCdsShown() the first time the CDS tab is opened.

const cdsState = { loaded: false, results: [], expanded: new Set() };

function cdsStatus(message, kind) {
  const node = el("cds-status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

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

async function priceCds() {
  const rateCurveId = el("cds-rate-curve").value;
  if (!rateCurveId) {
    cdsStatus("Select a saved rate curve", "error");
    return;
  }
  const tradeDate = el("cds-trade-date").value;
  if (!tradeDate) return;

  const btn = el("cds-refresh");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Pricing\u2026';
  cdsStatus("Pricing CDS contracts\u2026");

  const selected = [...el("cds-issuer-filter").selectedOptions]
    .filter((o) => o.value)
    .map((o) => o.value);
  const params = new URLSearchParams({
    rate_curve_id: rateCurveId,
    trade_date: tradeDate,
  });
  if (selected.length === 1) params.append("issuer_id", selected[0]);

  try {
    const data = await apiFetch("GET", "/api/cds-pricing?" + params.toString());
    cdsState.results = data.results;
    cdsState.expanded.clear();
    renderCdsTable();
    el("cds-empty").hidden = true;
    el("cds-results-card").hidden = false;
    cdsStatus(data.results.length + " contracts priced", "ok");
  } catch (err) {
    cdsStatus(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Price CDS";
  }
}

function renderCdsTable() {
  const standardOnly = el("cds-standard-only").checked;
  const selected = [...el("cds-issuer-filter").selectedOptions]
    .filter((o) => o.value)
    .map((o) => o.value);

  let rows = cdsState.results;
  if (standardOnly) rows = rows.filter((r) => r.is_standard);
  if (selected.length > 0) rows = rows.filter((r) => selected.includes(String(r.issuer_id)));

  const tbody = el("cds-table").querySelector("tbody");
  tbody.innerHTML = "";
  rows.forEach((r, idx) => {
    const key = String(r.issuer_id) + "_" + String(r.tenor_months);
    const isExpanded = cdsState.expanded.has(key);
    const tr = document.createElement("tr");
    if (isExpanded) tr.classList.add("expanded");
    tr.dataset.key = key;
    tr.innerHTML =
      "<td>" + escapeHtml(r.issuer_name) + "</td>" +
      "<td>" + r.tenor_months + "m" + (r.is_standard ? " \u2605" : "") + "</td>" +
      "<td>" + r.expiry_date + "</td>" +
      "<td>" + Number(r.par_spread).toFixed(2) + "</td>" +
      "<td>" + (r.upfront >= 0 ? "+" : "") + Number(r.upfront).toFixed(4) + "</td>" +
      "<td>" + Number(r.rebate).toFixed(4) + "</td>" +
      "<td>" + (r.net_premium >= 0 ? "+" : "") + Number(r.net_premium).toFixed(4) + "</td>" +
      "<td>" + Number(r.dv01).toFixed(6) + "</td>" +
      "<td>" + Number(r.credit_dv01).toFixed(6) + "</td>";
    tr.addEventListener("click", () => toggleCdsRow(tr, r));
    tbody.appendChild(tr);
    if (isExpanded) appendDetailRow(tr, r);
  });
}

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

function appendDetailRow(tr, row) {
  const detail = document.createElement("tr");
  detail.classList.add("cds-detail");
  const td = document.createElement("td");
  td.colSpan = 9;

  const breakdown = row.breakdown || [];
  const header =
    '<tr><th>Date</th><th>&Delta;</th><th>DF</th><th>Q</th>' +
    '<th>Premium PV</th><th>Protection PV</th></tr>';
  const periodRows = breakdown
    .map(
      (b) =>
        "<tr><td>" + b.date + "</td>" +
        "<td>" + Number(b.year_fraction).toFixed(8) + "</td>" +
        "<td>" + Number(b.df).toFixed(6) + "</td>" +
        "<td>" + Number(b.survival).toFixed(6) + "</td>" +
        "<td>" + Number(b.premium_pv).toFixed(8) + "</td>" +
        "<td>" + Number(b.protection_pv).toFixed(8) + "</td></tr>"
    )
    .join("");

  td.innerHTML =
    '<div class="detail-card">' +
    "<h4>Pricing breakdown — " +
    escapeHtml(row.issuer_name) + " " + row.tenor_months + "m</h4>" +
    '<table><thead>' + header + '</thead><tbody>' + periodRows + "</tbody></table>" +
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
}

function initCds() {
  el("cds-refresh").addEventListener("click", priceCds);
  el("cds-standard-only").addEventListener("change", () => {
    cdsState.expanded.clear();
    renderCdsTable();
  });
  el("cds-issuer-filter").addEventListener("change", () => {
    cdsState.expanded.clear();
    renderCdsTable();
  });
}

initCds();
