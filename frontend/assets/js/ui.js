/* ==========================================================================
   SamadhanSetu — ui.js
   Small, dependency-free rendering helpers shared by every page.
   Exposed globally as `UI`.
   ========================================================================== */
(function () {
  "use strict";

  /** Escape untrusted text before inserting into innerHTML. */
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }
  function qsa(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function param(name, fallback) {
    const v = new URLSearchParams(window.location.search).get(name);
    return v === null ? (fallback === undefined ? null : fallback) : v;
  }

  function fmtDate(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function fmtDateTime(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleString(undefined, {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    });
  }

  function timeAgo(value) {
    if (!value) return "";
    const d = new Date(value);
    if (isNaN(d.getTime())) return "";
    const secs = Math.round((Date.now() - d.getTime()) / 1000);
    if (secs < 60) return "just now";
    const mins = Math.round(secs / 60);
    if (mins < 60) return mins + "m ago";
    const hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    const days = Math.round(hours / 24);
    if (days < 30) return days + "d ago";
    return fmtDate(value);
  }

  function money(amount, currency) {
    if (amount === null || amount === undefined) return "Not stated";
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency", currency: currency || "INR", maximumFractionDigits: 0,
      }).format(amount);
    } catch (e) {
      return (currency || "INR") + " " + amount;
    }
  }

  function titleCase(text) {
    if (!text) return "";
    return String(text)
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (c) {
        return c.toUpperCase();
      });
  }

  // Status → badge variant. Status is ALWAYS shown as text, never colour alone.
  const STATUS_VARIANT = {
    submitted: "info", needs_clarification: "warning", pending_review: "warning",
    approved: "success", accepted: "primary", in_project: "primary", completed: "success",
    excluded_government: "danger", declined: "danger",
    todo: "neutral", in_progress: "info", under_review: "warning",
    planning: "neutral", active: "primary", on_hold: "warning", cancelled: "danger",
    invited: "warning", removed: "neutral",
    pending: "neutral", submitted_for_review: "warning", changes_requested: "danger",
    overdue: "danger", long_term_unestablished: "neutral",
    proposed: "info", accepted_commitment: "primary", committed: "primary", disbursed: "success",
    registered: "info", upcoming: "neutral", sent: "info",
    verified: "success", demonstrated: "info",
    proposed_impact: "info",
  };

  function badge(status, options) {
    options = options || {};
    const variant = options.variant || STATUS_VARIANT[status] || "neutral";
    const label = options.label || titleCase(status || "unknown");
    return '<span class="badge badge--' + variant + '">' + esc(label) + "</span>";
  }

  // One shared vocabulary for problem domains. The reporting form loads the list
  // from the API; every other page uses the same labels so the words a citizen
  // picked are the words they see again. First label per key wins.
  var DOMAIN_LABELS = {
    water: "Water and sanitation",
    agriculture: "Agriculture",
    environment: "Environment and pollution",
    waste: "Waste and recycling",
    transport: "Transportation",
    education: "Education",
    health: "Healthcare",
    electricity: "Energy and electricity",
    safety: "Public safety",
    accessibility: "Accessibility",
    other: "Community infrastructure",
  };

  function setDomainLabels(domains) {
    (domains || []).forEach(function (d) {
      if (d && d.key && !DOMAIN_LABELS[d.key]) DOMAIN_LABELS[d.key] = d.label;
    });
  }

  function domainLabel(key) {
    if (!key) return "";
    return DOMAIN_LABELS[key] || titleCase(key);
  }

  function demoTag(show) {
    if (!show) return "";
    return '<span class="tag-demo" title="Seeded demonstration record, not real user activity">Demo data</span>';
  }

  function emptyState(icon, title, text, actionHtml) {
    return (
      '<div class="empty-state">' +
      '<div class="empty-state__icon" aria-hidden="true">' + esc(icon) + "</div>" +
      "<h3>" + esc(title) + "</h3>" +
      "<p>" + esc(text || "") + "</p>" +
      (actionHtml ? '<div style="margin-top:var(--space-4)">' + actionHtml + "</div>" : "") +
      "</div>"
    );
  }

  function loading(text) {
    return (
      '<div class="empty-state"><div class="spinner" aria-hidden="true"></div>' +
      '<p style="margin-top:var(--space-3)">' + esc(text || "Loading…") + "</p></div>"
    );
  }

  function errorState(message, retryFn) {
    return (
      '<div class="empty-state"><div class="empty-state__icon" aria-hidden="true">⚠️</div>' +
      "<h3>Something went wrong</h3><p>" + esc(message || "Please try again.") + "</p></div>"
    );
  }

  // Toast region (created on demand).
  function toastRegion() {
    let region = document.querySelector(".toast-region");
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.setAttribute("aria-live", "polite");
      document.body.appendChild(region);
    }
    return region;
  }

  function toast(message, type, timeout) {
    const region = toastRegion();
    const el = document.createElement("div");
    el.className = "toast" + (type ? " toast--" + type : "");
    el.setAttribute("role", "status");
    el.innerHTML =
      '<span>' + esc(message) + '</span>' +
      '<button class="toast__close" aria-label="Dismiss">×</button>';
    region.appendChild(el);
    const remove = function () {
      el.style.opacity = "0";
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 200);
    };
    el.querySelector(".toast__close").addEventListener("click", remove);
    setTimeout(remove, timeout || 4200);
  }

  function setBusy(button, busy, busyLabel) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      button.innerHTML = '<span class="btn__spinner"></span>' + esc(busyLabel || "Working…");
    } else {
      button.disabled = false;
      if (button.dataset.label) button.textContent = button.dataset.label;
    }
  }

  function field(name, label, options) {
    options = options || {};
    const id = options.id || name;
    const hint = options.hint ? '<span class="field__hint">' + esc(options.hint) + "</span>" : "";
    const error = '<span class="field__error" role="alert"></span>';
    let control;
    if (options.type === "textarea") {
      control =
        '<textarea class="input" id="' + id + '" name="' + name + '" rows="' +
        (options.rows || 4) + '"' +
        (options.placeholder ? ' placeholder="' + esc(options.placeholder) + '"' : "") +
        (options.required ? " required" : "") + "></textarea>";
    } else if (options.type === "select") {
      control =
        '<select class="select" id="' + id + '" name="' + name + '">' +
        (options.options || []).map(function (o) {
          return (
            '<option value="' + esc(o.value) + '"' +
            (o.value === options.value ? " selected" : "") + ">" + esc(o.label) + "</option>"
          );
        }).join("") +
        "</select>";
    } else {
      control =
        '<input class="input" id="' + id + '" name="' + name + '" type="' +
        (options.type || "text") + '"' +
        (options.placeholder ? ' placeholder="' + esc(options.placeholder) + '"' : "") +
        (options.value !== undefined && options.value !== null ? ' value="' + esc(options.value) + '"' : "") +
        (options.required ? " required" : "") +
        (options.min !== undefined ? ' min="' + options.min + '"' : "") +
        (options.max !== undefined ? ' max="' + options.max + '"' : "") +
        (options.step !== undefined ? ' step="' + options.step + '"' : "") + ">";
    }
    return (
      '<div class="field" data-field="' + name + '">' +
      '<label class="field__label" for="' + id + '">' + esc(label) +
      (options.required ? ' <span aria-hidden="true">*</span>' : "") + "</label>" +
      control + hint + error + "</div>"
    );
  }

  function formValues(form) {
    const data = {};
    new FormData(form).forEach(function (value, key) {
      const v = typeof value === "string" ? value.trim() : value;
      if (v !== "") data[key] = v;
    });
    return data;
  }

  function showFieldError(form, name, message) {
    const wrap = form.querySelector('[data-field="' + name + '"]');
    if (wrap) {
      wrap.classList.add("has-error");
      const err = wrap.querySelector(".field__error");
      if (err) err.textContent = message;
    }
  }

  function clearFieldErrors(form) {
    qsa(".field.has-error", form).forEach(function (f) {
      f.classList.remove("has-error");
      const e = f.querySelector(".field__error");
      if (e) e.textContent = "";
    });
  }

  function pagination(container, page, pages, onGo) {
    if (!container) return;
    if (pages <= 1) {
      container.innerHTML = "";
      return;
    }
    let html = "";
    html += '<button data-page="' + (page - 1) + '"' + (page <= 1 ? " disabled" : "") + ">Prev</button>";
    const start = Math.max(1, page - 2);
    const end = Math.min(pages, start + 4);
    for (let p = start; p <= end; p++) {
      html += '<button data-page="' + p + '"' + (p === page ? ' class="is-active"' : "") + ">" + p + "</button>";
    }
    html += '<button data-page="' + (page + 1) + '"' + (page >= pages ? " disabled" : "") + ">Next</button>";
    container.innerHTML = html;
    qsa("button", container).forEach(function (btn) {
      btn.addEventListener("click", function () {
        const p = parseInt(btn.dataset.page, 10);
        if (p >= 1 && p <= pages && p !== page) onGo(p);
      });
    });
  }

  // ------------------------------------------------------------------------
  // Reporting wizard, journey and chart components
  // ------------------------------------------------------------------------

  function formatBytes(bytes) {
    var n = Number(bytes) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  /** Progress indicator for multi-step forms. `current` is 1-based. */
  function stepper(steps, current) {
    var html = '<ol class="stepper" role="list">';
    steps.forEach(function (s, i) {
      var n = i + 1;
      var state = n < current ? "is-done" : (n === current ? "is-current" : "");
      html +=
        '<li class="stepper__item ' + state + '"' +
        (n === current ? ' aria-current="step"' : "") + '>' +
        '<span class="stepper__marker" aria-hidden="true">' + (n < current ? "✓" : n) + "</span>" +
        '<span class="stepper__text"><strong>' + esc(s.label) + "</strong>" +
        (s.hint ? '<span class="stepper__hint">' + esc(s.hint) + "</span>" : "") +
        "</span></li>";
    });
    return html + "</ol>";
  }

  /** A labelled summary block used by the AI-simplification panels. */
  function insight(label, value, options) {
    options = options || {};
    var body = options.html ? value : esc(value || "—");
    return (
      '<div class="insight' + (options.tone ? " insight--" + options.tone : "") + '">' +
      '<p class="insight__label">' + esc(label) + "</p>" +
      '<div class="insight__value">' + body + "</div>" +
      (options.note ? '<p class="insight__note">' + esc(options.note) + "</p>" : "") +
      "</div>"
    );
  }

  /** Horizontal labelled bars — used for breakdowns (categories, statuses). */
  function breakdown(rows, options) {
    options = options || {};
    if (!rows || !rows.length) {
      return '<p class="muted">' + esc(options.emptyText || "No data yet.") + "</p>";
    }
    var max = Math.max.apply(null, rows.map(function (r) { return Number(r.value) || 0; }).concat([1]));
    return '<div class="breakdown">' + rows.map(function (r) {
      var pct = Math.round(((Number(r.value) || 0) / max) * 100);
      return (
        '<div class="breakdown__row">' +
        '<span class="breakdown__label">' + esc(r.label) + "</span>" +
        '<span class="breakdown__track" role="img" aria-label="' + esc(r.label) + ": " +
          (Number(r.value) || 0) + '">' +
        '<span class="breakdown__fill breakdown__fill--' + (r.variant || "primary") +
          '" style="width:' + pct + '%"></span></span>' +
        '<span class="breakdown__value">' + (Number(r.value) || 0) + "</span>" +
        "</div>");
    }).join("") + "</div>";
  }

  /** Vertical SVG bar chart for a small series (e.g. submissions per week). */
  function bars(series, options) {
    options = options || {};
    if (!series || !series.length) {
      return '<p class="muted">' + esc(options.emptyText || "No data yet.") + "</p>";
    }
    var w = 520, h = options.height || 160, pad = 26;
    var max = Math.max.apply(null, series.map(function (p) { return Number(p.value) || 0; }).concat([1]));
    var step = (w - pad * 2) / series.length;
    var barW = Math.max(6, Math.min(38, step * 0.55));
    var parts = [];
    series.forEach(function (p, i) {
      var v = Number(p.value) || 0;
      var barH = max ? Math.round((v / max) * (h - pad * 2)) : 0;
      var x = pad + i * step + (step - barW) / 2;
      var y = h - pad - barH;
      parts.push(
        '<rect class="chart__bar" x="' + x.toFixed(1) + '" y="' + y + '" width="' + barW.toFixed(1) +
        '" height="' + Math.max(barH, v > 0 ? 2 : 0) + '" rx="4">' +
        "<title>" + esc(p.label) + ": " + v + "</title></rect>");
      parts.push('<text class="chart__value" x="' + (x + barW / 2).toFixed(1) + '" y="' + (y - 6) +
        '" text-anchor="middle">' + v + "</text>");
      parts.push('<text class="chart__label" x="' + (x + barW / 2).toFixed(1) + '" y="' + (h - 8) +
        '" text-anchor="middle">' + esc(p.label) + "</text>");
    });
    return '<div class="chart"><svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' +
      esc(options.title || "Bar chart") + '" preserveAspectRatio="xMidYMid meet">' +
      '<line class="chart__axis" x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad) +
      '" y2="' + (h - pad) + '" />' + parts.join("") + "</svg></div>";
  }

  /** SVG donut with a text legend — used for status mixes. */
  function donut(segments, options) {
    options = options || {};
    var total = (segments || []).reduce(function (a, s) { return a + (Number(s.value) || 0); }, 0);
    if (!total) {
      return '<p class="muted">' + esc(options.emptyText || "No data yet.") + "</p>";
    }
    var colours = {
      primary: "var(--color-primary)", success: "var(--color-success)", warning: "var(--color-warning)",
      danger: "var(--color-error)", info: "var(--color-info)", neutral: "var(--color-border-strong)",
    };
    var r = 52, circumference = 2 * Math.PI * r, offset = 0;
    var arcs = segments.map(function (s) {
      var frac = (Number(s.value) || 0) / total;
      var len = frac * circumference;
      var dash = len.toFixed(2) + " " + (circumference - len).toFixed(2);
      var arc = '<circle class="chart__arc" cx="70" cy="70" r="' + r + '" stroke-dasharray="' +
        dash + '" stroke-dashoffset="' + (-offset).toFixed(2) + '" stroke="' +
        (colours[s.variant] || colours.primary) + '"><title>' + esc(s.label) + ": " +
        (Number(s.value) || 0) + "</title></circle>";
      offset += len;
      return arc;
    }).join("");
    var legend = segments.filter(function (s) { return Number(s.value) > 0; }).map(function (s) {
      return '<li><span class="chart__swatch chart__swatch--' + (s.variant || "primary") + '"></span>' +
        esc(s.label) + ' <strong>' + s.value + "</strong></li>";
    }).join("");
    return '<div class="donut"><div class="donut__figure"><svg viewBox="0 0 140 140" role="img" aria-label="' +
      esc(options.title || "Distribution") + '">' + arcs +
      '<text class="chart__total" x="70" y="66" text-anchor="middle">' + total + "</text>" +
      '<text class="chart__totallabel" x="70" y="84" text-anchor="middle">' +
      esc(options.totalLabel || "total") + "</text></svg></div>" +
      '<ul class="donut__legend">' + legend + "</ul></div>";
  }

  /**
   * The problem's journey through the platform, shown identically wherever the
   * record appears so the user can see it is one connected system.
   */
  function journey(steps) {
    var html = '<ol class="journey">';
    (steps || []).forEach(function (s) {
      html +=
        '<li class="journey__step journey__step--' + (s.state || "pending") + '">' +
        '<span class="journey__dot" aria-hidden="true">' + (s.state === "done" ? "✓" : "•") + "</span>" +
        '<div class="journey__body"><p class="journey__title">' + esc(s.label) + "</p>" +
        (s.detail ? '<p class="journey__detail">' + esc(s.detail) + "</p>" : "") +
        "</div></li>";
    });
    return html + "</ol>";
  }

  /** Tab bar + panel wiring. Returns the markup; call wireTabs() afterwards. */
  function tabs(tabsList, activeKey) {
    var bar = '<div class="tabs" role="tablist">';
    tabsList.forEach(function (t) {
      var on = t.key === activeKey;
      bar += '<button class="tabs__tab' + (on ? " is-active" : '') + '" role="tab" type="button" data-tab="' +
        esc(t.key) + '" aria-selected="' + (on ? "true" : "false") + '">' + esc(t.label) +
        (t.count !== undefined && t.count !== null ? '<span class="tabs__count">' + t.count + "</span>" : "") +
        "</button>";
    });
    return bar + "</div>";
  }

  function wireTabs(root, onSelect) {
    var bar = qs(".tabs", root);
    if (!bar) return;
    qsa(".tabs__tab", bar).forEach(function (btn) {
      btn.addEventListener("click", function () {
        qsa(".tabs__tab", bar).forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
          b.setAttribute("aria-selected", b === btn ? "true" : "false");
        });
        if (onSelect) onSelect(btn.dataset.tab);
      });
    });
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait || 300);
    };
  }

  window.UI = {
    esc: esc, qs: qs, qsa: qsa, param: param,
    fmtDate: fmtDate, fmtDateTime: fmtDateTime, timeAgo: timeAgo, money: money,
    titleCase: titleCase, badge: badge, demoTag: demoTag,
    domainLabel: domainLabel, setDomainLabels: setDomainLabels,
    emptyState: emptyState, loading: loading, errorState: errorState,
    toast: toast, setBusy: setBusy, field: field, formValues: formValues,
    showFieldError: showFieldError, clearFieldErrors: clearFieldErrors,
    pagination: pagination,
    stepper: stepper, insight: insight, breakdown: breakdown, bars: bars, donut: donut,
    journey: journey, tabs: tabs, wireTabs: wireTabs, debounce: debounce,
    formatBytes: formatBytes,
  };
})();
