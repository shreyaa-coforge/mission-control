// Shared helpers for the Mission Control pages.
//
// SECURITY: every value from the API is untrusted. Build DOM with el() (which
// uses textContent) and never parse data as HTML markup.
"use strict";

/**
 * Send/receive JSON. Never throws on HTTP errors.
 * Returns {ok, status, data}. On network failure returns
 * {ok: false, status: 0, data: {error: "Cannot reach the server"}}.
 */
async function api(path, options) {
  const opts = Object.assign({ method: "GET" }, options || {});
  const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
  if (opts.body !== undefined && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  opts.headers = headers;

  let response;
  try {
    response = await fetch(path, opts);
  } catch (err) {
    return { ok: false, status: 0, data: { error: "Cannot reach the server" } };
  }

  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = response.ok ? {} : { error: "Server error (HTTP " + response.status + ")" };
  }
  if (data === null || typeof data !== "object") {
    data = { value: data };
  }
  return { ok: response.ok, status: response.status, data: data };
}

/**
 * Build an element safely.
 *   el("div", {className: "x", text: "hello", attrs: {id: "a"}}, [child, "text"])
 * Text is set with textContent; string children become text nodes.
 */
function el(tag, props, children) {
  const node = document.createElement(tag);
  const p = props || {};
  if (p.className) node.className = p.className;
  if (p.text !== undefined && p.text !== null) node.textContent = String(p.text);
  if (p.attrs) {
    for (const name of Object.keys(p.attrs)) {
      const value = p.attrs[name];
      if (value === undefined || value === null || value === false) continue;
      node.setAttribute(name, value === true ? "" : String(value));
    }
  }
  for (const child of children || []) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number"
      ? document.createTextNode(String(child))
      : child);
  }
  return node;
}

/** Remove every child of a node. */
function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** "$0.000123" with 6 decimals; "n/a" for null/undefined/non-numbers. */
function formatCost(n) {
  if (n === null || n === undefined || n === "" || !isFinite(Number(n))) return "n/a";
  return "$" + Number(n).toFixed(6);
}

/** ISO timestamp -> local time string. */
function formatTime(iso) {
  if (!iso) return "n/a";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

/** 5 -> "5 min". */
function formatMinutes(n) {
  if (n === null || n === undefined || !isFinite(Number(n))) return "n/a";
  return Number(n) + " min";
}

/** Verdict badge built from the server's verdict value. */
function verdictBadge(verdict, large) {
  const size = large ? " badge-large" : "";
  if (verdict === "go") return el("span", { className: "badge badge-go" + size, text: "GO" });
  if (verdict === "no_go") return el("span", { className: "badge badge-nogo" + size, text: "NO_GO" });
  return el("span", { className: "badge badge-unknown" + size, text: "NO RESULT" });
}

function needsReviewBadge() {
  return el("span", { className: "badge badge-review", text: "needs review" });
}

function statusBadge(status) {
  if (status === "approved") return el("span", { className: "badge badge-approved", text: "approved" });
  if (status === "pending") return el("span", { className: "badge badge-pending", text: "pending" });
  return el("span", { className: "badge badge-unknown", text: "no briefing" });
}
