"use strict";

// Reuses globals from app.js: el, apiFetch, escapeHtml, wireSegmented, chip.

const dataState = { mode: "rates", selectedCalendarId: null };

function dataStatus(message, kind) {
  const node = el("data-status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

function renderDataSummary(rateCount, calCount) {
  el("data-summary").innerHTML = [
    chip("Rate indices", String(rateCount)),
    chip("Calendars", String(calCount)),
  ].join("");
}

// --- rate indices ----------------------------------------------------------

async function loadRateIndices() {
  const indices = await apiFetch("GET", "/api/data/rate-indices");
  const calCount = (await apiFetch("GET", "/api/data/calendars")).length;
  renderDataSummary(indices.length, calCount);
  const tbody = el("rates-table").querySelector("tbody");
  tbody.innerHTML = indices
    .map(
      (r) =>
        '<tr class="ri-row" data-id="' + r.id + '">' +
        "<td>" + escapeHtml(r.name) + "</td>" +
        "<td>" + escapeHtml(r.currency) + "</td>" +
        "<td>" + escapeHtml(r.day_count) + "</td>" +
        "<td>" + r.spot_lag + "</td>" +
        "<td>" + r.payment_lag + "</td>" +
        "<td>" + escapeHtml(r.fixed_frequency) + "</td>" +
        "<td>" + escapeHtml(r.business_day_convention) + "</td>" +
        '<td><button class="bond-del" data-id="' + r.id + '" type="button" title="Delete">&times;</button></td></tr>'
    )
    .join("");
  tbody.querySelectorAll(".ri-row").forEach((tr) =>
    tr.addEventListener("click", (e) => {
      if (e.target.closest(".bond-del")) return;
      openRiModal(parseInt(tr.dataset.id), indices);
    })
  );
  tbody.querySelectorAll(".bond-del").forEach((btn) =>
    btn.addEventListener("click", () => deleteRateIndex(btn.dataset.id))
  );
}

async function deleteRateIndex(id) {
  if (!window.confirm("Delete this rate index?")) return;
  try {
    await apiFetch("DELETE", "/api/data/rate-indices/" + id);
    await loadRateIndices();
    dataStatus("Deleted", "ok");
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

async function addRateIndex() {
  const name = el("ri-name").value.trim();
  if (!name) return;
  try {
    await apiFetch("POST", "/api/data/rate-indices", {
      name,
      currency: el("ri-currency").value,
      day_count: el("ri-dc").value,
      spot_lag: parseInt(el("ri-spot").value) || 0,
      payment_lag: parseInt(el("ri-pay").value) || 0,
      fixed_frequency: el("ri-freq").value,
      business_day_convention: el("ri-bdc").value,
    });
    el("ri-name").value = "";
    await loadRateIndices();
    dataStatus("Added", "ok");
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

// --- rate index modal -------------------------------------------------------

function openRiModal(id, indices) {
  const ri = indices.find((r) => r.id === id);
  if (!ri) return;
  el("ri-edit-id").value = ri.id;
  el("ri-edit-name").value = ri.name;
  el("ri-edit-currency").value = ri.currency;
  el("ri-edit-dc").value = ri.day_count;
  el("ri-edit-spot").value = ri.spot_lag;
  el("ri-edit-pay").value = ri.payment_lag;
  el("ri-edit-freq").value = ri.fixed_frequency;
  el("ri-edit-bdc").value = ri.business_day_convention;
  el("ri-modal").hidden = false;
  el("ri-edit-name").focus();
  el("ri-modal-status").textContent = "";
  el("ri-modal-status").className = "status";
}

function closeRiModal() {
  el("ri-modal").hidden = true;
}

async function saveRiModal() {
  const id = el("ri-edit-id").value;
  try {
    const updated = await apiFetch("PATCH", "/api/data/rate-indices/" + id, {
      name: el("ri-edit-name").value,
      currency: el("ri-edit-currency").value,
      day_count: el("ri-edit-dc").value,
      spot_lag: parseInt(el("ri-edit-spot").value) || 0,
      payment_lag: parseInt(el("ri-edit-pay").value) || 0,
      fixed_frequency: el("ri-edit-freq").value,
      business_day_convention: el("ri-edit-bdc").value,
    });
    closeRiModal();
    await loadRateIndices();
    dataStatus("Saved: " + updated.name, "ok");
  } catch (err) {
    const s = el("ri-modal-status");
    s.textContent = err.message;
    s.className = "status error";
  }
}

// --- calendars --------------------------------------------------------------

async function loadCalendars() {
  const calendars = await apiFetch("GET", "/api/data/calendars");
  const riCount = (await apiFetch("GET", "/api/data/rate-indices")).length;
  renderDataSummary(riCount, calendars.length);
  const tbody = el("calendars-table").querySelector("tbody");
  tbody.innerHTML = calendars
    .map(
      (c) => {
        const sel = c.id === dataState.selectedCalendarId ? " expanded" : "";
        return (
          '<tr class="' + sel + '">' +
          '<td><a href="#" class="cal-link" data-id="' + c.id + '" data-code="' + escapeHtml(c.code) + '">' +
          escapeHtml(c.code) + "</a></td>" +
          "<td>" + escapeHtml(c.description) + "</td>" +
          "<td>" + escapeHtml(c.currency) + "</td>" +
          '<td class="icon-col toggle-flag" data-field="is_default_for_cds" data-id="' + c.id + '">' +
          (c.is_default_for_cds ? "\u2713" : "") + "</td>" +
          '<td class="icon-col toggle-flag" data-field="is_default_for_ois" data-id="' + c.id + '">' +
          (c.is_default_for_ois ? "\u2713" : "") + "</td>" +
          "<td>" + c.date_count + "</td>" +
          '<td><button class="bond-del" data-id="' + c.id + '" type="button" title="Delete">&times;</button></td></tr>'
        );
      }
    )
    .join("");
  tbody.querySelectorAll(".cal-link").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const id = parseInt(a.dataset.id);
      selectCalendar(dataState.selectedCalendarId === id ? null : id, a.dataset.code);
    })
  );
  tbody.querySelectorAll(".toggle-flag").forEach((td) =>
    td.addEventListener("click", () => toggleCalendarFlag(parseInt(td.dataset.id), td.dataset.field))
  );
  tbody.querySelectorAll(".bond-del").forEach((btn) =>
    btn.addEventListener("click", () => deleteCalendar(btn.dataset.id))
  );
}

async function toggleCalendarFlag(id, field) {
  const payload = {};
  payload[field] = true;  // will toggle below
  try {
    const cal = await apiFetch("GET", "/api/data/calendars");
    const current = cal.find((c) => c.id === id);
    if (!current) return;
    payload[field] = !current[field];
    await apiFetch("PATCH", "/api/data/calendars/" + id, payload);
    await loadCalendars();
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

async function deleteCalendar(id) {
  if (!window.confirm("Delete this calendar and all its dates?")) return;
  try {
    await apiFetch("DELETE", "/api/data/calendars/" + id);
    if (dataState.selectedCalendarId === id) selectCalendar(null);
    await loadCalendars();
    dataStatus("Deleted", "ok");
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

async function addCalendar() {
  const code = el("cal-code").value.trim();
  if (!code) return;
  try {
    await apiFetch("POST", "/api/data/calendars", {
      code,
      description: el("cal-desc").value,
      currency: el("cal-currency").value,
      is_default_for_cds: el("cal-cds-def").checked,
      is_default_for_ois: el("cal-ois-def").checked,
    });
    el("cal-code").value = "";
    await loadCalendars();
    dataStatus("Added", "ok");
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

// --- holiday editor ---------------------------------------------------------

async function selectCalendar(id, code) {
  dataState.selectedCalendarId = id;
  el("holiday-editor").hidden = !id;
  if (!id) { el("holiday-list").innerHTML = ""; return; }
  el("holiday-editor-title").textContent = "Holidays — " + (code || id);
  await loadHolidays();
}

async function loadHolidays() {
  if (!dataState.selectedCalendarId) return;
  const holidays = await apiFetch(
    "GET", "/api/data/calendars/" + dataState.selectedCalendarId + "/holidays"
  );
  el("holiday-list").innerHTML = holidays
    .map(
      (h) =>
        '<div class="bond-row"><div class="bond-main">' + h.date +
        '</div><button class="bond-del" data-id="' + h.id + '" type="button">&times;</button></div>'
    )
    .join("");
  el("holiday-list").querySelectorAll(".bond-del").forEach((btn) =>
    btn.addEventListener("click", () => deleteHoliday(btn.dataset.id))
  );
}

async function addHoliday() {
  if (!dataState.selectedCalendarId) return;
  const date = el("holiday-date").value;
  if (!date) return;
  try {
    await apiFetch(
      "POST",
      "/api/data/calendars/" + dataState.selectedCalendarId + "/holidays",
      { date }
    );
    el("holiday-date").value = "";
    await loadHolidays();
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

async function deleteHoliday(id) {
  try {
    await apiFetch(
      "DELETE",
      "/api/data/calendars/" + dataState.selectedCalendarId + "/holidays/" + id
    );
    await loadHolidays();
  } catch (err) {
    dataStatus(err.message, "error");
  }
}

async function importDates() {
  if (!dataState.selectedCalendarId) return;
  const text = el("import-dates").value.trim();
  if (!text) return;
  let dates;
  try {
    dates = JSON.parse(text);
    if (!Array.isArray(dates)) throw new Error("Must be a JSON array");
  } catch (err) {
    dataStatus("Invalid JSON: " + err.message, "error");
    return;
  }
  let ok = 0, fail = 0;
  for (const d of dates) {
    try {
      await apiFetch(
        "POST",
        "/api/data/calendars/" + dataState.selectedCalendarId + "/holidays",
        { date: String(d).trim() }
      );
      ok++;
    } catch (_) { fail++; }
  }
  el("import-dates").value = "";
  await loadHolidays();
  dataStatus("Imported " + ok + (fail ? " (" + fail + " failed)" : ""), "ok");
}

// --- export -----------------------------------------------------------------

function exportRateIndices() {
  window.location.href = "/api/data/rate-indices/export";
}

function exportCalendar() {
  if (dataState.selectedCalendarId) {
    window.location.href = "/api/data/calendars/" + dataState.selectedCalendarId + "/export";
  } else {
    dataStatus("Select a calendar first", "error");
  }
}

// --- mode switching ---------------------------------------------------------

function setMode(mode) {
  dataState.mode = mode;
  dataState.selectedCalendarId = null;
  el("holiday-editor").hidden = true;
  el("data-rates-card").hidden = mode !== "rates";
  el("data-calendars-card").hidden = mode !== "calendars";
  if (mode === "rates") loadRateIndices();
  else loadCalendars();
}

// --- init -------------------------------------------------------------------

function initData() {
  wireSegmented("data-mode", setMode);
  el("add-rate-index").addEventListener("click", addRateIndex);
  el("add-calendar").addEventListener("click", addCalendar);
  el("add-holiday").addEventListener("click", addHoliday);
  el("holiday-date").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addHoliday(); }
  });
  el("import-dates-btn").addEventListener("click", importDates);
  el("export-rate-indices").addEventListener("click", exportRateIndices);
  el("export-calendar").addEventListener("click", exportCalendar);
  el("ri-save").addEventListener("click", saveRiModal);
  el("ri-close").addEventListener("click", closeRiModal);
  el("ri-modal").addEventListener("click", (e) => {
    if (e.target === el("ri-modal")) closeRiModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !el("ri-modal").hidden) closeRiModal();
  });
  setMode("rates");
}

initData();
