"use strict";

const TARGET_STEP = 0.5;
const POLL_MS = 10000;

const $ = (id) => document.getElementById(id);
const token = () => localStorage.getItem("token");

// Safe event binding: no-op (with a warning) if the element is missing, so a
// single stale/renamed element can never break the rest of the handlers.
function on(id, event, handler) {
  const el = $(id);
  if (el) el.addEventListener(event, handler);
  else console.warn(`element #${id} not found; skipping ${event} handler`);
}

let current = null; // last status payload

// Diagnostics the UI surfaces so the user can always tell what's happening.
const diag = {
  lastOk: 0,        // epoch (s) of the last successful API call
  lastError: null,  // last error message
  busyText: null,   // current in-flight action label (shown in the bottom diag line)
  busyShort: null,  // short label shown inside the triggering button
  busyTarget: null, // id of the button to show the in-button spinner on
  busyBle: false,   // true if the action involves a (slow) Bluetooth op
  busyStart: 0,     // epoch (s) the in-flight action began
};

const API_TIMEOUT_MS = 40000; // hard cap so a wedged BLE op can't hang the UI forever

async function api(path, method = "GET", body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const t = token();
  if (t) headers["X-Auth-Token"] = t;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), API_TIMEOUT_MS);
  try {
    const res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error("Timed out — the Pi didn't respond. Check it's online.");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function fmtAge(seconds) {
  if (seconds == null) return "no reading";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

function fmtClock(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function fmtDuration(seconds) {
  if (seconds <= 0) return "now";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function render(s) {
  current = s;
  const sym = s.unit === "fahrenheit" ? "F" : "C";
  $("temp").textContent = s.temperature == null ? "--" : s.temperature.toFixed(1);
  $("unit").textContent = `°${sym}`;
  $("humidity").textContent = s.humidity == null ? "--% RH" : `${s.humidity}% RH`;
  $("battery").textContent = s.battery == null ? "--% batt" : `${s.battery}% batt`;
  $("age").textContent = fmtAge(s.reading_age);
  $("target").textContent = s.target == null ? "--" : s.target.toFixed(1);
  $("unit-sm") && ($("unit-sm").textContent = `°${sym}`);

  const verb = s.action === "cool" ? "cooling" : "heating";
  $("target-meta").textContent =
    `±${s.hysteresis}°${sym} deadband · from ${s.target_source}`;

  // Surface a clear reason when there's no temperature to show, instead of a
  // silent "--". A missing or very stale reading is the usual culprit.
  const note = $("temp-note");
  if (s.temperature == null) {
    note.hidden = false;
    note.className = "temp-note warn";
    note.textContent = "⚠ No reading from the meter — it may be out of range or have a low battery. Tap ⟳ to retry.";
  } else if (s.reading_age != null && s.reading_age > 300) {
    note.hidden = false;
    note.className = "temp-note warn";
    note.textContent = `⚠ Last reading is ${fmtAge(s.reading_age)} — the meter may be struggling to connect.`;
  } else {
    note.hidden = true;
  }

  // Green "running" highlight when the thermostat is on (box-shadow glow doesn't
  // affect layout, so nothing shifts).
  const tempCard = document.querySelector(".temp-card");
  if (tempCard) tempCard.classList.toggle("running", s.believed === true);

  // Badges
  const badges = [];
  const onLabel = s.believed ? `ON (${verb})` : "OFF";
  badges.push(`<span class="badge ${s.believed ? "on" : "off"}">${onLabel}</span>`);
  badges.push(`<span class="badge mode">${s.action}</span>`);
  badges.push(`<span class="badge ${s.paused ? "paused" : ""}">${s.paused ? "Paused" : "Auto"}</span>`);
  if (s.dry_run) badges.push(`<span class="badge dry">dry-run</span>`);
  if (s.off_timer_at) badges.push(`<span class="badge timer" id="badge-timer"></span>`);
  $("badges").innerHTML = badges.join("");

  // Control states
  $("pause").textContent = s.paused ? "Resume" : "Pause";
  $("pause").classList.toggle("active", s.paused);
  $("mode").textContent = s.action === "cool" ? "Mode: Cool" : "Mode: Heat";

  // Single toggle button: label + style follow the believed state. Both
  // directions are the same physical button press on the wall thermostat.
  const toggle = $("toggle");
  toggle.classList.toggle("is-on", s.believed === true);   // currently on -> shows "Turn Off"
  toggle.classList.toggle("is-off", s.believed === false); // currently off -> shows "Turn On"

  // Auto-off timer card: only relevant while the thermostat is on.
  const timerCard = $("timer-card");
  timerCard.hidden = !s.believed;
  const active = !!s.off_timer_at;
  $("timer-idle").hidden = active;
  $("timer-active").hidden = !active;
  if (active) $("timer-when").textContent = fmtClock(s.off_timer_at);

  // Anchor a client-side countdown for the compressor-protection lock.
  s.lockUntil = s.on_locked_s > 0 ? Date.now() / 1000 + s.on_locked_s : 0;
  s._firedReload = false;
  s._lockReload = false;
  tick(); // refresh countdowns + lock button immediately
}

function fmtMMSS(seconds) {
  const m = Math.floor(Math.max(0, seconds) / 60);
  const s = String(Math.max(0, Math.floor(seconds % 60))).padStart(2, "0");
  return `${m}:${s}`;
}

// Connection pill (header) and the diagnostics footer (bottom). Neither shifts
// page content: the pill is fixed-size, the footer is the last element so its
// text can change freely. Called every second to keep elapsed times live.
function renderDiag() {
  const now = Date.now() / 1000;
  const conn = $("conn");
  const connText = $("conn-text");

  if (diag.busyText) {
    conn && (conn.className = "conn working");
    connText && (connText.textContent = "working…");
  } else {
    const sinceOk = diag.lastOk ? Math.round(now - diag.lastOk) : null;
    const offline = diag.lastError && (sinceOk == null || sinceOk > 12);
    if (conn) conn.className = "conn " + (offline ? "bad" : "ok");
    if (connText) connText.textContent = offline ? "offline" : "connected";
  }

  const dEl = $("diag");
  if (dEl) {
    if (diag.busyText) {
      // While an action runs, the bottom line says exactly what we're waiting
      // for (+ a Bluetooth hint when it drags) — no layout shift.
      const elapsed = Math.round(now - diag.busyStart);
      const slow = diag.busyBle && elapsed >= 6 ? " — waiting on Bluetooth (~20s on a weak signal)" : "";
      dEl.textContent = `${diag.busyText} ${elapsed}s${slow}`;
      dEl.className = "diag busy";
    } else {
      const parts = [];
      if (diag.lastOk) parts.push(`updated ${fmtAge(now - diag.lastOk)}`);
      if (current && current.reading_age != null) parts.push(`sensor ${fmtAge(current.reading_age)}`);
      if (current && current.rssi != null) parts.push(`signal ${current.rssi}dBm`);
      if (diag.lastError) parts.push(`last error: ${diag.lastError}`);
      dEl.textContent = parts.join(" · ");
      dEl.className = "diag" + (diag.lastError ? " err" : "");
    }
  }
}

// Per-second tick: refreshes the auto-off countdown and the compressor-lock
// countdown without waiting for the next poll.
function tick() {
  renderDiag();
  if (!current) return;

  // Auto-off timer countdown
  if (current.off_timer_at) {
    const remaining = Math.round(current.off_timer_at - Date.now() / 1000);
    const el = $("timer-remaining");
    if (el) el.textContent = remaining <= 0 ? "turning off…" : `in ${fmtDuration(remaining)}`;
    const badge = $("badge-timer");
    if (badge) badge.textContent = remaining <= 0 ? "⏱ off" : `⏱ ${fmtDuration(remaining)}`;
    if (remaining <= 0 && !current._firedReload) { current._firedReload = true; load(); }
  }

  // Drive the single toggle button. While its own action is in flight, show an
  // in-button spinner (full-width button -> no layout shift). Otherwise show the
  // label + the compressor-protection lock countdown.
  const btn = $("toggle");
  if (busy && diag.busyTarget === "toggle") {
    const elapsed = Math.round(Date.now() / 1000 - diag.busyStart);
    btn.classList.add("loading");
    btn.innerHTML = `<span class="ispin"></span>${diag.busyShort || "Working…"} ${elapsed}s`;
  } else {
    btn.classList.remove("loading");
    if (!busy) {
      const isOn = current.believed === true;
      const remaining = current.lockUntil ? Math.max(0, Math.round(current.lockUntil - Date.now() / 1000)) : 0;
      // Only turning ON is locked; turning OFF is always allowed.
      const locked = !isOn && remaining > 0;
      btn.disabled = locked;
      btn.textContent = isOn ? "Turn Off" : (locked ? `On in ${fmtMMSS(remaining)}` : "Turn On");
      const note = $("lock-note");
      if (note) note.textContent = locked ? `Compressor protection — on available in ${fmtMMSS(remaining)}` : "";
      if (current.lockUntil && remaining === 0 && !current._lockReload) { current._lockReload = true; load(); }
    }
  }
}

function msg(text, kind = "") {
  const el = $("msg");
  el.textContent = text;
  el.className = "msg " + kind;
  if (text) setTimeout(() => { if (el.textContent === text) { el.textContent = ""; el.className = "msg"; } }, 4000);
}

let busy = false;

function setBusy(on) {
  busy = on;
  document.body.classList.toggle("busy", on);
  document.querySelectorAll("button, input").forEach((el) => { el.disabled = on; });
}

// Run an action with a busy guard: a tap is ignored while another is in flight,
// the controls are disabled, and a pending label is shown — so the slow Bot
// press can't be double-tapped. `opts.handle(s)` lets a caller inspect the
// response (e.g. a blocked compressor-protection result).
// Force the UI back to an idle, interactive state from anywhere. Safe to call
// repeatedly; used by the finally block, the watchdog, and on tab re-focus.
function forceIdle(errorText) {
  diag.busyText = null;
  diag.busyShort = null;
  diag.busyTarget = null;
  diag.busyBle = false;
  busy = false;
  document.body.classList.remove("busy");
  document.querySelectorAll("button, input").forEach((el) => { el.disabled = false; });
  if (errorText) { diag.lastError = errorText; msg(errorText, "error"); }
  tick();
}

async function act(fn, opts = {}) {
  if (busy) return;
  setBusy(true);
  diag.busyText = opts.pending || "Working…";
  diag.busyShort = opts.short || null;   // shown inside the triggering button
  diag.busyTarget = opts.target || null; // which button hosts the in-button spinner
  diag.busyBle = !!opts.ble; // a Bluetooth op (slow); enables the "waiting on Bluetooth" hint
  diag.busyStart = Date.now() / 1000;
  renderDiag();
  // Watchdog: guarantee the UI can't stay stuck even if the request never
  // settles (e.g. the OS kills it while the screen is locked).
  const watchdog = setTimeout(() => {
    if (busy) forceIdle("Timed out waiting for the Pi. Tap to try again.");
  }, API_TIMEOUT_MS + 3000);
  try {
    const s = await fn();
    diag.lastOk = Date.now() / 1000;
    diag.lastError = null;
    render(s);
    if (opts.handle) opts.handle(s);
    else if (opts.ok) msg(opts.ok, "ok");
  } catch (e) {
    diag.lastError = e.message;
    msg(e.message, "error");
  } finally {
    clearTimeout(watchdog);
    diag.busyText = null;
    diag.busyShort = null;
    diag.busyTarget = null;
    diag.busyBle = false;
    setBusy(false);
    tick(); // re-apply the lock-button state that setBusy cleared
    renderDiag();
  }
}

async function load() {
  if (busy) return; // never fight an in-flight action / overwrite its pending UI
  try {
    render(await api("/api/status"));
    diag.lastOk = Date.now() / 1000;
    diag.lastError = null;
  } catch (e) {
    diag.lastError = e.message;
  }
  renderDiag();
}

// --- wire up controls ---
on("refresh", "click", async () => {
  const btn = $("refresh");
  btn.classList.add("spin");
  await act(() => api("/api/refresh", "POST"),
    { ok: "Sensor updated", pending: "Reading the temperature sensor…", ble: true });
  btn.classList.remove("spin");
});

on("t-up", "click", () =>
  act(() => api("/api/target", "POST", { value: (current.target || 0) + TARGET_STEP }), { pending: "Updating target temperature…" }));
on("t-down", "click", () =>
  act(() => api("/api/target", "POST", { value: (current.target || 0) - TARGET_STEP }), { pending: "Updating target temperature…" }));

on("pause", "click", () =>
  act(() => api("/api/pause", "POST", { paused: !current.paused }),
    { pending: current.paused ? "Resuming automatic control…" : "Pausing automatic control…" }));

on("mode", "click", () =>
  act(() => api("/api/mode", "POST", { action: current.action === "cool" ? "heat" : "cool" }), { pending: "Switching heat/cool mode…" }));

function outputAction(on) {
  act(() => api("/api/output", "POST", { on, force: true }), {
    ble: true,
    target: "toggle",
    short: on ? "Turning on…" : "Turning off…",
    pending: on ? "Turning thermostat ON — pressing the button…" : "Turning thermostat OFF — pressing the button…",
    handle: (s) => {
      const r = s.action_result || {};
      if (r.blocked) msg(`Compressor protection — wait ${fmtMMSS(r.retry_after_s)} before turning on.`, "error");
      else if (r.changed) msg(on ? "Turned on." : "Turned off.", "ok");
      else if (r.dry_run) msg("Dry-run is on — forced the press.", "ok");
      else msg(`Already ${on ? "on" : "off"}.`, "ok");
    },
  });
}
// One button toggles to the opposite of the current believed state.
on("toggle", "click", () => outputAction(!current.believed));

on("state-on", "click", () =>
  act(() => api("/api/state", "POST", { on: true }), { ok: "Marked ON", pending: "Saving believed state…" }));
on("state-off", "click", () =>
  act(() => api("/api/state", "POST", { on: false }), { ok: "Marked OFF", pending: "Saving believed state…" }));

// Auto-off timer
document.querySelectorAll(".chip[data-min]").forEach((btn) =>
  btn.addEventListener("click", () =>
    act(() => api("/api/timer", "POST", { minutes: Number(btn.dataset.min) }),
      { ok: `Auto-off in ${btn.textContent}`, pending: "Setting auto-off timer…" })));
on("timer-at-set", "click", () => {
  const t = $("timer-time").value; // "HH:MM"
  if (!t) { msg("Pick a time first.", "error"); return; }
  act(() => api("/api/timer", "POST", { at: t }), { ok: `Auto-off at ${t}`, pending: "Setting auto-off timer…" });
});
on("timer-cancel", "click", () =>
  act(() => api("/api/timer", "POST", { clear: true }), { ok: "Timer cancelled", pending: "Cancelling auto-off timer…" }));

// --- service worker (best-effort; only works over https/localhost) ---
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// Recover when the tab returns to the foreground after being backgrounded
// (browsers throttle/pause timers for hidden tabs, which can strand an
// in-flight request). On re-focus, clear any long-stale busy state and re-sync.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (busy && Date.now() / 1000 - diag.busyStart > API_TIMEOUT_MS / 1000) {
    forceIdle("Connection was interrupted. Re-syncing…");
  }
  load();
});

load();
setInterval(load, POLL_MS);
setInterval(tick, 1000); // smooth per-second countdowns between polls
