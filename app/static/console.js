// Flight Director console.
//
// The browser NEVER computes verdict, severity, brief-by time or cost. It only
// displays values from the API (token totals are summed for display only).
// All API data is untrusted and inserted with textContent via el().
"use strict";

(function () {
  const REFRESH_MS = 10000;

  const listEl = document.getElementById("reports-list");
  const listStatus = document.getElementById("list-status");
  const detailEl = document.getElementById("report-detail");

  let selectedId = null;
  let currentView = null;     // last GET /reports/{id} payload shown
  let detailRequest = 0;      // guards against out-of-order responses
  let countdownTimer = null;
  let listLoadedOnce = false;

  // ---------- URL handling ----------

  function idFromUrl() {
    const raw = new URLSearchParams(window.location.search).get("report");
    if (raw === null || !/^\d+$/.test(raw)) return null;
    return Number(raw);
  }

  function setUrl(id) {
    const url = id === null ? "/" : "/?report=" + encodeURIComponent(String(id));
    if (window.location.pathname + window.location.search !== url) {
      window.history.pushState({ report: id }, "", url);
    }
  }

  // ---------- Reports list ----------

  function renderList(reports) {
    clear(listEl);
    if (!reports.length) {
      listEl.appendChild(el("li", { className: "empty", text: "No reports yet. File one from the form." }));
      return;
    }
    reports.forEach(function (r) {
      const alert = r.verdict === "no_go" || r.needs_review === true;
      let cls = "report-row";
      if (alert) cls += " alert";
      if (alert && r.verdict !== "no_go") cls += " review-only";
      if (r.id === selectedId) cls += " selected";

      const line1 = el("div", { className: "row-line" }, [
        verdictBadge(r.verdict),
        el("span", { className: "row-name", text: r.name }),
        el("span", { className: "muted", text: "@ " + r.station }),
      ]);
      const line2 = el("div", { className: "row-line small" }, [
        el("span", { text: (r.subsystem || "n/a") + " / " + (r.effective_severity || "n/a") }),
        el("span", { className: "muted", text: "| brief by " + formatMinutes(r.brief_by_minutes) }),
        el("span", { className: "muted", text: "| " + formatCost(r.total_cost) }),
      ]);
      const line3 = el("div", { className: "row-line small" }, [
        r.needs_review ? needsReviewBadge() : null,
        statusBadge(r.briefing_status),
        el("span", { className: "muted", text: formatTime(r.created_at) }),
      ]);

      const button = el("button", {
        className: cls,
        attrs: { type: "button", "aria-current": r.id === selectedId ? "true" : null, "data-id": r.id },
      }, [line1, line2, line3]);
      button.addEventListener("click", function () { selectReport(r.id, true); });
      listEl.appendChild(el("li", {}, [button]));
    });
  }

  function highlightSelected() {
    listEl.querySelectorAll(".report-row").forEach(function (row) {
      const on = Number(row.getAttribute("data-id")) === selectedId;
      row.classList.toggle("selected", on);
      if (on) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    });
  }

  async function loadList() {
    const res = await api("/reports");
    if (!res.ok || !Array.isArray(res.data.reports)) {
      if (listLoadedOnce) {
        // Keep the list we have; just say there is a problem.
        listStatus.textContent = "Connection problem, retrying";
        listStatus.classList.add("problem");
      } else {
        clear(listEl);
        listEl.appendChild(el("li", { className: "empty notice-error",
          text: (res.data && res.data.error ? String(res.data.error) : "Could not load reports") + ". Retrying..." }));
        listStatus.textContent = "Connection problem, retrying";
        listStatus.classList.add("problem");
      }
      return;
    }
    listLoadedOnce = true;
    listStatus.classList.remove("problem");
    listStatus.textContent = "Updated " + new Date().toLocaleTimeString();
    renderList(res.data.reports);
  }

  // ---------- Detail ----------

  function stopCountdown() {
    if (countdownTimer !== null) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }
  }

  function selectReport(id, pushUrl) {
    selectedId = id;
    highlightSelected();
    if (pushUrl) setUrl(id);
    loadDetail(id);
  }

  async function loadDetail(id) {
    const token = ++detailRequest;
    stopCountdown();
    clear(detailEl);
    detailEl.appendChild(el("p", { className: "muted", text: "Loading..." }));

    const res = await api("/reports/" + encodeURIComponent(String(id)));
    if (token !== detailRequest) return; // a newer selection won
    if (!res.ok) {
      currentView = null;
      clear(detailEl);
      const msg = res.data && res.data.error ? String(res.data.error) : "Request failed (HTTP " + res.status + ")";
      detailEl.appendChild(el("div", { className: "notice notice-error", attrs: { role: "alert" } }, [
        el("strong", { text: "Could not load report " + id + ": " }), msg,
      ]));
      return;
    }
    currentView = res.data;
    renderDetail(res.data);
  }

  function section(title, children) {
    return el("section", {}, [title ? el("h2", { text: title }) : null].concat(children));
  }

  function renderHeader(report, result, briefing) {
    const head = el("div", { className: "detail-header" }, [
      verdictBadge(result ? result.verdict : null, true),
      el("div", {}, [
        el("div", { className: "who", text: report.name }),
        el("div", { className: "muted", text: "Station: " + report.station }),
        el("div", { className: "muted small", text: "Filed " + formatTime(report.created_at) }),
      ]),
    ]);

    const children = [head];
    if (result) {
      const minutes = result.brief_by_minutes;
      const filed = new Date(report.created_at).getTime();
      // Deadline is created_at plus the server's brief_by_minutes; the
      // browser only adds the two for display.
      const deadline = isNaN(filed) ? null : new Date(filed + Number(minutes) * 60000);
      const countdown = el("span", { className: "countdown" });
      children.push(el("div", { className: "brief-by" }, [
        el("strong", { text: "Brief by: " + formatMinutes(minutes) }),
        el("span", { className: "muted", text: deadline ? "deadline " + deadline.toLocaleTimeString() : "" }),
        countdown,
      ]));
      startCountdown(countdown, deadline, briefing && briefing.status === "approved");
    } else {
      children.push(el("p", { className: "notice notice-warn",
        text: "No pipeline result for this report, so there is no verdict or brief by time." }));
    }
    return el("section", {}, children);
  }

  function startCountdown(node, deadline, briefed) {
    stopCountdown();
    if (briefed) {
      node.textContent = "Briefed";
      node.className = "countdown briefed";
      return;
    }
    if (!deadline) return;
    function tick() {
      const left = deadline.getTime() - Date.now();
      if (left <= 0) {
        node.textContent = "Overdue";
        node.className = "countdown overdue";
        stopCountdown();
        return;
      }
      const total = Math.floor(left / 1000);
      const m = Math.floor(total / 60);
      const s = total % 60;
      node.textContent = m + ":" + (s < 10 ? "0" : "") + s + " left";
      node.className = "countdown";
    }
    tick();
    if (countdownTimer === null && node.textContent !== "Overdue") {
      countdownTimer = setInterval(tick, 1000);
    }
  }

  function renderReadings(report, result) {
    const violations = (result && Array.isArray(result.violations)) ? result.violations : [];
    const violated = {};
    violations.forEach(function (v) { if (v && v.reading) violated[v.reading] = true; });

    const items = [
      ["wind_knots", "Wind (knots)"],
      ["temp_c", "Temp (C)"],
      ["fuel_pct", "Fuel (%)"],
    ].map(function (pair) {
      return el("div", { className: "kv-item" + (violated[pair[0]] ? " violated" : "") }, [
        el("div", { className: "k", text: pair[0] + " - " + pair[1] }),
        el("div", { className: "v", text: report[pair[0]] }),
      ]);
    });

    const children = [el("div", { className: "kv" }, items)];
    if (!result) {
      children.push(el("p", { className: "muted", text: "Violations: not available" }));
    } else if (violations.length === 0) {
      children.push(el("p", { text: "Violations: none" }));
    } else {
      children.push(el("ul", { className: "violations" },
        violations.map(function (v) { return el("li", { text: v.message }); })));
    }
    return section("Readings", children);
  }

  function renderClassification(result) {
    if (!result) return section("Classification", [el("p", { className: "muted", text: "Not available." })]);
    const children = [el("div", { className: "kv" }, [
      el("div", { className: "kv-item" }, [el("div", { className: "k", text: "Subsystem" }), el("div", { className: "v", text: result.subsystem })]),
      el("div", { className: "kv-item" }, [el("div", { className: "k", text: "Severity (model)" }), el("div", { className: "v", text: result.severity })]),
      el("div", { className: "kv-item" }, [el("div", { className: "k", text: "Effective severity (code)" }), el("div", { className: "v", text: result.effective_severity })]),
    ])];
    if (result.effective_severity !== result.severity) {
      children.push(el("p", { className: "notice notice-warn", text: "Raised to high because a launch limit was violated." }));
    }
    if (result.classify_fallback) {
      children.push(el("p", { className: "notice notice-warn", text: "Model reply was invalid; safe fallback used." }));
    }
    return section("Classification", children);
  }

  function renderReview(result) {
    if (!result) return section("Review flags", [el("p", { className: "muted", text: "Not available." })]);
    const children = [];
    if (result.needs_review) {
      const reasons = Array.isArray(result.review_reasons) ? result.review_reasons : [];
      children.push(el("div", { className: "review-box", attrs: { role: "note" } }, [
        el("h3", { text: "Needs review" }),
        el("ul", {}, reasons.map(function (r) { return el("li", { text: r }); })),
      ]));
    }
    const checks = result.checks || {};
    const cards = ["injection", "missing", "panic"].map(function (name) {
      const c = checks[name];
      if (!c) {
        return el("div", { className: "check" }, [el("h3", { text: name }), el("p", { className: "muted", text: "Not run." })]);
      }
      const state = c.failed ? "failed" : (c.flagged ? "flagged" : "clear");
      const details = Array.isArray(c.details) ? c.details : [];
      return el("div", { className: "check" }, [
        el("h3", { text: name }),
        el("div", { className: "check-state " + state, text: state }),
        el("p", { className: "small", text: c.reason || "" }),
        details.length ? el("ul", {}, details.map(function (d) { return el("li", { text: d }); })) : null,
      ]);
    });
    children.push(el("div", { className: "checks" }, cards));
    return section("Review flags", children);
  }

  function renderNote(report) {
    return section("Engineer note", [
      el("div", { className: "raw-note" }, [
        el("div", { className: "label", text: "Raw note (untrusted)" }),
        el("pre", { text: report.note }),
      ]),
    ]);
  }

  function approvedLine(briefing) {
    return el("p", { className: "approved-line",
      text: "Approved by " + briefing.approved_by + " at " + formatTime(briefing.approved_at) });
  }

  function renderBriefing(reportId, result, briefing) {
    if (!briefing) return section("Briefing", [el("p", { className: "muted", text: "No briefing for this report." })]);

    const meta = el("div", { className: "row-line" }, [
      statusBadge(briefing.status),
      el("span", { text: "Rounds used: " + briefing.rounds_used }),
      el("span", { text: "| Reviewer passed: " + (briefing.review_passed ? "yes" : "no") }),
    ]);
    const children = [meta];
    if (!briefing.review_passed) {
      children.push(el("p", { className: "notice notice-warn",
        text: "Reviewer did not pass this draft after " + briefing.rounds_used + " rounds." }));
    }
    children.push(el("pre", { text: briefing.full_text || "" }));

    if (briefing.status === "approved") {
      children.push(approvedLine(briefing));
    } else if (briefing.status === "pending") {
      children.push(renderApprovePanel(reportId, !!(result && result.needs_review)));
    }
    return section("Briefing", children);
  }

  function renderApprovePanel(reportId, needsReview) {
    const nameId = "approve-name";
    const panel = el("form", { className: "approve-panel", attrs: { novalidate: true } });
    const nameErr = el("div", { className: "field-error", attrs: { id: nameId + "-error" } });
    const nameInput = el("input", { attrs: { type: "text", id: nameId, name: "approved_by", maxlength: "100", autocomplete: "name", "aria-describedby": nameId + "-error" } });
    panel.appendChild(el("h3", { text: "Approve briefing" }));
    panel.appendChild(el("div", { className: "field" }, [
      el("label", { text: "Your name", attrs: { for: nameId } }), nameInput, nameErr,
    ]));

    let checkbox = null;
    const checkErr = el("div", { className: "field-error", attrs: { id: "approve-reviewed-error" } });
    if (needsReview) {
      checkbox = el("input", { attrs: { type: "checkbox", id: "approve-reviewed", "aria-describedby": "approve-reviewed-error" } });
      panel.appendChild(el("div", { className: "checkbox-row" }, [
        checkbox, el("label", { text: "I have reviewed the flagged issues", attrs: { for: "approve-reviewed" } }),
      ]));
      panel.appendChild(checkErr);
      checkbox.addEventListener("change", function () { checkErr.textContent = ""; });
    }
    nameInput.addEventListener("input", function () { nameErr.textContent = ""; nameInput.removeAttribute("aria-invalid"); });

    const button = el("button", { text: "Approve briefing", attrs: { type: "submit" } });
    const serverErr = el("div", { className: "notice notice-error hidden", attrs: { role: "alert" } });
    panel.appendChild(button);
    panel.appendChild(serverErr);

    panel.addEventListener("submit", async function (event) {
      event.preventDefault();
      clear(serverErr);
      serverErr.classList.add("hidden");
      nameErr.textContent = "";
      checkErr.textContent = "";

      const name = nameInput.value.trim();
      let bad = false;
      if (name === "") {
        nameErr.textContent = "Your name is required";
        nameInput.setAttribute("aria-invalid", "true");
        bad = true;
      }
      if (checkbox && !checkbox.checked) {
        checkErr.textContent = "Confirm you reviewed the flagged issues";
        bad = true;
      }
      if (bad) return;

      button.disabled = true;
      const res = await api("/briefings/" + encodeURIComponent(String(reportId)) + "/approve", {
        method: "POST",
        body: { approved_by: name, reviewed: checkbox ? checkbox.checked : false },
      });
      button.disabled = false;

      if (res.status === 200) {
        const view = currentView;
        if (view && view.report && view.report.id === reportId) {
          view.briefing = res.data;
          renderDetail(view);
        } else {
          panel.replaceWith(approvedLine(res.data));
        }
        loadList();
        return;
      }

      const lines = [];
      if (Array.isArray(res.data.review_reasons)) {
        res.data.review_reasons.forEach(function (r) { lines.push(String(r)); });
      }
      if (Array.isArray(res.data.details)) {
        res.data.details.forEach(function (d) {
          if (d && d.message) lines.push(String(d.message));
        });
      }
      serverErr.appendChild(el("strong", { text: res.data.error ? String(res.data.error) : "Approval failed (HTTP " + res.status + ")" }));
      if (lines.length) serverErr.appendChild(el("ul", {}, lines.map(function (l) { return el("li", { text: l }); })));
      serverErr.classList.remove("hidden");
    });
    return panel;
  }

  function renderTrace(result, trace) {
    const steps = Array.isArray(trace) ? trace.slice() : [];
    steps.sort(function (a, b) { return a.position - b.position; });
    // Token totals are summed for display only. Cost comes from the server.
    let inTok = 0, outTok = 0;
    steps.forEach(function (s) { inTok += Number(s.input_tokens) || 0; outTok += Number(s.output_tokens) || 0; });

    const children = [el("div", { className: "trace-summary",
      text: steps.length + " steps | " + inTok + " input tokens | " + outTok + " output tokens | total cost " +
        formatCost(result ? result.total_cost : null) })];

    const cards = steps.map(function (s) {
      const isModel = s.kind === "model";
      const detail = s.detail || {};
      const head = el("div", { className: "row-line" }, [
        el("span", { className: "title", text: s.position + ". " + s.step }),
        el("span", { className: "badge " + (isModel ? "badge-model" : "badge-code"), text: isModel ? "model" : "code" }),
        isModel && s.model_role ? el("span", { className: "small muted", text: "role: " + s.model_role }) : null,
        detail.parallel_group === "checks" ? el("span", { className: "parallel", text: "ran in parallel" }) : null,
        detail.parallel_group === "checks" ? el("span", { className: "small muted", text: detail.elapsed_ms + " ms" }) : null,
      ]);
      const metaText = isModel
        ? s.input_tokens + " input tokens, " + s.output_tokens + " output tokens, " + formatCost(s.cost)
        : ((Number(s.input_tokens) || 0) + (Number(s.output_tokens) || 0)) + " tokens, " + formatCost(s.cost);
      return el("div", { className: "trace-card " + (isModel ? "model" : "code") }, [
        head,
        el("div", { className: "meta", text: metaText }),
        el("div", { text: s.summary }),
        el("details", {}, [
          el("summary", { text: "Show details" }),
          el("pre", { text: JSON.stringify(detail, null, 2) }),
        ]),
      ]);
    });
    children.push(el("div", { className: "trace-cards" }, cards));
    return section("Pipeline trace", children);
  }

  function renderDetail(view) {
    stopCountdown();
    clear(detailEl);
    const report = view.report;
    const result = view.result;
    const briefing = view.briefing;
    detailEl.appendChild(renderHeader(report, result, briefing));
    detailEl.appendChild(renderReadings(report, result));
    detailEl.appendChild(renderClassification(result));
    detailEl.appendChild(renderReview(result));
    detailEl.appendChild(renderNote(report));
    detailEl.appendChild(renderBriefing(report.id, result, briefing));
    detailEl.appendChild(renderTrace(result, view.trace));
  }

  // ---------- Start ----------

  window.addEventListener("popstate", function () {
    const id = idFromUrl();
    if (id === null) {
      selectedId = null;
      stopCountdown();
      highlightSelected();
      clear(detailEl);
      detailEl.appendChild(el("p", { className: "muted", text: "Select a report from the list." }));
    } else {
      selectReport(id, false);
    }
  });

  const initial = idFromUrl();
  loadList();
  if (initial !== null) selectReport(initial, false);
  setInterval(loadList, REFRESH_MS);
})();
