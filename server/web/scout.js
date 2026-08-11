/* Scout -- read-only listings dashboard (E9). Plain DOM, no framework.
 *
 * Thin client over three endpoints added to the docrag stdlib server:
 *   GET /api/scout/search?q=&price_min=&price_max=&type=&jurisdiction=&top_k=
 *   GET /api/scout/stats?type=&jurisdiction=
 *   GET /api/scout/theses
 *
 * Read-only: this page never writes to listings.db. If listings.db doesn't
 * exist yet (or the `listings` package can't import), the server still
 * answers with a clean JSON {"error": "..."} -- we surface that as a
 * friendly "no listings ingested yet" banner instead of a broken page.
 */

(function () {
  "use strict";

  var els = {
    emptyBanner: document.getElementById("empty-banner"),
    form: document.getElementById("search-form"),
    q: document.getElementById("q"),
    priceMin: document.getElementById("price-min"),
    priceMax: document.getElementById("price-max"),
    type: document.getElementById("type"),
    jurisdiction: document.getElementById("jurisdiction"),
    topK: document.getElementById("top-k"),
    searchStatus: document.getElementById("search-status"),
    resultsTable: document.getElementById("results-table"),
    resultsBody: document.getElementById("results-body"),
    resultsEmpty: document.getElementById("results-empty"),
    statsStatus: document.getElementById("stats-status"),
    statsBody: document.getElementById("stats-body"),
    thesesStatus: document.getElementById("theses-status"),
    thesesBody: document.getElementById("theses-body"),
  };

  // ---------- utils ----------

  function el(tag, opts, children) {
    var node = document.createElement(tag);
    if (opts) {
      if (opts.className) node.className = opts.className;
      if (opts.text !== undefined) node.textContent = opts.text;
      if (opts.html !== undefined) node.innerHTML = opts.html;
      if (opts.attrs) for (var k in opts.attrs)
        if (Object.prototype.hasOwnProperty.call(opts.attrs, k))
          node.setAttribute(k, opts.attrs[k]);
    }
    if (children) for (var i = 0; i < children.length; i++)
      if (children[i] != null) node.appendChild(children[i]);
    return node;
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtMoney(v) {
    if (v === null || v === undefined) return "?";
    return "$" + Math.round(v).toLocaleString();
  }

  function fmtNum(v, digits) {
    if (v === null || v === undefined) return "?";
    return Number(v).toFixed(digits === undefined ? 0 : digits);
  }

  function fmtPct(v) {
    if (v === null || v === undefined) return "?";
    return (v * 100).toFixed(1) + "%";
  }

  async function getJson(url) {
    var r = await fetch(url);
    var data = null;
    try { data = await r.json(); } catch (e) { /* leave null */ }
    if (!r.ok && !(data && "error" in data)) {
      throw new Error("HTTP " + r.status);
    }
    return data || {};
  }

  function currentFilters() {
    var out = {};
    if (els.priceMin.value) out.price_min = els.priceMin.value;
    if (els.priceMax.value) out.price_max = els.priceMax.value;
    if (els.type.value.trim()) out.type = els.type.value.trim();
    if (els.jurisdiction.value.trim()) out.jurisdiction = els.jurisdiction.value.trim();
    return out;
  }

  function showEmptyBanner(show) {
    els.emptyBanner.hidden = !show;
  }

  // ---------- results ----------

  function buildabilityBadge(row) {
    // Buildability isn't guaranteed to be joined onto a search hit today (it
    // lives in the enrichment table, keyed by listing_id) -- render it only
    // when a result actually carries it, so this stays forward-compatible
    // without pretending to know something we don't.
    var cited = row.buildability_cited || (row.buildability_json && row.buildability_json.cited);
    var verdict = row.buildability_json && row.buildability_json.verdict;
    if (!cited && verdict === undefined) return null;
    var cls = "build-unknown";
    var label = "unclear";
    if (verdict === true || verdict === "yes") { cls = "build-yes"; label = "by-right"; }
    else if (verdict === false || verdict === "no") { cls = "build-no"; label = "not by-right"; }
    var span = el("span", { className: "badge " + cls, text: label });
    if (cited) span.title = String(cited);
    return span;
  }

  function renderResults(rows) {
    els.resultsBody.innerHTML = "";
    if (!rows || !rows.length) {
      els.resultsTable.hidden = true;
      els.resultsEmpty.hidden = false;
      return;
    }
    els.resultsEmpty.hidden = true;
    els.resultsTable.hidden = false;
    rows.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", { className: "addr", text: r.address || "(no address)" }));
      tr.appendChild(el("td", { className: "price", text: fmtMoney(r.price) }));
      tr.appendChild(el("td", { text: r.property_type || "?" }));
      tr.appendChild(el("td", { text: r.planning_jurisdiction || "?" }));
      tr.appendChild(el("td", { className: "score", text: r._score != null ? fmtNum(r._score, 4) : "-" }));
      var buildTd = el("td");
      var badge = buildabilityBadge(r);
      if (badge) buildTd.appendChild(badge); else buildTd.textContent = "-";
      tr.appendChild(buildTd);
      tr.appendChild(el("td", { className: "snippet",
        text: r._snippet || (r.description_text || "").slice(0, 200) || "" }));
      els.resultsBody.appendChild(tr);
    });
  }

  async function runSearch() {
    var query = els.q.value.trim();
    var filters = currentFilters();
    var topK = parseInt(els.topK.value, 10) || 20;
    var qs = new URLSearchParams(filters);
    qs.set("q", query);
    qs.set("top_k", String(topK));

    els.searchStatus.textContent = "Searching…";
    els.searchStatus.classList.remove("error");
    try {
      var data = await getJson("/api/scout/search?" + qs.toString());
      if (data.error) {
        showEmptyBanner(true);
        els.searchStatus.textContent = "";
        renderResults([]);
        return;
      }
      showEmptyBanner(false);
      renderResults(data.results || []);
      els.searchStatus.textContent = (data.results || []).length + " result(s).";
    } catch (e) {
      els.searchStatus.textContent = "Search failed: " + e.message;
      els.searchStatus.classList.add("error");
      renderResults([]);
    }
  }

  // ---------- stats ----------

  function statTile(label, value, sub) {
    var children = [
      el("div", { className: "stat-label", text: label }),
      el("div", { className: "stat-value", text: value }),
    ];
    if (sub) children.push(el("div", { className: "stat-sub", text: sub }));
    return el("div", { className: "stat-tile" }, children);
  }

  function renderBreakdown(title, obj, priceKey) {
    var keys = Object.keys(obj || {}).sort();
    if (!keys.length) return null;
    var rows = keys.map(function (k) {
      var v = obj[k];
      var tr = el("tr");
      tr.appendChild(el("td", { text: k }));
      var val = priceKey ? fmtMoney(v[priceKey]) : String(v.count != null ? v.count : v);
      tr.appendChild(el("td", { text: val + (v.count != null && priceKey ? " (n=" + v.count + ")" : "") }));
      return tr;
    });
    var table = el("table", null, rows);
    return el("div", { className: "breakdown" }, [el("h3", { text: title }), table]);
  }

  function renderStats(stats) {
    els.statsBody.innerHTML = "";
    if (!stats || !stats.count) {
      els.statsBody.appendChild(el("div", { className: "empty-state", text: "No listings match these filters yet." }));
      return;
    }
    var price = stats.price || {};
    var psf = stats.price_per_sqft || {};
    var dom = stats.dom || {};
    var drop = stats.price_drop_rate || {};

    var tiles = el("div", { className: "stats-grid" }, [
      statTile("Listings", String(stats.count)),
      statTile("Median price", fmtMoney(price.median), "range " + fmtMoney(price.min) + " – " + fmtMoney(price.max)),
      statTile("Median $/sqft", psf.median != null ? "$" + fmtNum(psf.median, 0) : "?"),
      statTile("Median DOM", dom.median != null ? fmtNum(dom.median, 0) + "d" : "?"),
      statTile("Price-drop rate", fmtPct(drop.rate), drop.median_drop_pct != null
        ? "median drop " + fmtNum(drop.median_drop_pct, 1) + "%" : ""),
      statTile("Active listings", stats.inventory ? String(stats.inventory.total_active) : "?"),
    ]);
    els.statsBody.appendChild(tiles);

    var breakdowns = el("div", { className: "breakdown-tables" });
    var byType = renderBreakdown("By property type", stats.by_property_type, "price_median");
    var byJur = renderBreakdown("By jurisdiction", stats.by_jurisdiction, "median_price");
    if (byType) breakdowns.appendChild(byType);
    if (byJur) breakdowns.appendChild(byJur);
    if (byType || byJur) els.statsBody.appendChild(breakdowns);
  }

  async function loadStats() {
    var filters = currentFilters();
    delete filters.price_min;
    delete filters.price_max;
    var qs = new URLSearchParams(filters);
    els.statsStatus.textContent = "Loading…";
    els.statsStatus.classList.remove("error");
    try {
      var data = await getJson("/api/scout/stats?" + qs.toString());
      if (data.error) {
        showEmptyBanner(true);
        els.statsStatus.textContent = "";
        renderStats(null);
        return;
      }
      els.statsStatus.textContent = "";
      renderStats(data);
    } catch (e) {
      els.statsStatus.textContent = "Stats failed: " + e.message;
      els.statsStatus.classList.add("error");
      renderStats(null);
    }
  }

  // ---------- theses ----------

  function renderTheses(theses) {
    els.thesesBody.innerHTML = "";
    if (!theses || !theses.length) {
      els.thesesBody.appendChild(el("div", { className: "empty-state",
        text: "No saved theses yet -- add one with: listings thesis add --name ... --spec-file ..." }));
      return;
    }
    theses.forEach(function (t) {
      var head = el("div", { className: "thesis-head" }, [
        el("span", { className: "thesis-name", text: t.name || ("#" + t.id) }),
        el("span", { className: "thesis-meta", text: "last run: " + (t.last_run || "never") }),
      ]);
      var spec = el("div", { className: "thesis-spec",
        text: t.spec_json ? JSON.stringify(t.spec_json) : "" });
      var card = el("div", { className: "thesis-card" }, [head, spec]);

      var hits = t.hits || [];
      if (hits.length) {
        var hitsWrap = el("div", { className: "thesis-hits" });
        hits.slice(0, 10).forEach(function (h) {
          hitsWrap.appendChild(el("div", { className: "hit",
            html: "<strong>listing #" + escapeHtml(h.listing_id) + "</strong> &mdash; "
              + escapeHtml(h.rationale || "(no rationale)")
              + " <span style=\"color:var(--text-mute)\">(" + escapeHtml(h.first_alerted || "") + ")</span>" }));
        });
        card.appendChild(hitsWrap);
      }
      els.thesesBody.appendChild(card);
    });
  }

  async function loadTheses() {
    els.thesesStatus.textContent = "Loading…";
    els.thesesStatus.classList.remove("error");
    try {
      var data = await getJson("/api/scout/theses");
      if (data.error) {
        els.thesesStatus.textContent = "";
        renderTheses([]);
        return;
      }
      els.thesesStatus.textContent = "";
      renderTheses(data.theses || []);
    } catch (e) {
      els.thesesStatus.textContent = "Theses failed: " + e.message;
      els.thesesStatus.classList.add("error");
      renderTheses([]);
    }
  }

  // ---------- init ----------

  els.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    runSearch();
    loadStats();
  });

  document.addEventListener("DOMContentLoaded", function () {
    loadStats();
    loadTheses();
  });
})();
