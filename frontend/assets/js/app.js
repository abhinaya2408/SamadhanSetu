/* ==========================================================================
   SamadhanSetu — app.js
   Renders the shared header/footer, tracks the signed-in user and the
   notification bell. Pages can await `App.ready` to know who is signed in.
   ========================================================================== */
(function () {
  "use strict";

  let user = null;
  let notifTimer = null;

  const PUBLIC_NAV = [
    { href: "/index.html", label: "Home" },
    { href: "/challenges.html", label: "Explore problems" },
    { href: "/how-it-works.html", label: "How it works" },
    { href: "/impact.html", label: "Impact" },
  ];

  // Signed-in navigation is kept small and permission-aware: everyone gets a
  // single "Dashboard" entry (there is no separate University / Faculty link).
  function memberNav(u) {
    const items = [
      { href: "/dashboard.html", label: "Dashboard" },
      { href: "/challenges.html", label: "Explore problems" },
      { href: "/projects.html", label: "Projects" },
      { href: "/competitions.html", label: "Competitions" },
      { href: "/profile.html", label: "Profile" },
    ];
    if (u && u.role === "admin") {
      items.push({ href: "/admin.html", label: "Administration" });
    }
    return items;
  }

  function roleLabel(role) {
    const map = {
      citizen: "Citizen", student: "Student",
      university_faculty: "University & Faculty", faculty: "University & Faculty",
      university_admin: "University & Faculty",
      company: "Company", admin: "Administrator",
    };
    return map[role] || role;
  }

  function capabilitiesLabel(user) {
    const caps = (user && user.capabilities) || [];
    const map = {
      challenge_reviewer: "Challenge reviewer",
      project_mentor: "Project mentor",
      university_coordinator: "University coordinator",
    };
    return caps.map(function (c) { return map[c] || c; }).join(" · ");
  }

  function initial(name) {
    return (name || "?").trim().charAt(0).toUpperCase();
  }

  function renderHeader() {
    const host = document.getElementById("site-header");
    if (!host) return;

    const links = user ? memberNav(user) : PUBLIC_NAV;
    const nav = links.map(function (item) {
      return '<a class="site-nav__link" href="' + item.href + '">' + item.label + "</a>";
    }).join("");

    let account;
    if (user) {
      const caps = capabilitiesLabel(user);
      account =
        '<button class="notif-bell" id="notif-bell" aria-label="Notifications" aria-haspopup="true">' +
        "🔔" + '<span class="notif-bell__count" id="notif-count" hidden>0</span></button>' +
        '<a class="btn btn--secondary btn--sm" href="/dashboard.html" title="' +
        escapeHtml(caps || roleLabel(user.role)) + '">' +
        '<span aria-hidden="true">' + escapeHtml(initial(user.full_name)) + "</span> Dashboard</a>" +
        '<button class="btn btn--ghost btn--sm" id="logout-btn">Sign out</button>';
    } else {
      account =
        '<a class="btn btn--ghost btn--sm" href="/login.html">Sign In</a>' +
        '<a class="btn btn--primary btn--sm" href="/register.html">Get Started</a>';
    }

    host.className = "site-header";
    host.innerHTML =
      '<div class="container container--wide site-header__inner">' +
      '<a class="brand" href="/index.html" aria-label="SamadhanSetu home">' +
      '<span class="brand__mark" aria-hidden="true">◆</span>' +
      '<span>Samadhan<span class="brand__bridge">Setu</span></span></a>' +
      '<nav class="site-nav" id="site-nav" aria-label="Main">' + nav +
      '<div class="site-nav__group">' + account + "</div></nav>" +
      '<button class="nav-toggle" id="nav-toggle" aria-label="Menu" aria-expanded="false">' +
      "<span></span><span></span><span></span></button>" +
      "</div>";

    const toggle = document.getElementById("nav-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        const open = host.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }

    const logout = document.getElementById("logout-btn");
    if (logout) {
      logout.addEventListener("click", async function () {
        try {
          await API.logout();
        } catch (e) {
          /* ignore */
        }
        window.location.href = "/index.html";
      });
    }

    const bell = document.getElementById("notif-bell");
    if (bell) bell.addEventListener("click", function () {
      window.location.href = "/dashboard.html#notifications";
    });

    // Highlight the active nav link.
    const path = window.location.pathname.replace(/\/$/, "") || "/index.html";
    UI.qsa("#site-nav .site-nav__link").forEach(function (a) {
      if (a.getAttribute("href") === path) a.classList.add("is-active");
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderFooter() {
    const host = document.getElementById("site-footer");
    if (!host) return;
    host.className = "site-footer";
    host.innerHTML =
      '<div class="container container--wide site-footer__grid">' +
      "<div>" +
      '<a class="brand" href="/index.html"><span class="brand__mark" aria-hidden="true">◆</span>' +
      '<span>Samadhan<span class="brand__bridge">Setu</span></span></a>' +
      '<p class="muted" style="font-size:var(--fs-caption);margin-top:var(--space-3);max-width:36ch">' +
      "SamadhanSetu connects a community problem a citizen describes to a student team that can " +
      "build and measure a solution — with human review at every decision.</p>" +
      "</div>" +
      "<div><h4>Explore</h4><ul>" +
      '<li><a href="/challenges.html">Explore Problems</a></li>' +
      '<li><a href="/how-it-works.html">How It Works</a></li>' +
      '<li><a href="/competitions.html">Competitions</a></li>' +
      '<li><a href="/impact.html">Impact</a></li>' +
      "</ul></div>" +
      "<div><h4>Get started</h4><ul>" +
      '<li><a href="/submit.html">Report a Problem</a></li>' +
      '<li><a href="/projects.html">Explore Projects</a></li>' +
      '<li><a href="/login.html">Sign In</a></li>' +
      '<li><a href="/register.html">Register</a></li>' +
      "</ul></div>" +
      "<div><h4>How it stays honest</h4><ul>" +
      "<li>AI assists — people decide</li>" +
      "<li>Demo data is always labelled</li>" +
      "<li>Impact states its limitations</li>" +
      "</ul></div>" +
      "</div>" +
      '<div class="container container--wide site-footer__legal">' +
      "<span>© " + new Date().getFullYear() + " SamadhanSetu. Demonstration build.</span>" +
      "<span>Not affiliated with any university or government body.</span>" +
      "</div>";
  }

  async function refreshNotifications() {
    if (!user) return;
    const bell = document.getElementById("notif-count");
    if (!bell) return;
    try {
      const res = await API.get("/api/notifications/unread-count");
      const n = res.unread || 0;
      bell.textContent = n > 99 ? "99+" : String(n);
      bell.hidden = n === 0;
    } catch (e) {
      /* ignore */
    }
  }

  // ---- Guard helpers -------------------------------------------------------

  async function requireAuth(roles) {
    if (!user) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = "/login.html?next=" + next;
      throw new Error("not-authenticated");
    }
    if (roles && roles.length && roles.indexOf(user.role) === -1) {
      UI.toast("Your role does not have access to this page.", "error");
      throw new Error("forbidden");
    }
    return user;
  }

  const ready = (async function () {
    user = await API.me();
    renderHeader();
    renderFooter();
    refreshNotifications();
    const hash = window.location.hash;
    if (hash === "#notifications") {
      const el = document.getElementById("notifications");
      if (el) el.scrollIntoView();
    }
    if (notifTimer) clearInterval(notifTimer);
    notifTimer = setInterval(refreshNotifications, 60000);
    return user;
  })();

  window.App = {
    ready: ready,
    get user() {
      return user;
    },
    set user(u) {
      user = u;
      renderHeader();
      refreshNotifications();
    },
    requireAuth: requireAuth,
    roleLabel: roleLabel,
    refreshNotifications: refreshNotifications,
    initial: initial,
    escapeHtml: escapeHtml,
  };
})();
