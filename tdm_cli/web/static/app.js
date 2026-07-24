/* TDMConsole WebUI client — vanilla JS, no build step.
   Opens a WebSocket, renders MinerState snapshots, sends /commands. */
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

let ws = null;
let seenLog = 0;          // highest log seq rendered (dedup)
let state = null;         // latest snapshot
let modalKind = null;     // "login" | "games" | "settings" | null
let campaignsExpanded = false;
let campaignTransitioning = false;
let campaignCollapseTimer = null;
const RUNTIME_COLLAPSED_KEY = "tdm-runtime-collapsed";
let runtimeCollapsed = localStorage.getItem(RUNTIME_COLLAPSED_KEY) !== "false";
let engineUpdateRequested = false;
let engineUpToDate = false;
let engineUpToDateTimer = null;
let toastTimer = null;

/* ---- theming: accent colour is user-overridable, persisted locally ------ */
const THEME_KEY = "tdm-accent";
const PRESET_THEMES = [
  ["Twitch",  "#9146FF"],
  ["Ocean",   "#1E9BF0"],
  ["Emerald", "#12C27A"],
  ["Sunset",  "#FF7A1A"],
  ["Rose",    "#F0407F"],
  ["Gold",    "#E0A73C"],
];
function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex([r, g, b]) {
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}
function applyAccent(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return;
  const root = document.documentElement.style;
  root.setProperty("--accent-r", rgb[0]);
  root.setProperty("--accent-g", rgb[1]);
  root.setProperty("--accent-b", rgb[2]);
}
function getAccent() {
  return localStorage.getItem(THEME_KEY) || "#9146FF";
}
function setAccent(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return;
  const norm = rgbToHex(rgb);
  localStorage.setItem(THEME_KEY, norm);
  applyAccent(norm);
}
// apply saved theme immediately (before first paint of dynamic content)
applyAccent(getAccent());

/* ---- light / dark mode, user-overridable, persisted locally ------------- */
const MODE_KEY = "tdm-mode";
// Returns "light" | "dark". Default follows the OS preference, unless the
// user explicitly chose one (saved in localStorage).
function getMode() {
  const saved = localStorage.getItem(MODE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light" : "dark";
}
function applyMode(mode) {
  document.documentElement.setAttribute("data-theme", mode === "light" ? "light" : "dark");
}
function setMode(mode) {
  const m = mode === "light" ? "light" : "dark";
  localStorage.setItem(MODE_KEY, m);
  applyMode(m);
}
// apply saved / OS-preferred mode immediately
applyMode(getMode());
// follow OS changes only while the user hasn't pinned a choice
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", (e) => {
    if (!localStorage.getItem(MODE_KEY)) applyMode(e.matches ? "light" : "dark");
  });
}

/* ---- i18n: strings fetched from the server, language saved locally ------ */
const LANG_KEY = "tdm-lang";
let STR = {};                 // current { key: text } catalogue
let LANGS = [];               // [{code, name}] from the server
let META = { app: "?", engine: "?", engineCommit: "?", repo: "", authEnabled: false };

function t(key, fallback) {
  return (STR && STR[key]) || fallback || key;
}
function getLang() {
  return localStorage.getItem(LANG_KEY) || "";   // "" = auto-detect
}
// Pick the best server language for the browser, unless the user chose one.
function pickLang() {
  const saved = getLang();
  if (saved) return saved;
  const codes = LANGS.map((l) => l.code);
  // upstream file stems are native names (简体中文, 日本語, …) and english_name
  // is exposed as .name; match navigator languages against a small alias table.
  const nav = (navigator.languages || [navigator.language || "en"]).map((s) => s.toLowerCase());
  const ALIAS = {
    "zh-cn": "简体中文", "zh-sg": "简体中文", "zh-hans": "简体中文", "zh": "简体中文",
    "zh-tw": "繁體中文", "zh-hk": "繁體中文", "zh-hant": "繁體中文",
    "ja": "日本語", "de": "Deutsch", "fr": "Français", "es": "Español",
    "it": "Italiano", "pt": "Português", "pt-br": "Português (Brasil)",
    "ru": "Русский", "tr": "Türkçe", "pl": "Polski", "nl": "Nederlandse",
    "cs": "Čeština", "da": "Dansk", "hu": "Magyar", "no": "Norsk",
    "ro": "Română", "uk": "Українська", "ar": "العربية", "id": "Indonesian",
  };
  for (const n of nav) {
    if (codes.includes(ALIAS[n])) return ALIAS[n];
    const base = n.split("-")[0];
    if (codes.includes(ALIAS[base])) return ALIAS[base];
  }
  return "English";
}
async function loadLang(code) {
  try {
    const r = await fetch(`/i18n/${encodeURIComponent(code)}`);
    if (r.ok) STR = await r.json();
  } catch { /* keep whatever we had */ }
}
async function setLang(code) {
  localStorage.setItem(LANG_KEY, code);
  await loadLang(code);
  applyStaticI18n();
  if (state) render(state);           // re-render dynamic content in new language
}
// Translate the static markup: any [data-i18n] gets textContent, any
// [data-i18n-attr="attr:key,attr:key"] gets those attributes set.
function applyStaticI18n() {
  document.documentElement.lang = (getLang() || pickLang());
  document.querySelectorAll("[data-i18n]").forEach((n) => {
    n.textContent = t(n.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-attr]").forEach((n) => {
    n.getAttribute("data-i18n-attr").split(",").forEach((pair) => {
      const [attr, key] = pair.split(":");
      if (attr && key) n.setAttribute(attr.trim(), t(key.trim()));
    });
  });
  document.title = "TDMConsole · " + t("app.tagline");
  // repo / version footer under the Settings button
  const link = $("repo-link");
  if (link && META.repo) link.href = META.repo;
  const ver = $("repo-ver");
  if (ver) {
    // Two independent versions: our app, and the bundled mining engine @ commit.
    const parts = [];
    if (META.app) parts.push(`${t("footer.version")} ${META.app}`);
    if (META.engine) {
      const commit = META.engineCommit && META.engineCommit !== "unknown"
        ? ` @ ${META.engineCommit}` : "";
      parts.push(`${t("footer.engine")} ${META.engine}${commit}`);
    }
    ver.textContent = parts.join(" · ");
  }
  $("btn-logout").hidden = !META.authEnabled;
  updateCampaignExpandButton();
  updateRuntimeToggle();
}
async function initMeta() {
  try {
    const r = await fetch("/meta");
    if (r.ok) {
      const m = await r.json();
      META = {
        app: m.app,
        engine: m.engine,
        engineCommit: m.engineCommit,
        repo: m.repo,
        authEnabled: Boolean(m.authEnabled),
      };
      LANGS = m.languages || [];
    }
  } catch { /* offline meta — non-fatal */ }
}

function renderRuntime(runtime) {
  if (!runtime) return;
  $("runtime-uptime").textContent = runtime.uptime || "—";
  $("runtime-started").textContent = runtime.startedAt
    ? new Date(runtime.startedAt).toLocaleString() : "—";
  $("runtime-version").textContent = runtime.version || "—";
  const engine = runtime.engine || {};
  $("runtime-engine").textContent = engine.version
    ? `${engine.version}${engine.commit && engine.commit !== "unknown" ? ` @ ${engine.commit}` : ""}`
    : "—";
  $("runtime-cpu").textContent = runtime.cpu && runtime.cpu.usage || "—";
  $("runtime-memory").textContent = runtime.memory && runtime.memory.usage || "—";
  $("runtime-cache").textContent = runtime.cache && runtime.cache.size || "—";
  $("runtime-summary").textContent = runtime.cpu && runtime.memory
    ? `${runtime.cpu.usage} · ${runtime.memory.usage}` : "—";
}

function updateRuntimeToggle() {
  const window = $("runtime-window");
  const body = $("runtime-body");
  const button = $("btn-runtime-toggle");
  const label = runtimeCollapsed
    ? t("runtime.expand", "Expand runtime")
    : t("runtime.collapse", "Collapse runtime");
  window.classList.toggle("is-collapsed", runtimeCollapsed);
  body.setAttribute("aria-hidden", String(runtimeCollapsed));
  button.setAttribute("aria-expanded", String(!runtimeCollapsed));
  button.setAttribute("aria-label", label);
  button.title = label;
  const icon = button.querySelector("use");
  if (icon) icon.setAttribute("href", runtimeCollapsed ? "#i-chevron-up" : "#i-chevron-down");
}

function toggleRuntime() {
  runtimeCollapsed = !runtimeCollapsed;
  localStorage.setItem(RUNTIME_COLLAPSED_KEY, String(runtimeCollapsed));
  updateRuntimeToggle();
}

function showToast(message) {
  const toast = $("toast");
  if (!toast) return;
  if (toastTimer) window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 4000);
}

function renderUpdateButton(s = state || {}) {
  const button = $("btn-update");
  const active = engineUpdateRequested || Boolean(s.engineUpdating);
  const upToDate = engineUpToDate && !active;
  const label = button.querySelector("span");
  button.disabled = active;
  button.classList.toggle("is-loading", active);
  button.classList.toggle("is-up-to-date", upToDate);
  button.setAttribute("aria-busy", String(active));
  if (label) {
    label.textContent = active
      ? t("btn.updating", "Updating engine...")
      : (upToDate
        ? t("btn.up_to_date", "Engine is up to date")
        : t("btn.update", "Update engine"));
  }
}

function showEngineUpToDate() {
  engineUpToDate = true;
  if (engineUpToDateTimer) window.clearTimeout(engineUpToDateTimer);
  renderUpdateButton();
  engineUpToDateTimer = window.setTimeout(() => {
    engineUpToDate = false;
    renderUpdateButton();
  }, 3000);
}

async function refreshRuntime() {
  try {
    const response = await fetch("/runtime", { credentials: "same-origin" });
    if (response.status === 401) {
      redirectToLogin();
      return;
    }
    if (response.ok) renderRuntime(await response.json());
  } catch { /* transient failures do not interrupt mining controls */ }
}

/* ---- WebSocket with auto-reconnect ------------------------------------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = (event) => {
    if (event.code === 4401) {
      redirectToLogin();
      return;
    }
    setConn(false);
    checkSessionThenReconnect();
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "state") { state = msg.data; render(state); }
    else if (msg.type === "log") { appendLog(msg.lines); }
  };
}

function redirectToLogin() {
  location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
}

async function checkSessionThenReconnect() {
  try {
    const response = await fetch("/session", { cache: "no-store" });
    if (response.status === 401) {
      redirectToLogin();
      return;
    }
  } catch { /* server may still be restarting */ }
  setTimeout(connect, 1500);
}

function setConn(ok) {
  if (!ok) {
    $("status-text").textContent = "disconnected — reconnecting…";
    $("led").dataset.state = "offline";
  }
}

function send(text) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "command", text }));
  }
}

/* ---- rendering ---------------------------------------------------------- */
function render(s) {
  // status + LED
  $("status-text").textContent = s.status || t("status.starting", "starting…");
  const led = $("led");
  if (s.login && s.login.userId == null && s.login.available) led.dataset.state = "error";
  else if (s.watching && s.watching.channel) led.dataset.state = "watching";
  else led.dataset.state = "idle";

  const updateResult = s.engineUpdateResult || "";
  if (engineUpdateRequested && !s.engineUpdating && updateResult) {
    engineUpdateRequested = false;
    if (updateResult === "up_to_date") {
      showToast(t("toast.engine_up_to_date", "The engine is already up to date."));
      showEngineUpToDate();
    }
  }
  renderUpdateButton(s);

  // user plate
  const login = s.login || {};
  $("user-plate").textContent =
    login.userId != null ? `user ${login.userId}`
      : (login.available ? t("login.logged_out", "not logged in") : "—");

  // login strip
  const showLogin = login.available && login.userId == null;
  $("login-strip").hidden = !showLogin;

  // login control: swap to a ✓ Online badge once authorized
  const online = login.userId != null;
  const btnLogin = $("btn-login");
  const lang = getLang() || pickLang();
  if (btnLogin._online !== online || btnLogin._lang !== lang) {
    btnLogin._online = online;
    btnLogin._lang = lang;
    const lbl = online ? t("btn.online", "Online") : t("btn.login", "Log in");
    const sym = online ? "i-online" : "i-login";
    btnLogin.innerHTML =
      `<svg class="ico" aria-hidden="true"><use href="#${sym}"></use></svg><span>${lbl}</span>`;
    btnLogin.classList.toggle("btn-online", online);
  }
  // enabled only when there's something to do (a pending login)
  btnLogin.disabled = !online && !login.available;

  // now mining
  $("cur-game").textContent = s.watching.game || s.campaign.game || "—";
  $("cur-channel").textContent = s.watching.channel || "—";

  // drop gauge
  $("drop-rewards").textContent = s.drop.rewards || t("mining.no_drop", "No active drop");
  $("drop-remaining").textContent = s.drop.rewards ? s.drop.remaining : "—";
  meter("drop", s.drop.progress);
  // campaign gauge
  $("camp-name").textContent = s.campaign.name || t("progress.campaign", "Campaign");
  $("camp-remaining").textContent = s.campaign.name ? s.campaign.remaining : "—";
  meter("camp", s.campaign.progress);
  $("camp-drops").textContent = s.campaign.total
    ? t("mining.drops_claimed", "{claimed} / {total} drops claimed")
        .replace("{claimed}", s.campaign.claimed).replace("{total}", s.campaign.total)
    : "";

  renderWs(s.websockets);
  renderChannels(s.channels);
  renderCampaigns(s.campaigns);

  // live-refresh an open modal that mirrors state
  if (modalKind === "login") refreshLoginModal();
  else if (modalKind === "games") refreshGamesModal();
  // auto-open the login modal when the user asked (login.prompt) and it isn't up
  if (login.prompt && modalKind !== "login") openLogin();
  if (!login.prompt && modalKind === "login") closeModal();
}

function meter(prefix, frac) {
  const pct = Math.round(Math.max(0, Math.min(1, frac || 0)) * 100);
  $(`${prefix}-fill`).style.width = pct + "%";
  $(`${prefix}-pct`).textContent = pct + "%";
  $(`${prefix}-meter`).setAttribute("aria-valuenow", String(pct));
}

function renderWs(list) {
  const strip = $("ws-strip");
  strip.replaceChildren();
  (list || []).forEach((w) => {
    const dot = el("span", "ws-dot");
    const led = el("span", "led");
    const connected = /connect|open|ONLINE|online/i.test(w.status) && !/dis/i.test(w.status);
    led.dataset.state = connected ? "watching" : "idle";
    dot.append(led, el("span", null, `#${w.idx + 1} ${w.status} · ${w.topics}t`));
    strip.append(dot);
  });
}

function renderChannels(chs) {
  const body = $("channels-body");
  body.replaceChildren();
  if (!chs || !chs.length) {
    const tr = el("tr");
    tr.append(Object.assign(el("td", "empty", t("channels.empty", "No channels yet…")), { colSpan: 6 }));
    body.append(tr);
    return;
  }
  chs.forEach((c) => {
    const tr = el("tr");
    if (c.watching) tr.classList.add("watching");
    else if (c.locked) tr.classList.add("locked");
    // status column: ▶ = now watching, 🔒 = locked to this channel
    const flag = el("td", "ch-flag");
    flag.textContent = c.watching ? "▶" : c.locked ? "🔒" : "";
    flag.title = c.watching ? t("flag.watching", "Now watching") : c.locked ? t("flag.locked", "Locked to this channel") : "";
    tr.append(flag);
    tr.append(el("td", null, c.name));
    tr.append(el("td", null, c.game || "—"));
    const st = el("td");
    const pill = el("span",
      "pill " + (c.online ? "online" : c.pending ? "pending" : "offline"),
      c.online ? "online" : c.pending ? "pending" : "offline");
    st.append(pill);
    tr.append(st);
    tr.append(el("td", "num", c.viewers == null ? "—" : String(c.viewers)));
    // action column: switch-to (arrows) when not locked, unlock (✕) when locked
    const actCell = el("td", "ch-act");
    if (c.locked) {
      const unlock = el("button", "row-btn unlock");
      unlock.innerHTML = "✕";
      unlock.title = t("action.unlock", "Unlock — resume automatic channel selection");
      unlock.setAttribute("aria-label", t("action.unlock"));
      unlock.onclick = (e) => { e.stopPropagation(); send("/unpin"); };
      actCell.append(unlock);
    } else {
      const sw = el("button", "row-btn switch");
      sw.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-switch"/></svg>';
      sw.title = c.online
        ? t("action.switch", "Switch to & lock this channel")
        : t("action.switch_offline", "Channel is offline — can't switch right now");
      sw.setAttribute("aria-label", t("action.switch"));
      sw.disabled = !c.online;
      sw.onclick = (e) => { e.stopPropagation(); send(`/pin ${c.name}`); };
      actCell.append(sw);
    }
    tr.append(actCell);
    body.append(tr);
  });
}

function renderCampaigns(cps) {
  const box = $("campaigns-cards");
  box.replaceChildren();
  if (!cps || !cps.length) { box.append(el("p", "empty", t("campaigns.empty", "Inventory empty…"))); return; }
  cps.forEach((c) => {
    const card = el("div", "card");
    const top = el("div", "card-top");
    top.append(el("span", "card-game", c.game));
    const tagCls = c.active ? "active" : c.upcoming ? "upcoming" : "expired";
    top.append(el("span", "tag " + tagCls, t("tag." + tagCls, tagCls)));
    card.append(top);
    card.append(el("div", "card-name", `${c.name} · ${c.claimed}/${c.total}`));
    const m = el("div", "card-meter");
    const fill = el("div");
    fill.style.width = Math.round((c.progress || 0) * 100) + "%";
    m.append(fill);
    card.append(m);
    if (campaignsExpanded) {
      const drops = el("div", "campaign-drop-list");
      if (!c.drops || !c.drops.length) {
        drops.append(el("p", "campaign-drop-empty", t("campaigns.rewards_empty", "Reward details unavailable")));
      } else {
        c.drops.forEach((drop) => {
          const row = el("div", "campaign-drop");
          const head = el("div", "campaign-drop-head");
          const rewards = el("div", "campaign-drop-rewards");
          if (drop.rewards && drop.rewards.length) {
            drop.rewards.forEach((reward) => {
              const name = typeof reward === "string" ? reward : reward.name;
              const item = el("div", "campaign-reward");
              if (typeof reward !== "string" && reward.image) {
                const image = document.createElement("img");
                image.className = "campaign-reward-image";
                image.src = reward.image;
                image.alt = "";
                image.loading = "lazy";
                image.decoding = "async";
                image.onerror = () => image.remove();
                item.append(image);
              }
              item.append(el("span", "campaign-reward-name", name));
              rewards.append(item);
            });
          } else {
            rewards.append(el("span", "campaign-drop-empty", t("campaigns.rewards_empty", "Reward details unavailable")));
          }
          head.append(rewards);
          const current = Number(drop.currentMinutes) || 0;
          const required = Number(drop.requiredMinutes) || 0;
          const status = drop.claimed
            ? t("campaigns.drop_claimed", "Claimed")
            : t("campaigns.drop_progress", "{current} / {required} min")
              .replace("{current}", current).replace("{required}", required);
          head.append(el("span", "campaign-drop-status" + (drop.claimed ? " claimed" : ""), status));
          row.append(head);
          const meter = el("div", "campaign-drop-meter");
          const meterFill = el("div");
          const progress = Math.max(0, Math.min(1, Number(drop.progress) || 0));
          meterFill.style.width = Math.round(progress * 100) + "%";
          meter.append(meterFill);
          row.append(meter);
          drops.append(row);
        });
      }
      card.append(drops);
    }
    box.append(card);
  });
}

function updateCampaignExpandButton() {
  const btn = $("btn-campaigns-expand");
  if (!btn) return;
  const expanded = campaignsExpanded;
  const label = expanded
    ? t("campaigns.collapse", "Shrink campaigns")
    : t("campaigns.expand", "Expand campaigns");
  const icon = btn.querySelector("use");
  if (icon) icon.setAttribute("href", expanded ? "#i-collapse-campaigns" : "#i-expand-campaigns");
  btn.setAttribute("aria-label", label);
  btn.title = label;
  btn.disabled = campaignTransitioning;
}

function expandCampaigns() {
  if (campaignsExpanded || campaignTransitioning) return;
  const panel = $("campaigns-panel");
  if (!panel) return;
  if (campaignCollapseTimer) {
    window.clearTimeout(campaignCollapseTimer);
    campaignCollapseTimer = null;
  }
  campaignsExpanded = true;
  campaignTransitioning = true;
  document.body.classList.add("campaigns-expanded");
  panel.classList.add("is-expanded");
  renderCampaigns(state ? state.campaigns : []);
  updateCampaignExpandButton();
  window.requestAnimationFrame(() => {
    panel.classList.add("is-expanded-visible");
    campaignTransitioning = false;
    updateCampaignExpandButton();
  });
}

function collapseCampaigns() {
  if (!campaignsExpanded || campaignTransitioning) return;
  const panel = $("campaigns-panel");
  if (!panel) return;
  campaignTransitioning = true;
  panel.classList.remove("is-expanded-visible");
  updateCampaignExpandButton();
  campaignCollapseTimer = window.setTimeout(() => {
    panel.classList.remove("is-expanded");
    document.body.classList.remove("campaigns-expanded");
    campaignsExpanded = false;
    campaignTransitioning = false;
    campaignCollapseTimer = null;
    renderCampaigns(state ? state.campaigns : []);
    updateCampaignExpandButton();
  }, 200);
}

function appendLog(lines) {
  const screen = $("log");
  const atBottom = screen.scrollHeight - screen.scrollTop - screen.clientHeight < 40;
  (lines || []).forEach((e) => {
    if (e.seq <= seenLog) return;
    seenLog = e.seq;
    const line = el("div", "line");
    line.append(el("span", "stamp", e.stamp));
    const span = el("span", e.style ? "s-" + e.style : null, e.text);
    line.append(span);
    screen.append(line);
  });
  while (screen.childElementCount > 500) screen.removeChild(screen.firstChild);
  if (atBottom) screen.scrollTop = screen.scrollHeight;
}

/* ---- modals ------------------------------------------------------------- */
function sendAction(name, extra) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(Object.assign({ type: "action", name }, extra || {})));
  }
}

let lastFocus = null;
function openModal(kind, node) {
  modalKind = kind;
  lastFocus = document.activeElement;
  const root = $("modal-root");
  const wrap = el("div", "modal");
  wrap.setAttribute("role", "dialog");
  wrap.setAttribute("aria-modal", "true");
  wrap.append(node);
  root.replaceChildren(wrap);
  root.classList.add("open");
  root.onclick = (e) => { if (e.target === root) closeModal(); };
  // Keyboard-first: contain Tab within the dialog (focus trap).
  wrap.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const items = wrap.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  const focusable = wrap.querySelector("input, select, button");
  if (focusable) focusable.focus();
}
function closeModal() {
  const wasLogin = modalKind === "login";
  modalKind = null;
  const root = $("modal-root");
  root.classList.remove("open");
  root.replaceChildren();
  if (lastFocus && lastFocus.focus) lastFocus.focus();
  // Tell the server to stop asking to show the login prompt (poll continues).
  if (wasLogin) sendAction("login-hide");
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (campaignsExpanded) { e.preventDefault(); collapseCampaigns(); }
  else if (modalKind) { e.preventDefault(); closeModal(); }
});

function heading(text) { return el("h3", null, text); }
function actions(...btns) { const a = el("div", "modal-actions"); a.append(...btns); return a; }
function primaryBtn(text, fn) { const b = el("button", "btn btn-primary", text); b.onclick = fn; return b; }
function ghostBtn(text, fn) { const b = el("button", "btn", text); b.onclick = fn; return b; }

/* login modal */
function openLogin() {
  const node = el("div");
  node.append(heading(t("login.title", "Twitch login")));
  node.append(el("p", null, t("login.instructions", "Open this page and enter the code:")));
  const url = el("p", "login-url");
  url.id = "m-login-url";
  node.append(url);
  const code = el("div", "code-ticket");
  code.id = "m-login-code";
  node.append(code);
  node.append(el("p", null, t("login.waiting", "Waiting for authorization…")));
  node.append(actions(
    ghostBtn(t("login.open_url", "Open page"), () => state && state.login && window.open(state.login.url, "_blank")),
    primaryBtn(t("settings.done", "Close"), closeModal),
  ));
  openModal("login", node);
  refreshLoginModal();
}
function refreshLoginModal() {
  if (modalKind !== "login" || !state) return;
  const u = $("m-login-url"), c = $("m-login-code");
  if (u) u.textContent = state.login.url || "—";
  if (c) c.textContent = state.login.code || "—";
}

/* games modal */
function openGames() {
  const node = el("div");
  node.append(heading(t("games.title", "Games — priority & exclusions")));

  const prio = el("div");
  prio.append(el("label", null, t("games.priority", "Priority (top = highest)")));
  const plist = el("div", "list-editor");
  plist.id = "games-priority-list";
  prio.append(plist);
  // The add-row lives OUTSIDE the refreshed list so typing/focus survive the
  // 0.5s state snapshots that re-render the lists.
  const add = el("div", "add-row");
  const inp = el("input");
  inp.placeholder = t("games.add_priority", "Add a game to priority…");
  inp.setAttribute("aria-label", t("games.add_priority", "Add a game to priority…"));
  const doAdd = () => {
    const v = inp.value.trim();
    if (v) { send(`/priority add ${v}`); inp.value = ""; inp.focus(); }
  };
  inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); doAdd(); } };
  add.append(inp, primaryBtn(t("games.add", "Add"), doAdd));
  prio.append(add);
  node.append(prio);

  const exc = el("div");
  exc.style.marginTop = "16px";
  exc.append(el("label", null, t("games.excluded", "Excluded")));
  const elist = el("div", "list-editor");
  elist.id = "games-exclude-list";
  exc.append(elist);
  node.append(exc);

  node.append(actions(primaryBtn(t("settings.done", "Done"), closeModal)));
  openModal("games", node);
  gamesSig = "";        // force a first render
  refreshGamesModal();
}

let gamesSig = "";
function refreshGamesModal() {
  const s = state && state.settings;
  const plist = document.getElementById("games-priority-list");
  const elist = document.getElementById("games-exclude-list");
  if (!s || !plist || !elist) return;
  // Only rebuild when the lists actually change — avoids replacing the
  // interactive buttons every 0.5s snapshot (which could drop a click).
  const sig = JSON.stringify([s.priority, s.exclude]);
  if (sig === gamesSig) return;
  gamesSig = sig;
  plist.replaceChildren();
  s.priority.forEach((g, i) => {
    const row = el("div", "list-row");
    row.append(el("span", "grow", `${i + 1}. ${g}`));
    row.append(ghostBtn("↑", () => send(`/priority up ${g}`)));
    row.append(ghostBtn("↓", () => send(`/priority down ${g}`)));
    row.append(ghostBtn("✕", () => send(`/priority remove ${g}`)));
    row.append(ghostBtn(t("games.exclude_btn", "exclude"), () => send(`/exclude add ${g}`)));
    plist.append(row);
  });
  if (!s.priority.length) plist.append(el("p", "empty", t("games.none_priority", "No priority games")));
  elist.replaceChildren();
  s.exclude.forEach((g) => {
    const row = el("div", "list-row");
    row.append(el("span", "grow", g));
    row.append(ghostBtn("✕", () => send(`/exclude remove ${g}`)));
    elist.append(row);
  });
  if (!s.exclude.length) elist.append(el("p", "empty", t("games.none_excluded", "No excluded games")));
}

/* settings modal */
function openSettings() {
  const s = state.settings;
  const node = el("div");
  node.append(heading(t("settings.title", "Settings")));

  const proxyField = el("div", "field");
  proxyField.append(el("label", null, t("settings.proxy", "Proxy URL")));
  const proxy = el("input");
  proxy.value = s.proxy || "";
  proxy.placeholder = "http://127.0.0.1:7890 (blank = none)";
  proxyField.append(proxy);
  node.append(proxyField);

  const modeField = el("div", "field");
  modeField.append(el("label", null, t("settings.priority_mode", "Priority mode")));
  const modeSel = el("select");
  [["PRIORITY_ONLY", t("mode.priority_only", "Priority list only")],
   ["ENDING_SOONEST", t("mode.ending_soonest", "Ending soonest first")],
   ["LOW_AVBL_FIRST", t("mode.low_availability", "Low availability first")]]
    .forEach(([v, label]) => { const o = el("option", null, label); o.value = v; if (v === s.priorityMode) o.selected = true; modeSel.append(o); });
  modeField.append(modeSel);
  node.append(modeField);

  // ---- language picker (applies live, saved locally) ----
  const langField = el("div", "field");
  langField.append(el("label", null, t("settings.language", "Language")));
  const langSel = el("select");
  const curLang = getLang() || pickLang();
  LANGS.forEach((l) => {
    const o = el("option", null, l.name);
    o.value = l.code;
    if (l.code === curLang) o.selected = true;
    langSel.append(o);
  });
  langSel.onchange = () => { setLang(langSel.value); };
  langField.append(langSel);
  node.append(langField);

  // ---- theme accent picker (applies live, saved locally) ----
  const themeField = el("div", "field");
  themeField.append(el("label", null, t("settings.theme", "Theme colour")));
  const swatches = el("div", "swatches");
  const custom = el("input");
  custom.type = "color";
  custom.className = "swatch-custom";
  custom.setAttribute("aria-label", t("settings.theme_custom", "Custom theme colour"));
  const current = getAccent();
  custom.value = current;
  PRESET_THEMES.forEach(([name, hex]) => {
    const b = el("button", "swatch");
    b.type = "button";
    b.title = name;
    b.setAttribute("aria-label", `${name} theme`);
    b.style.setProperty("--sw", hex);
    if (hex.toLowerCase() === current.toLowerCase()) b.classList.add("active");
    b.onclick = () => {
      setAccent(hex);
      custom.value = hex;
      swatches.querySelectorAll(".swatch").forEach((s) => s.classList.remove("active"));
      b.classList.add("active");
    };
    swatches.append(b);
  });
  // live preview while dragging; commit persists it
  custom.oninput = () => {
    applyAccent(custom.value);
    swatches.querySelectorAll(".swatch").forEach((s) => s.classList.remove("active"));
  };
  custom.onchange = () => setAccent(custom.value);
  swatches.append(custom);
  themeField.append(swatches);
  node.append(themeField);

  // ---- light / dark mode toggle (applies live, saved locally) ----
  const modeThemeField = el("div", "field");
  modeThemeField.append(el("label", null, t("settings.appearance", "Appearance")));
  const seg = el("div", "seg");
  const curMode = getMode();
  [["light", t("settings.light", "Light")], ["dark", t("settings.dark", "Dark")]]
    .forEach(([v, label]) => {
      const b = el("button", "seg-btn", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(v === curMode));
      if (v === curMode) b.classList.add("active");
      b.onclick = () => {
        setMode(v);
        seg.querySelectorAll(".seg-btn").forEach((x) => {
          const on = x === b;
          x.classList.toggle("active", on);
          x.setAttribute("aria-pressed", String(on));
        });
      };
      seg.append(b);
    });
  modeThemeField.append(seg);
  node.append(modeThemeField);

  node.append(el("p", "login-url", t("settings.note")));

  node.append(actions(
    ghostBtn(t("settings.cancel", "Cancel"), closeModal),
    primaryBtn(t("settings.save", "Save"), () => {
      // Only send a command when the field actually changed, so opening
      // Settings (e.g. just to pick a theme) never clears the proxy or
      // triggers a needless restart.
      const p = proxy.value.trim();
      const origProxy = (s.proxy || "").trim();
      if (modeSel.value !== s.priorityMode) {
        send(`/priority-mode ${modeSel.value.toLowerCase()}`);
      }
      if (p !== origProxy) {
        send(`/proxy ${p || "clear"}`);
      }
      closeModal();
    }),
  ));
  openModal("settings", node);
}

/* ---- wire up controls --------------------------------------------------- */
$("btn-reload").onclick = () => send("/reload");
$("btn-update").onclick = () => {
  if (engineUpdateRequested) return;
  engineUpToDate = false;
  if (engineUpToDateTimer) window.clearTimeout(engineUpToDateTimer);
  engineUpdateRequested = true;
  renderUpdateButton();
  send("/update");
};
$("btn-login").onclick = () => send("/login");
$("login-cta").onclick = () => send("/login");
$("btn-campaigns-expand").onclick = () => {
  if (campaignsExpanded) collapseCampaigns();
  else expandCampaigns();
};
$("btn-games").onclick = () => { if (state) openGames(); };
$("btn-settings").onclick = () => { if (state) openSettings(); };
$("btn-logout").onclick = async () => {
  $("btn-logout").disabled = true;
  try {
    await fetch("/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    location.assign("/login");
  }
};

/* ---- startup: load languages + strings before first paint, then connect - */
async function start() {
  try {
    await initMeta();                 // /meta → languages, repo, version
    await loadLang(pickLang());       // /i18n/<lang> → current catalogue
    applyStaticI18n();                // translate the static shell
    await refreshRuntime();
    window.setInterval(refreshRuntime, 5000);
  } catch (e) {
    // i18n is best-effort; the UI still works in English if it fails.
  }
  connect();
}

$("btn-runtime-toggle").onclick = toggleRuntime;
start();
