// Engineer report form. The server judges every reading; the form only
// checks for empty fields. All server data is inserted with textContent.
"use strict";

(function () {
  const FIELDS = ["name", "station", "wind_knots", "temp_c", "fuel_pct", "note"];
  const NUMBER_FIELDS = ["wind_knots", "temp_c", "fuel_pct"];
  const NOTE_MAX = 2000;

  const form = document.getElementById("report-form");
  const summary = document.getElementById("form-summary");
  const success = document.getElementById("success-panel");
  const submitBtn = document.getElementById("submit-btn");
  const note = document.getElementById("note");
  const counter = document.getElementById("note-counter");
  const SUBMIT_LABEL = submitBtn.textContent;

  function input(field) { return form.elements.namedItem(field); }
  function errorEl(field) { return document.getElementById(field + "-error"); }

  function setFieldError(field, message) {
    const box = errorEl(field);
    box.textContent = message;
    input(field).setAttribute("aria-invalid", "true");
  }

  function clearFieldError(field) {
    errorEl(field).textContent = "";
    input(field).removeAttribute("aria-invalid");
  }

  function showSummary(title, messages) {
    clear(summary);
    summary.appendChild(el("strong", { text: title }));
    if (messages && messages.length) {
      summary.appendChild(el("ul", {}, messages.map(function (m) { return el("li", { text: m }); })));
    }
    summary.classList.remove("hidden");
    summary.focus();
  }

  function hideSummary() {
    clear(summary);
    summary.classList.add("hidden");
  }

  function clearAllErrors() {
    FIELDS.forEach(clearFieldError);
    hideSummary();
  }

  function updateCounter() {
    const len = note.value.length;
    counter.textContent = len + " / " + NOTE_MAX;
    counter.classList.toggle("over", len > NOTE_MAX);
  }

  // Clear a field's error as soon as the user edits it.
  FIELDS.forEach(function (field) {
    input(field).addEventListener("input", function () { clearFieldError(field); });
  });
  note.addEventListener("input", updateCounter);

  function showSuccess(data) {
    clear(success);
    const report = data.report || {};
    const result = data.result;
    const briefing = data.briefing;

    success.appendChild(el("h2", { text: "Report filed" }));
    const line = el("div", { className: "row-line" });
    line.appendChild(verdictBadge(result ? result.verdict : null));
    if (result) {
      line.appendChild(el("span", { text: "Brief by " + formatMinutes(result.brief_by_minutes) }));
      if (result.needs_review) line.appendChild(needsReviewBadge());
    }
    if (briefing) line.appendChild(statusBadge(briefing.status));
    success.appendChild(line);

    if (data.error) {
      success.appendChild(el("p", { className: "notice notice-warn", text: data.error }));
    }

    const href = "/?report=" + encodeURIComponent(String(report.id));
    success.appendChild(el("p", {}, [el("a", { text: "Open in console", attrs: { href: href } })]));
    success.classList.remove("hidden");
    success.scrollIntoView({ block: "nearest" });
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    clearAllErrors();
    success.classList.add("hidden");

    // Client-side: only check for empty fields. Out-of-limit readings are
    // real readings and are judged by the server.
    let firstBad = null;
    FIELDS.forEach(function (field) {
      const node = input(field);
      if (node.validity && node.validity.badInput) {
        setFieldError(field, field + " must be a number");
        firstBad = firstBad || node;
      } else if (node.value.trim() === "") {
        setFieldError(field, field + " is required");
        firstBad = firstBad || node;
      }
    });
    if (firstBad) {
      firstBad.focus();
      return;
    }

    const body = {};
    FIELDS.forEach(function (field) {
      const raw = input(field).value;
      body[field] = NUMBER_FIELDS.indexOf(field) >= 0 ? Number(raw) : raw;
    });

    submitBtn.disabled = true;
    submitBtn.textContent = "Filing report...";
    let res;
    try {
      res = await api("/reports", { method: "POST", body: body });
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = SUBMIT_LABEL;
    }

    if (res.status === 201) {
      form.reset();
      updateCounter();
      showSuccess(res.data);
      return;
    }

    if (res.status === 422 && Array.isArray(res.data.details)) {
      const leftovers = [];
      res.data.details.forEach(function (d) {
        const field = d && d.field;
        const message = d && d.message ? String(d.message) : "Invalid value";
        if (FIELDS.indexOf(field) >= 0) {
          setFieldError(field, message);
        } else {
          leftovers.push(message);
        }
      });
      if (leftovers.length) {
        showSummary(res.data.error || "Invalid report", leftovers);
      } else {
        const first = res.data.details.find(function (d) { return FIELDS.indexOf(d.field) >= 0; });
        if (first) input(first.field).focus();
      }
      return;
    }

    const msg = (res.data && res.data.error) ? String(res.data.error)
      : "Request failed (HTTP " + res.status + ")";
    showSummary(msg);
  });

  updateCounter();
})();
