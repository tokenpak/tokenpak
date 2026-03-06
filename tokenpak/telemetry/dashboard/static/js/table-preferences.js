/**
 * TokenPak Dashboard — Table Column Preferences & Compact Mode
 *
 * Features:
 *   - Column show/hide with picker dropdown (min 3 visible enforced)
 *   - Column resize via drag handles (80px–500px, double-click to reset)
 *   - Compact mode toggle (keyboard shortcut: D)
 *   - Persistent preferences via localStorage (key: tokenpak_table_prefs)
 *   - Responsive: auto-hide secondary columns on < 900px
 *
 * Usage: included in base.html; auto-initialises on DOMContentLoaded.
 */
(function (global) {
  "use strict";

  // ── Constants ─────────────────────────────────────────────────────────────

  const STORAGE_KEY = "tokenpak_table_prefs";
  const MIN_VISIBLE  = 3;
  const MIN_COL_W    = 80;    // px
  const MAX_COL_W    = 500;   // px
  const COMPACT_BP   = 900;   // px — below this hide secondary cols

  /** Columns that are hidden by default on narrow viewports */
  const RESPONSIVE_HIDDEN = new Set(["qmd_tokens", "tp_tokens", "cache_tokens", "pricing_version"]);

  /** All known column ids (must match th class names like th-ts, th-provider …) */
  const ALL_COLUMNS = [
    { id: "ts",       label: "Timestamp",  group: "default"  },
    { id: "trace-id", label: "Trace ID",   group: "default"  },
    { id: "provider", label: "Provider",   group: "default"  },
    { id: "model",    label: "Model",      group: "default"  },
    { id: "agent",    label: "Agent",      group: "default"  },
    { id: "input",    label: "Raw Tok",    group: "default"  },
    { id: "output",   label: "Out Tok",    group: "default"  },
    { id: "cost",     label: "Actual $",   group: "default"  },
    { id: "savings",  label: "Savings $",  group: "default"  },
    { id: "status",   label: "Status",     group: "default"  },
    { id: "latency",  label: "Latency",    group: "default"  },
    { id: "qmd_tokens",       label: "QMD Tokens",    group: "advanced" },
    { id: "tp_tokens",        label: "TP Tokens",     group: "advanced" },
    { id: "cache_tokens",     label: "Cache Tokens",  group: "advanced" },
    { id: "pricing_version",  label: "Pricing Ver",   group: "advanced" },
  ];

  const DEFAULT_PREFS = {
    visible: Object.fromEntries(ALL_COLUMNS.map(c => [c.id, true])),
    widths:  {},
    compact: false,
  };

  // ── State ─────────────────────────────────────────────────────────────────

  let prefs = loadPrefs();
  let pickerOpen = false;

  // ── Persistence ──────────────────────────────────────────────────────────

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return JSON.parse(JSON.stringify(DEFAULT_PREFS));
      const saved = JSON.parse(raw);
      // Merge defaults so new columns added later still appear
      return {
        visible: Object.assign({}, DEFAULT_PREFS.visible, saved.visible || {}),
        widths:  saved.widths  || {},
        compact: !!saved.compact,
      };
    } catch (e) {
      return JSON.parse(JSON.stringify(DEFAULT_PREFS));
    }
  }

  function savePrefs() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch (_) {}
  }

  function resetPrefs() {
    prefs = JSON.parse(JSON.stringify(DEFAULT_PREFS));
    savePrefs();
    applyAll();
    renderPicker();
  }

  // ── Apply Preferences ─────────────────────────────────────────────────────

  function applyAll() {
    applyColumnVisibility();
    applyColumnWidths();
    applyCompactMode();
  }

  function applyColumnVisibility() {
    const narrow = window.innerWidth < COMPACT_BP;
    ALL_COLUMNS.forEach(({ id }) => {
      const visible = prefs.visible[id] &&
                      !(narrow && RESPONSIVE_HIDDEN.has(id));
      const els = document.querySelectorAll(
        `.th-${id}, .td-${id}, [data-col="${id}"]`
      );
      els.forEach(el => {
        el.style.display = visible ? "" : "none";
      });
    });
  }

  function applyColumnWidths() {
    Object.entries(prefs.widths).forEach(([id, w]) => {
      document.querySelectorAll(`.th-${id}`).forEach(th => {
        th.style.width = w + "px";
        th.style.minWidth = w + "px";
      });
    });
  }

  function applyCompactMode() {
    const table = document.querySelector(".trace-table, table.audit-table, #trace-table");
    if (!table) return;
    if (prefs.compact) {
      table.classList.add("tp-table--compact");
    } else {
      table.classList.remove("tp-table--compact");
    }
    const btn = document.querySelector("[data-compact-toggle]");
    if (btn) {
      btn.setAttribute("aria-pressed", prefs.compact ? "true" : "false");
      btn.title = prefs.compact ? "Compact: ON (press D)" : "Compact: OFF (press D)";
    }
  }

  // ── Column Picker UI ──────────────────────────────────────────────────────

  function buildPickerHTML() {
    const groups = ["default", "advanced"];
    const groupLabel = { default: "Columns", advanced: "Advanced" };
    let html = `
      <div id="tp-col-picker" role="dialog" aria-label="Column picker">
        <div class="tp-picker-header">
          <span>Table Columns</span>
          <button class="tp-picker-close" aria-label="Close" data-col-picker-close>✕</button>
        </div>
        <div class="tp-picker-body">`;
    groups.forEach(grp => {
      const cols = ALL_COLUMNS.filter(c => c.group === grp);
      html += `<div class="tp-picker-group"><div class="tp-picker-group-label">${groupLabel[grp]}</div>`;
      cols.forEach(({ id, label }) => {
        const checked = prefs.visible[id] ? "checked" : "";
        html += `
          <label class="tp-picker-row">
            <input type="checkbox" data-col-toggle="${id}" ${checked}> ${label}
          </label>`;
      });
      html += `</div>`;
    });
    html += `</div>
        <div class="tp-picker-footer">
          <button class="tp-picker-reset" data-col-picker-reset>Reset to default</button>
        </div>
      </div>`;
    return html;
  }

  function renderPicker() {
    const existing = document.getElementById("tp-col-picker");
    if (existing) {
      // Re-render in place
      existing.outerHTML = buildPickerHTML();
      bindPickerEvents();
    }
  }

  function openPicker(anchorEl) {
    if (pickerOpen) { closePicker(); return; }
    const wrapper = document.createElement("div");
    wrapper.id = "tp-col-picker-wrapper";
    wrapper.innerHTML = buildPickerHTML();
    document.body.appendChild(wrapper);

    // Position near anchor
    if (anchorEl) {
      const r = anchorEl.getBoundingClientRect();
      const picker = wrapper.querySelector("#tp-col-picker");
      picker.style.position = "fixed";
      picker.style.top  = (r.bottom + 4) + "px";
      picker.style.left = Math.max(8, r.left - 200) + "px";
      picker.style.zIndex = "9999";
    }

    pickerOpen = true;
    bindPickerEvents();

    // Close on outside click
    setTimeout(() => {
      document.addEventListener("click", onOutsideClick, { once: false });
    }, 10);
  }

  function closePicker() {
    const w = document.getElementById("tp-col-picker-wrapper");
    if (w) w.remove();
    pickerOpen = false;
    document.removeEventListener("click", onOutsideClick);
  }

  function onOutsideClick(e) {
    const w = document.getElementById("tp-col-picker-wrapper");
    const btn = document.querySelector("[data-col-picker-btn]");
    if (w && !w.contains(e.target) && e.target !== btn) {
      closePicker();
    }
  }

  function bindPickerEvents() {
    // Checkboxes
    document.querySelectorAll("[data-col-toggle]").forEach(cb => {
      cb.addEventListener("change", () => {
        const id = cb.dataset.colToggle;
        const visibleCount = ALL_COLUMNS.filter(c => prefs.visible[c.id]).length;
        if (!cb.checked && visibleCount <= MIN_VISIBLE) {
          cb.checked = true; // Enforce minimum
          return;
        }
        prefs.visible[id] = cb.checked;
        savePrefs();
        applyColumnVisibility();
      });
    });

    // Reset button
    const resetBtn = document.querySelector("[data-col-picker-reset]");
    if (resetBtn) resetBtn.addEventListener("click", resetPrefs);

    // Close button
    const closeBtn = document.querySelector("[data-col-picker-close]");
    if (closeBtn) closeBtn.addEventListener("click", closePicker);
  }

  // ── Column Resize ─────────────────────────────────────────────────────────

  function installResizeHandles() {
    const table = document.querySelector("table");
    if (!table) return;
    table.querySelectorAll("th").forEach(th => {
      if (th.querySelector(".tp-resize-handle")) return; // Already installed
      const handle = document.createElement("span");
      handle.className = "tp-resize-handle";
      handle.title = "Drag to resize; double-click to reset";
      th.style.position = "relative";
      th.appendChild(handle);

      // Drag to resize
      handle.addEventListener("mousedown", startResize.bind(null, th));
      // Double-click to reset width
      handle.addEventListener("dblclick", e => {
        e.stopPropagation();
        const id = getColId(th);
        if (id) {
          delete prefs.widths[id];
          savePrefs();
          th.style.width = "";
          th.style.minWidth = "";
        }
      });
    });
  }

  function getColId(th) {
    // Extract id from class like "th-ts", "th-provider"
    const m = th.className.match(/\bth-([\w-]+)\b/);
    return m ? m[1] : null;
  }

  function startResize(th, e) {
    e.preventDefault();
    const startX = e.clientX;
    const startW = th.offsetWidth;

    function onMove(ev) {
      const w = Math.min(MAX_COL_W, Math.max(MIN_COL_W, startW + ev.clientX - startX));
      th.style.width = w + "px";
      th.style.minWidth = w + "px";
    }

    function onUp(ev) {
      const w = Math.min(MAX_COL_W, Math.max(MIN_COL_W, startW + ev.clientX - startX));
      const id = getColId(th);
      if (id) {
        prefs.widths[id] = w;
        savePrefs();
      }
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup",   onUp);
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup",   onUp);
  }

  // ── Toolbar Injection ─────────────────────────────────────────────────────

  function injectToolbar() {
    const toolbar = document.querySelector(".table-toolbar, .toolbar, .filter-bar");
    if (!toolbar || document.querySelector("[data-col-picker-btn]")) return;

    // Compact toggle
    const compactBtn = document.createElement("button");
    compactBtn.className = "tp-toolbar-btn";
    compactBtn.setAttribute("data-compact-toggle", "");
    compactBtn.setAttribute("aria-pressed", prefs.compact ? "true" : "false");
    compactBtn.title = prefs.compact ? "Compact: ON (press D)" : "Compact: OFF (press D)";
    compactBtn.textContent = "⊡";
    compactBtn.addEventListener("click", () => {
      prefs.compact = !prefs.compact;
      savePrefs();
      applyCompactMode();
    });

    // Column picker button
    const pickerBtn = document.createElement("button");
    pickerBtn.className = "tp-toolbar-btn";
    pickerBtn.setAttribute("data-col-picker-btn", "");
    pickerBtn.title = "Column visibility";
    pickerBtn.textContent = "⊞";
    pickerBtn.addEventListener("click", e => {
      e.stopPropagation();
      openPicker(pickerBtn);
    });

    toolbar.appendChild(compactBtn);
    toolbar.appendChild(pickerBtn);
  }

  // ── Keyboard Shortcut ────────────────────────────────────────────────────

  function onKeyDown(e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "d" || e.key === "D") {
      prefs.compact = !prefs.compact;
      savePrefs();
      applyCompactMode();
    }
  }

  // ── HTMX re-init hook ─────────────────────────────────────────────────────

  function onHTMXSettle() {
    installResizeHandles();
    applyAll();
    injectToolbar();
  }

  // ── Bootstrap ─────────────────────────────────────────────────────────────

  function init() {
    applyAll();
    injectToolbar();
    installResizeHandles();

    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", applyColumnVisibility);

    // Re-apply after HTMX swaps (table content reload)
    document.body.addEventListener("htmx:afterSettle", onHTMXSettle);
    document.body.addEventListener("htmx:afterSwap",   onHTMXSettle);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Public API for debugging
  global.TPTablePrefs = { reset: resetPrefs, prefs: () => prefs };

}(window));
