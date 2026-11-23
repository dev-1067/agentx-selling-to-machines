/* SAUDA account page: sign in / create account, with inline validation. */
"use strict";
const $ = s => document.querySelector(s);
const EMAIL = /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/;
let mode = location.pathname.includes("signup") || location.pathname.includes("register") ? "up" : "in";
const next = new URLSearchParams(location.search).get("next");
const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/app";

function setMode(m, push) {
  mode = m;
  const up = m === "up";
  $("#tabs").classList.toggle("up", up);
  $("#tab-in").classList.toggle("on", !up); $("#tab-up").classList.toggle("on", up);
  $("#tab-in").setAttribute("aria-selected", !up); $("#tab-up").setAttribute("aria-selected", up);
  document.querySelectorAll(".only-up").forEach(el => el.style.display = up ? "" : "none");
  $("#title").textContent = up ? "Create your account" : "Welcome back";
  $("#subtitle").textContent = up ? "It takes less than a minute. No card needed." : "Sign in to open your control room.";
  $("#submit").textContent = up ? "Create account" : "Sign in";
  $("#password").autocomplete = up ? "new-password" : "current-password";
  $("#switch").innerHTML = up ? `Already have an account? <a href="/signin" data-m="in">Sign in</a>` : `New to SAUDA? <a href="/signup" data-m="up">Create an account</a>`;
  $("#switch a").onclick = e => { e.preventDefault(); setMode(e.target.dataset.m, true); };
  document.title = up ? "SAUDA · Create account" : "SAUDA · Sign in";
  clearErrors();
  if (push) history.replaceState(null, "", (up ? "/signup" : "/signin") + location.search);
}
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => setMode(b.dataset.mode, true));

function clearErrors() {
  document.querySelectorAll(".field").forEach(f => { f.classList.remove("bad"); f.querySelector(".err").textContent = ""; });
  $("#form-err").classList.remove("on");
}
function fieldError(id, msg) { const f = $("#f-" + id); f.classList.add("bad"); f.querySelector(".err").textContent = msg; }

function strength(p) {
  let s = 0;
  if (p.length >= 8) s++; if (p.length >= 12) s++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s++; if (/\d/.test(p)) s++; if (/[^A-Za-z0-9]/.test(p)) s++;
  return s;
}
$("#password").addEventListener("input", e => {
  if (mode !== "up") return;
  const s = strength(e.target.value), m = $("#meter");
  m.style.width = (s / 5 * 100) + "%";
  m.style.background = s <= 1 ? "#c4572e" : s <= 3 ? "#e9a23b" : "#1d7a4a";
  $("#meter-l").textContent = !e.target.value ? "Use 8+ characters with a letter and a number." : ["Too weak", "Weak", "Okay", "Good", "Strong", "Very strong"][s];
});
$("#peek").onclick = () => {
  const p = $("#password"), show = p.type === "password";
  p.type = show ? "text" : "password";
  $("#peek").textContent = show ? "Hide" : "Show";
  $("#peek").setAttribute("aria-label", show ? "Hide password" : "Show password");
};
document.querySelectorAll(".field input").forEach(i => i.addEventListener("input", () => {
  const f = i.closest(".field"); f.classList.remove("bad"); f.querySelector(".err").textContent = "";
}));

function validate() {
  clearErrors();
  let ok = true;
  const email = $("#email").value.trim(), pw = $("#password").value;
  if (mode === "up") {
    if ($("#name").value.trim().length < 2) { fieldError("name", "Please enter your name."); ok = false; }
    if (!$("#store").value.trim()) { fieldError("store", "Please enter your store's name."); ok = false; }
  }
  if (!EMAIL.test(email)) { fieldError("email", "Please enter a valid email address."); ok = false; }
  if (mode === "up" && (pw.length < 8 || !/[A-Za-z]/.test(pw) || !/\d/.test(pw))) { fieldError("password", "Use at least 8 characters, with a letter and a number."); ok = false; }
  if (mode === "in" && !pw) { fieldError("password", "Please enter your password."); ok = false; }
  if (!ok) { const first = document.querySelector(".field.bad input"); first && first.focus(); }
  return ok;
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || "Something went wrong. Please try again.");
  return d;
}
function busy(btn, on, label) {
  btn.disabled = on;
  if (on) { btn._l = btn.innerHTML; btn.innerHTML = `<span class="spin"></span> ${label}`; } else if (btn._l) btn.innerHTML = btn._l;
}

$("#form").addEventListener("submit", async e => {
  e.preventDefault();
  if (!validate()) return;
  const btn = $("#submit");
  busy(btn, true, mode === "up" ? "Creating your account…" : "Signing in…");
  try {
    const body = { email: $("#email").value.trim(), password: $("#password").value };
    if (mode === "up") Object.assign(body, { name: $("#name").value.trim(), store: $("#store").value.trim() });
    await post(mode === "up" ? "/api/auth/signup" : "/api/auth/login", body);
    btn.innerHTML = "✓ Done, opening your control room";
    location.href = target;
  } catch (err) {
    $("#form-err").textContent = err.message; $("#form-err").classList.add("on");
    busy(btn, false);
  }
});
$("#demo").onclick = async () => {
  const btn = $("#demo");
  busy(btn, true, "Opening the demo…");
  try { await post("/api/auth/demo"); location.href = target; }
  catch (err) { $("#form-err").textContent = err.message; $("#form-err").classList.add("on"); busy(btn, false); }
};
setMode(mode);
