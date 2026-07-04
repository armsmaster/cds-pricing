"use strict";

// Reuses globals from app.js (loaded first): el, PALETTE, PLOT_CONFIG,
// baseLayout, chip. Nav/tab switching and the help-popover system live in app.js;
// app.js calls onCreditShown() the first time the Credit tab is opened.

const creditState = {
  loaded: false,
  issuers: [],
  issuerId: null,
  rateCurves: [],
  result: null,
};

function escapeHtml(value) {
  return String(value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function apiFetch(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

function creditStatus(message, kind) {
  const node = el("credit-status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

// --- lazy load + hooks (called from app.js) -------------------------------

async function onCreditShown() {
  if (creditState.loaded) return;
  creditState.loaded = true;
  el("credit-trade-date").value = new Date().toISOString().slice(0, 10);
  await Promise.all([loadIssuers(), loadRateCurves()]);
}

function onRateCurvesChanged() {
  if (creditState.loaded) loadRateCurves();
}

// --- issuers --------------------------------------------------------------

async function loadIssuers() {
  creditState.issuers = await apiFetch("GET", "/api/issuers");
  const select = el("credit-issuer");
  select.innerHTML = creditState.issuers
    .map((i) => `<option value="${i.id}">${escapeHtml(i.name)}</option>`)
    .join("");
  if (creditState.issuers.length) {
    if (!creditState.issuers.some((i) => i.id === creditState.issuerId)) {
      creditState.issuerId = creditState.issuers[0].id;
    }
    select.value = String(creditState.issuerId);
    await selectIssuer(creditState.issuerId);
  } else {
    creditState.issuerId = null;
    el("bond-list").innerHTML = "";
  }
}

async function selectIssuer(issuerId) {
  creditState.issuerId = issuerId;
  const issuer = creditState.issuers.find((i) => i.id === issuerId);
  if (issuer) el("credit-recovery").value = issuer.recovery_rate;
  await loadBonds();
}

async function addIssuer() {
  const name = el("new-issuer-name").value.trim();
  if (!name) {
    creditStatus("Enter an issuer name", "error");
    return;
  }
  const recovery = parseFloat(el("new-issuer-recovery").value) || 0.4;
  try {
    const created = await apiFetch("POST", "/api/issuers", {
      name,
      recovery_rate: recovery,
    });
    el("new-issuer-name").value = "";
    creditState.issuerId = created.id;
    await loadIssuers();
    el("credit-issuer").value = String(created.id);
    creditStatus("Added issuer " + created.name, "ok");
  } catch (err) {
    creditStatus(err.message, "error");
  }
}

async function saveRecovery() {
  if (!creditState.issuerId) return;
  const recovery = parseFloat(el("credit-recovery").value);
  try {
    await apiFetch("PATCH", "/api/issuers/" + creditState.issuerId, {
      recovery_rate: recovery,
    });
    const issuer = creditState.issuers.find((i) => i.id === creditState.issuerId);
    if (issuer) issuer.recovery_rate = recovery;
    creditStatus("Recovery rate updated", "ok");
  } catch (err) {
    creditStatus(err.message, "error");
  }
}

async function deleteIssuer() {
  if (!creditState.issuerId) return;
  if (!window.confirm("Delete this issuer and all its bonds?")) return;
  try {
    await apiFetch("DELETE", "/api/issuers/" + creditState.issuerId);
    creditState.issuerId = null;
    await loadIssuers();
    creditStatus("Issuer deleted", "ok");
  } catch (err) {
    creditStatus(err.message, "error");
  }
}

// --- bonds ----------------------------------------------------------------

async function loadBonds() {
  if (!creditState.issuerId) {
    el("bond-list").innerHTML = "";
    return;
  }
  const bonds = await apiFetch(
    "GET",
    "/api/issuers/" + creditState.issuerId + "/bonds"
  );
  renderBondList(bonds);
}

function renderBondList(bonds) {
  el("bond-list").innerHTML = bonds
    .map((b) => {
      const sub =
        escapeHtml(b.shortname || b.name || "") +
        " \u00b7 mat " +
        (b.maturity_date || "?") +
        (b.offer_date ? " \u00b7 put " + b.offer_date : "");
      return (
        '<div class="bond-row"><div class="bond-main">' +
        '<div class="bond-isin">' + escapeHtml(b.isin) + "</div>" +
        '<div class="bond-sub">' + sub + "</div></div>" +
        '<button class="bond-del" type="button" data-isin="' +
        escapeHtml(b.isin) + '" title="Remove">&times;</button></div>'
      );
    })
    .join("");
  el("bond-list")
    .querySelectorAll(".bond-del")
    .forEach((btn) =>
      btn.addEventListener("click", () => deleteBond(btn.dataset.isin))
    );
}

async function addBond() {
  const isin = el("bond-isin").value.trim().toUpperCase();
  if (!isin) return;
  if (!creditState.issuerId) {
    creditStatus("Select or create an issuer first", "error");
    return;
  }
  const btn = el("add-bond");
  btn.disabled = true;
  creditStatus("Fetching " + isin + " from MOEX\u2026");
  try {
    await apiFetch("POST", "/api/issuers/" + creditState.issuerId + "/bonds", {
      isin,
    });
    el("bond-isin").value = "";
    await loadBonds();
    creditStatus("Added " + isin, "ok");
  } catch (err) {
    creditStatus(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

async function deleteBond(isin) {
  try {
    await apiFetch("DELETE", "/api/bonds/" + isin);
    await loadBonds();
  } catch (err) {
    creditStatus(err.message, "error");
  }
}

// --- rate curves ----------------------------------------------------------

async function loadRateCurves() {
  creditState.rateCurves = await apiFetch("GET", "/api/rate-curves");
  const select = el("credit-rate-curve");
  const previous = select.value;
  select.innerHTML = creditState.rateCurves
    .map((c) => `<option value="${c.id}">${escapeHtml(c.rate_index)} @ ${c.trade_date}</option>`)
    .join("");
  if (previous && creditState.rateCurves.some((c) => String(c.id) === previous)) {
    select.value = previous;
  }
}

// --- compute + render -----------------------------------------------------

async function calculateCredit() {
  if (!creditState.issuerId) {
    creditStatus("Select an issuer", "error");
    return;
  }
  const rateCurveId = el("credit-rate-curve").value;
  if (!rateCurveId) {
    creditStatus("Save a risk-free curve on the Rate tab, then select it", "error");
    return;
  }
  const btn = el("credit-calculate");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Calculating\u2026';
  creditStatus("Fetching prices & bootstrapping\u2026");
  try {
    const result = await apiFetch("POST", "/api/credit-curve", {
      issuer_id: creditState.issuerId,
      rate_curve_id: parseInt(rateCurveId, 10),
      trade_date: el("credit-trade-date").value || null,
    });
    creditState.result = result;
    creditStatus("Credit curve bootstrapped", "ok");
    el("credit-empty").hidden = true;
    for (const id of ["hazard-card", "inst-hazard-card", "survival-card", "credit-table-card", "hazard-export-card"]) {
      el(id).hidden = false;
    }
    renderCreditSummary();
    renderHazardChart();
    renderInstHazardChart();
    renderSurvivalChart();
    renderCreditTable();
    renderHazardExport();
  } catch (err) {
    creditStatus(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Calculate credit curve";
  }
}

function renderCreditSummary() {
  const r = creditState.result;
  const skipped = r.skipped.length;
  el("credit-summary").innerHTML = [
    chip("Issuer", escapeHtml(r.issuer_name)),
    chip("Recovery", (r.recovery_rate * 100).toFixed(0) + "%"),
    chip("Trade date", r.trade_date),
    chip("Bonds used", String(r.fits.length)),
    chip("Skipped", String(skipped), skipped ? "warn" : ""),
    chip("Smoothing \u03bb", Number(r.smoothing_lambda).toPrecision(3)),
  ].join("");
}

function renderHazardChart() {
  const c = creditState.result.curve;
  const x = c.map((n) => n.date);
  const traces = [
    { x, y: c.map((n) => n.hazard), name: "hazard", mode: "lines",
      line: { color: PALETTE.forward, width: 2 } },
    { x, y: c.map((n) => n.spread), name: "spread (hazard \u00d7 LGD)", mode: "lines",
      line: { color: PALETTE.adj, width: 2, dash: "dot" } },
  ];
  Plotly.react("hazard-chart", traces, baseLayout("rate (%)"), PLOT_CONFIG);
}

function renderInstHazardChart() {
  const c = creditState.result.curve;
  const traces = [
    { x: c.map((n) => n.date), y: c.map((n) => n.forward), name: "instantaneous hazard",
      mode: "lines", line: { color: PALETTE.ask, width: 2, shape: "hv" } },
  ];
  Plotly.react("inst-hazard-chart", traces, baseLayout("hazard rate (%)"), PLOT_CONFIG);
}

function renderSurvivalChart() {
  const c = creditState.result.curve;
  const traces = [
    { x: c.map((n) => n.date), y: c.map((n) => n.survival), name: "survival",
      mode: "lines", line: { color: PALETTE.annual, width: 2 } },
  ];
  const layout = baseLayout("survival probability");
  layout.yaxis.range = [0, 1.02];
  Plotly.react("survival-chart", traces, layout, PLOT_CONFIG);
}

function _sourceClass(source) {
  if (source === "mid") return "src-mid";
  if (source === "last" || source === "waprice") return "src-soft";
  return "src-close";
}

function _residClass(pct) {
  const a = Math.abs(pct);
  if (a <= 0.1) return "good";
  if (a <= 0.5) return "warn";
  return "bad";
}

function renderCreditTable() {
  const r = creditState.result;
  const maxWeight = Math.max(1e-9, ...r.fits.map((f) => f.weight));
  const RESID_FULL = 1.0; // % residual that fills half the diverging bar

  el("credit-fit-table").querySelector("tbody").innerHTML = r.fits
    .map((f) => {
      const wpct = Math.min(100, (f.weight / maxWeight) * 100);
      const rp = f.residual_pct;
      const half = Math.min(50, (Math.abs(rp) / RESID_FULL) * 50);
      const market_yield = f.market_yield != null ? Number(f.market_yield).toFixed(2) + "%" : "\u2014";
      const model_yield = f.model_yield != null ? Number(f.model_yield).toFixed(2) + "%" : "\u2014";
      const cls = _residClass(rp);
      const fill =
        rp >= 0
          ? `left:50%;width:${half}%;`
          : `left:${50 - half}%;width:${half}%;`;
      return (
        '<tr><td class="bond-cell"><div class="name">' +
        escapeHtml(f.name || f.isin) + '</div><div class="isin">' +
        escapeHtml(f.isin) + "</div></td>" +
        "<td>" + f.maturity_date +
        ' <span class="mat-tenor">\u00b7 ' + Number(f.years).toFixed(1) + "y</span></td>" +
        '<td class="src"><span class="pill ' + _sourceClass(f.source) + '">' +
        escapeHtml(f.source) + "</span></td>" +
        '<td class="px"><div class="cell-main">' + Number(f.market_clean).toFixed(3) +
        '</div><div class="cell-sub">' + market_yield + "</div></td>" +
        '<td class="px"><div class="cell-main">' + Number(f.model_clean).toFixed(3) +
        '</div><div class="cell-sub">' + model_yield + "</div></td>" +
        '<td class="rcell"><span class="rval">' +
        (rp >= 0 ? "+" : "") + Number(rp).toFixed(2) + "%</span>" +
        '<span class="dbar"><span class="dbar-fill ' + cls + '" style="' + fill + '"></span></span></td>' +
        '<td class="wcell"><span class="wtrack"><span class="wbar" style="width:' +
        wpct.toFixed(0) + '%"></span></span></td></tr>'
      );
    })
    .join("");

  const resids = r.fits.map((f) => Math.abs(f.residual_pct));
  const avg = resids.reduce((a, b) => a + b, 0) / (resids.length || 1);
  const max = resids.length ? Math.max(...resids) : 0;
  el("credit-fit-table").querySelector("tfoot").innerHTML =
    "<tr><td colspan=\"7\">" +
    r.fits.length + " bond" + (r.fits.length === 1 ? "" : "s") +
    " \u00b7 avg |residual| " + avg.toFixed(2) + "% \u00b7 max " + max.toFixed(2) + "%</td></tr>";

  const skip = r.skipped;
  el("skipped").innerHTML =
    (skip.length ? '<div class="skipped-title">Excluded (' + skip.length + ")</div>" : "") +
    skip
      .map((s) => '<span class="pill">' + escapeHtml(s.isin) + ": " + escapeHtml(s.reason) + "</span>")
      .join("");
}

function renderHazardExport() {
  el("hazard-json").value = JSON.stringify(creditState.result.hazard_export, null, 2);
}

async function copyHazard() {
  try {
    await navigator.clipboard.writeText(el("hazard-json").value);
    const s = el("hazard-copy-status");
    s.textContent = "Copied!";
    setTimeout(() => (s.textContent = ""), 1500);
  } catch (_) {
    el("hazard-json").select();
  }
}

function downloadHazard() {
  const r = creditState.result;
  if (!r) return;
  const blob = new Blob([el("hazard-json").value], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "hazard_" + r.issuer_name.replace(/\s+/g, "_") + "_" + r.trade_date + ".json";
  a.click();
  URL.revokeObjectURL(url);
}

function initCredit() {
  el("add-issuer").addEventListener("click", addIssuer);
  el("save-recovery").addEventListener("click", saveRecovery);
  el("delete-issuer").addEventListener("click", deleteIssuer);
  el("add-bond").addEventListener("click", addBond);
  el("bond-isin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addBond();
    }
  });
  el("credit-issuer").addEventListener("change", (e) =>
    selectIssuer(parseInt(e.target.value, 10))
  );
  el("credit-calculate").addEventListener("click", calculateCredit);
  el("copy-hazard").addEventListener("click", copyHazard);
  el("download-hazard").addEventListener("click", downloadHazard);
}

initCredit();
