"use strict";

const MODE_KEY = "tdm-mode";
const ACCENT_KEY = "tdm-accent";
const themeToggle = document.getElementById("theme-toggle");
const themeIcon = document.getElementById("theme-icon");
const form = document.getElementById("login-form");
const username = document.getElementById("username");
const password = document.getElementById("password");
const submit = document.getElementById("login-submit");
const error = document.getElementById("login-error");

function preferredMode() {
  const saved = localStorage.getItem(MODE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light" : "dark";
}

function applyMode(mode) {
  const current = mode === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", current);
  const target = current === "dark" ? "light" : "dark";
  themeIcon.textContent = target === "light" ? "☀" : "☾";
  themeToggle.setAttribute("aria-label", `Switch to ${target} mode`);
  themeToggle.title = `Switch to ${target} mode`;
}

function applyAccent() {
  const match = /^#?([0-9a-f]{6})$/i.exec(localStorage.getItem(ACCENT_KEY) || "#9146FF");
  if (!match) return;
  const value = parseInt(match[1], 16);
  const root = document.documentElement.style;
  root.setProperty("--accent-r", (value >> 16) & 255);
  root.setProperty("--accent-g", (value >> 8) & 255);
  root.setProperty("--accent-b", value & 255);
}

applyAccent();
applyMode(preferredMode());

themeToggle.addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark"
    ? "light" : "dark";
  localStorage.setItem(MODE_KEY, next);
  applyMode(next);
});

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", (event) => {
    if (!localStorage.getItem(MODE_KEY)) applyMode(event.matches ? "light" : "dark");
  });
}

function destination() {
  const value = new URLSearchParams(location.search).get("next") || "/";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  if (!form.reportValidity()) return;

  submit.disabled = true;
  try {
    const response = await fetch("/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    if (!response.ok) {
      error.textContent = response.status === 401
        ? "Invalid username or password."
        : "Unable to sign in. Try again.";
      error.hidden = false;
      password.select();
      return;
    }
    location.replace(destination());
  } catch {
    error.textContent = "Unable to reach TDMConsole.";
    error.hidden = false;
  } finally {
    submit.disabled = false;
  }
});
