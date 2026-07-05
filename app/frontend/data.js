"use strict";

// Reuses globals from app.js: el, apiFetch, escapeHtml, wireSegmented.

const dataState = { mode: "rates", selectedCalendarId: null };

function dataStatus(message, kind) {
  const node = el("data-status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

// --- load + render ---------------------------------------------------------

async function loadRateIndices() {
  const indices = await apiFetch("GET", "/api/data/rate-indices");
  const tbody = el("rates-table").querySelector("tbody");
  tbody.innerHTML = indices
    .map(
      (r) =>
        "<tr><td>" + escapeHtml(r.name) + "</td>" +
        "<td>" + escapeHtml(r.currency) + "</td>" +
        "<td>" + escapeHtml(r.day_count) + "</td>" +
        "<td>" + r.spot_lag + "</td>" +
        "<td>" + r.payment_lag + "</td>" +
        "<td>" + escapeHtml(r.fixed_frequency) + "</td>" +
        "<td>" + escapeHtml(r.business_day_convention) + "</td>" +
        '<td><button class="bond-del" data-id="' + r.id + '" type="button">&times;</button></td></tr>'
    )
    .join("");
  tbody.querySelectorAll(".bond-del").forEach((btn) =>
    btn.addEventListener("click", () => deleteRateIndex(btn.dataset.id))
  );
}

async function deleteRateIndex(id) {
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

// --- calendars --------------------------------------------------------------

async function loadCalendars() {
  const calendars = await apiFetch("GET", "/api/data/calendars");
  const tbody = el("calendars-table").querySelector("tbody");
  tbody.innerHTML = calendars
    .map(
      (c) =>
        "<tr><td><a href=\"#\" class=\"cal-link\" data-id=\"" + c.id + "\">" +
        escapeHtml(c.code) + "</a></td>" +
        "<td>" + escapeHtml(c.description) + "</td>" +
        "<td>" + escapeHtml(c.currency) + "</td>" +
        '<td class="icon-col">' + (c.is_default_for_cds ? "\u2713" : "") + "</td>" +
        '<td class="icon-col">' + (c.is_default_for_ois ? "\u2713" : "") + "</td>" +
        "<td>" + c.date_count + "</td>" +
        '<td><button class="bond-del" data-id="' + c.id + '" type="button">&times;</button></td></tr>'
    )
    .join("");
  tbody.querySelectorAll(".cal-link").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      selectCalendar(parseInt(a.dataset.id));
    })
  );
  tbody.querySelectorAll(".bond-del").forEach((btn) =>
    btn.addEventListener("click", () => deleteCalendar(btn.dataset.id))
  );
}

async function deleteCalendar(id) {
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

async function selectCalendar(id) {
  dataState.selectedCalendarId = id;
  el("holiday-editor").hidden = !id;
  if (!id) { el("holiday-list").innerHTML = ""; return; }
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
    dataStatus("Date added", "ok");
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
  setMode("rates");
}

initData();
