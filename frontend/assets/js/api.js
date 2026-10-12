/* ==========================================================================
   SamadhanSetu — api.js
   Tiny fetch wrapper: cookie-based sessions, CSRF header, friendly errors.
   Exposed globally as `API`.
   ========================================================================== */
(function () {
  "use strict";

  const CSRF_HEADER = "X-Requested-With";
  const CSRF_VALUE = "samadhansetu";

  function buildQuery(params) {
    if (!params) return "";
    const parts = [];
    Object.keys(params).forEach(function (k) {
      const v = params[k];
      if (v === undefined || v === null || v === "") return;
      if (Array.isArray(v)) {
        v.forEach(function (item) {
          parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(item));
        });
      } else {
        parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
      }
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  async function request(method, path, body, params) {
    const url = path + buildQuery(params);
    const headers = {};
    const opts = { method: method, credentials: "include", headers: headers };
    if (body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET" && method !== "HEAD") {
      headers[CSRF_HEADER] = CSRF_VALUE;
    }

    let res;
    try {
      res = await fetch(url, opts);
    } catch (networkErr) {
      const err = new Error("Could not reach the server. Check that the backend is running.");
      err.status = 0;
      throw err;
    }

    let data = null;
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = text;
      }
    }

    if (!res.ok) {
      const message =
        (data && data.detail) ||
        (typeof data === "string" && data) ||
        res.statusText ||
        "Request failed.";
      const err = new Error(typeof message === "string" ? message : "Request failed.");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  /**
   * Upload a file with real progress events (fetch cannot report upload progress,
   * so this uses XMLHttpRequest). Same cookie/CSRF rules as every other call.
   */
  function upload(path, file, options) {
    options = options || {};
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", path + buildQuery(options.params), true);
      xhr.withCredentials = true;
      xhr.setRequestHeader(CSRF_HEADER, CSRF_VALUE);
      if (options.timeout) xhr.timeout = options.timeout;
      if (xhr.upload && options.onProgress) {
        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable) options.onProgress(e.loaded / e.total);
        };
      }
      xhr.onload = function () {
        var data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (e) {
          data = xhr.responseText;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          if (options.onProgress) options.onProgress(1);
          resolve(data);
          return;
        }
        var message =
          (data && data.detail) || (typeof data === "string" && data) ||
          xhr.statusText || "Upload failed.";
        var err = new Error(typeof message === "string" ? message : "Upload failed.");
        err.status = xhr.status;
        err.data = data;
        reject(err);
      };
      xhr.onerror = function () {
        var err = new Error("Could not reach the server. Check that the backend is running.");
        err.status = 0;
        reject(err);
      };
      xhr.ontimeout = function () {
        var err = new Error("The upload timed out. Please try a smaller photo.");
        err.status = 0;
        reject(err);
      };
      var form = new FormData();
      form.append("file", file, file.name || "photo");
      xhr.send(form);
    });
  }

  window.API = {
    get: function (path, params) {
      return request("GET", path, null, params);
    },
    upload: upload,
    post: function (path, body, params) {
      return request("POST", path, body === undefined ? {} : body, params);
    },
    patch: function (path, body, params) {
      return request("PATCH", path, body === undefined ? {} : body, params);
    },
    del: function (path, params) {
      return request("DELETE", path, null, params);
    },
    /** Return the signed-in user, or null when anonymous. */
    me: async function () {
      try {
        return await request("GET", "/api/auth/me", null, null);
      } catch (e) {
        return null;
      }
    },
    login: function (email, password) {
      return request("POST", "/api/auth/login", { email: email, password: password }, null);
    },
    register: function (payload) {
      return request("POST", "/api/auth/register", payload, null);
    },
    logout: function () {
      return request("POST", "/api/auth/logout", {}, null);
    },

    // --- Reporting flow shortcuts (kept together so pages stay readable) ----
    reportingReference: function () {
      return request("GET", "/api/challenges/reference", null, null);
    },
    previewChallenge: function (payload) {
      return request("POST", "/api/challenges/preview", payload, { use_ai: "true" });
    },
    checkDuplicates: function (payload) {
      return request("POST", "/api/challenges/duplicates", payload, null);
    },
    uploadStaged: function (file, stagingKey, onProgress) {
      return upload("/api/challenges/attachments/staged", file, {
        params: { staging_key: stagingKey }, onProgress: onProgress, timeout: 60000,
      });
    },
    deleteStaged: function (attachmentId) {
      return request("DELETE", "/api/challenges/attachments/staged/" + attachmentId, null, null);
    },
    deleteAttachment: function (challengeId, attachmentId) {
      return request("DELETE", "/api/challenges/" + challengeId + "/attachments/" + attachmentId, null, null);
    },
    correctSummary: function (challengeId, payload) {
      return request("POST", "/api/challenges/" + challengeId + "/corrections", payload, null);
    },
  };
})();
