"use strict";

const DEFAULT_QUOTES = [
  { rate: "RUONIA", tenor: "1m", bid: 14.15, ask: 14.35 },
  { rate: "RUONIA", tenor: "2m", bid: 14.15, ask: 14.35 },
  { rate: "RUONIA", tenor: "3m", bid: 14.13, ask: 14.38 },
  { rate: "RUONIA", tenor: "6m", bid: 14.32, ask: 14.57 },
  { rate: "RUONIA", tenor: "9m", bid: 14.44, ask: 14.64 },
  { rate: "RUONIA", tenor: "1y", bid: 14.48, ask: 14.68 },
  { rate: "RUONIA", tenor: "2y", bid: 14.27, ask: 14.47 },
  { rate: "RUONIA", tenor: "3y", bid: 13.29, ask: 14.49 },
  { rate: "RUONIA", tenor: "4y", bid: 14.35, ask: 14.52 },
  { rate: "RUONIA", tenor: "5y", bid: 14.40, ask: 14.59 },
  { rate: "RUONIA", tenor: "7y", bid: 14.48, ask: 14.68 },
  { rate: "RUONIA", tenor: "10y", bid: 14.53, ask: 14.59 },
];

const el = (id) => document.getElementById(id);
const state = { preview: null, bootstrap: null, curveMode: "both", zcycMode: "annual" };

const PALETTE = {
  text: "#e2e8f0", muted: "#94a3b8", grid: "#334155",
  panel: "#1e293b", border: "#334155",
  bid: "#f87171", ask: "#fbbf24", mid: "#94a3b8", band: "rgba(148,163,184,0.13)",
  adj: "#38bdf8", annual: "#22c55e", cont: "#38bdf8", forward: "#a78bfa",
};

const PLOT_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: [
    "lasso2d", "select2d", "autoScale2d",
    "toggleSpikelines", "hoverClosestCartesian", "hoverCompareCartesian",
  ],
};

function baseLayout(yTitle) {
  const ax = { gridcolor: PALETTE.grid, zerolinecolor: PALETTE.grid, color: PALETTE.muted };
  return {
    margin: { l: 55, r: 20, t: 10, b: 40 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: PALETTE.text, size: 12 },
    legend: {
      orientation: "h", y: 1.12, x: 0,
      bgcolor: PALETTE.panel, bordercolor: PALETTE.border, font: { color: PALETTE.text },
    },
    xaxis: { type: "date", title: "maturity", ...ax },
    yaxis: { title: yTitle, ...ax },
    hovermode: "x unified",
    hoverlabel: { bgcolor: PALETTE.panel, bordercolor: PALETTE.border, font: { color: PALETTE.text } },
  };
}

function setStatus(message, kind) {
  const node = el("status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

function setQuotesValidity(valid) {
  const t = el("quotes");
  t.classList.toggle("valid", valid === true);
  t.classList.toggle("invalid", valid === false);
}

function readQuotes() {
  const text = el("quotes").value.trim();
  if (!text) return [];
  const parsed = JSON.parse(text);
  if (!Array.isArray(parsed)) throw new Error("JSON must be an array of quotes");
  return parsed;
}

function requestBody(extra) {
  const tradeDate = el("trade-date").value || null;
  return JSON.stringify({ quotes: readQuotes(), trade_date: tradeDate, ...extra });
}

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json();
}

async function preview() {
  let body;
  try {
    body = requestBody({});
    setQuotesValidity(el("quotes").value.trim() ? true : null);
  } catch (err) {
    setQuotesValidity(false);
    setStatus("Invalid JSON: " + err.message, "error");
    return;
  }
  try {
    state.preview = await postJSON("/api/quotes", body);
    setStatus("Quotes loaded (" + state.preview.quotes.length + ")", "ok");
    renderParChart();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function setBusy(on) {
  const btn = el("calculate");
  btn.disabled = on;
  btn.innerHTML = on ? '<span class="spinner"></span> Calculating\u2026' : "Calculate";
}

async function calculate() {
  if (el("calculate").disabled) return;
  let body;
  try {
    body = requestBody({ max_adjustment_bps: parseFloat(el("max-adj").value) || 15 });
    setQuotesValidity(true);
  } catch (err) {
    setQuotesValidity(false);
    setStatus("Invalid JSON: " + err.message, "error");
    return;
  }
  setBusy(true);
  setStatus("Calculating\u2026");
  try {
    state.bootstrap = await postJSON("/api/bootstrap", body);
    setStatus("Curve bootstrapped", "ok");
    el("empty-state").hidden = true;
    for (const id of ["curve-card", "forward-card", "table-card", "zcyc-card"]) {
      el(id).hidden = false;
    }
    renderParChart();
    renderCurveChart();
    renderForwardChart();
    renderTable();
    renderZcyc();
    renderSummary();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    setBusy(false);
  }
}

function chip(key, value, cls) {
  return (
    '<div class="stat"><div class="k">' + key + '</div>' +
    '<div class="v ' + (cls || "") + '">' + value + "</div></div>"
  );
}

function renderSummary() {
  const b = state.bootstrap;
  const cap = parseFloat(el("max-adj").value) || 15;
  const avg = b.average_adjustment_bps;
  const cls = avg >= cap - 1e-6 ? "bad" : avg >= 0.8 * cap ? "warn" : "good";
  el("summary").innerHTML = [
    chip("Rate", b.rate_index),
    chip("Spot", b.spot_date),
    chip("Avg adjustment", avg.toFixed(2) + " bps", cls),
    chip("Max adjustment", cap.toFixed(1) + " bps"),
    chip("Smoothing \u03bb", Number(b.smoothing_lambda).toPrecision(3)),
    chip("Nodes", b.curve.length + " \u00b7 monthly"),
  ].join("");
}

function renderParChart() {
  if (!state.preview) return;
  const q = state.preview.quotes;
  const x = q.map((r) => r.maturity_date);
  const bid = q.map((r) => r.bid);
  const ask = q.map((r) => r.ask);
  const mid = q.map((r) => r.mid);
  const traces = [
    { x, y: ask, mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
    { x, y: bid, mode: "lines", line: { width: 0 }, fill: "tonexty",
      fillcolor: PALETTE.band, name: "bid\u2013ask", hoverinfo: "skip" },
    { x, y: bid, name: "bid", mode: "markers",
      marker: { color: PALETTE.bid, symbol: "triangle-down", size: 8 } },
    { x, y: ask, name: "ask", mode: "markers",
      marker: { color: PALETTE.ask, symbol: "triangle-up", size: 8 } },
    { x, y: mid, name: "mid", mode: "lines+markers",
      line: { color: PALETTE.mid, dash: "dot" }, marker: { color: PALETTE.mid, size: 6 } },
  ];
  if (state.bootstrap) {
    const f = state.bootstrap.fits;
    traces.push({
      x: f.map((r) => r.maturity_date),
      y: f.map((r) => r.adj_mid),
      name: "adjusted mid",
      mode: "lines+markers",
      line: { color: PALETTE.adj, width: 2 },
      marker: { color: PALETTE.adj, size: 7 },
    });
  }
  Plotly.react("par-chart", traces, baseLayout("par rate (%)"), PLOT_CONFIG);
}

function renderCurveChart() {
  const c = state.bootstrap.curve;
  const x = c.map((r) => r.date);
  const mode = state.curveMode;
  const traces = [];
  if (mode === "annual" || mode === "both") {
    traces.push({ x, y: c.map((r) => r.zero_annual), name: "annual", mode: "lines",
      line: { color: PALETTE.annual, width: 2 } });
  }
  if (mode === "continuous" || mode === "both") {
    traces.push({ x, y: c.map((r) => r.zero_cont), name: "continuous", mode: "lines",
      line: { color: PALETTE.cont, width: 2, dash: mode === "both" ? "dot" : "solid" } });
  }
  Plotly.react("curve-chart", traces, baseLayout("zero rate (%)"), PLOT_CONFIG);
}

function renderForwardChart() {
  const f = state.bootstrap.forwards;
  const traces = [
    { x: f.map((r) => r.date), y: f.map((r) => r.forward), name: "forward",
      mode: "lines", line: { color: PALETTE.forward, width: 2, shape: "hv" } },
  ];
  Plotly.react("forward-chart", traces, baseLayout("forward rate (%)"), PLOT_CONFIG);
}

function renderTable() {
  const rows = state.bootstrap.fits.map((r) => {
    const pill = r.within_spread
      ? '<span class="pill yes">yes</span>'
      : '<span class="pill no">no</span>';
    const adjCls = r.adj_bps >= 0 ? "adj-pos" : "adj-neg";
    const rowCls = r.within_spread ? "" : ' class="out-of-spread"';
    return (
      "<tr" + rowCls + "><td>" + r.tenor + "</td><td>" + r.maturity_date + "</td>" +
      "<td>" + r.bid.toFixed(3) + "</td><td>" + r.ask.toFixed(3) + "</td>" +
      "<td>" + r.raw_mid.toFixed(3) + "</td><td>" + r.adj_mid.toFixed(3) + "</td>" +
      '<td class="' + adjCls + '">' + (r.adj_bps >= 0 ? "+" : "") + r.adj_bps.toFixed(1) + "</td>" +
      "<td>" + pill + "</td></tr>"
    );
  });
  el("fit-table").querySelector("tbody").innerHTML = rows.join("");
}

function renderZcyc() {
  if (!state.bootstrap) return;
  const zcyc = {};
  for (const node of state.bootstrap.curve) {
    const pct = state.zcycMode === "continuous" ? node.zero_cont : node.zero_annual;
    zcyc[node.date] = Number((pct / 100).toFixed(8));
  }
  el("zcyc").value = JSON.stringify(zcyc, null, 2);
}

async function copyZcyc() {
  try {
    await navigator.clipboard.writeText(el("zcyc").value);
    const s = el("copy-status");
    s.textContent = "Copied!";
    setTimeout(() => (s.textContent = ""), 1500);
  } catch (_) {
    el("zcyc").select();
  }
}

function downloadZcyc() {
  const b = state.bootstrap;
  if (!b) return;
  const blob = new Blob([el("zcyc").value], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "zcyc_" + b.rate_index + "_" + b.trade_date + ".json";
  a.click();
  URL.revokeObjectURL(url);
}

function wireSegmented(groupId, onSelect) {
  const buttons = document.querySelectorAll("#" + groupId + " button");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => {
        const active = b === btn;
        b.classList.toggle("active", active);
        b.setAttribute("aria-pressed", String(active));
      });
      onSelect(btn.dataset.mode);
    });
  });
}

function debounce(fn, ms) {
  let handle;
  return (...args) => {
    clearTimeout(handle);
    handle = setTimeout(() => fn(...args), ms);
  };
}

function init() {
  el("trade-date").value = new Date().toISOString().slice(0, 10);
  el("quotes").value = JSON.stringify(DEFAULT_QUOTES, null, 2);

  const debouncedPreview = debounce(preview, 350);
  el("quotes").addEventListener("input", debouncedPreview);
  el("trade-date").addEventListener("change", preview);
  el("calculate").addEventListener("click", calculate);
  el("copy-zcyc").addEventListener("click", copyZcyc);
  el("download-zcyc").addEventListener("click", downloadZcyc);

  wireSegmented("curve-mode", (mode) => {
    state.curveMode = mode;
    if (state.bootstrap) renderCurveChart();
  });
  wireSegmented("zcyc-mode", (mode) => {
    state.zcycMode = mode;
    renderZcyc();
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      calculate();
    }
  });

  preview();
}

init();
