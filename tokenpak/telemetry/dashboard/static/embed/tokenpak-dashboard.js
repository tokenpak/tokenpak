/**
 * TokenPak Dashboard — Web Component
 * UMD build - works in browser, AMD, CommonJS
 */
(function(root, factory) {
  if (typeof define === 'function' && define.amd) define([], factory);
  else if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.TokenPakDashboard = factory();
}(typeof self !== 'undefined' ? self : this, function() {
  'use strict';

  const TEMPLATE = `
    <style>
      :host{display:block;min-height:400px;width:100%}
      .tp-c{background:var(--tp-bg-primary,#0f172a);color:var(--tp-text-primary,#e2e8f0);font-family:var(--tp-font,'Inter',system-ui,sans-serif);height:100%;overflow:auto}
      .tp-load{align-items:center;display:flex;justify-content:center;min-height:400px;font-size:14px}
      .tp-err{align-items:center;display:flex;flex-direction:column;gap:16px;justify-content:center;min-height:400px;padding:24px;text-align:center}
      .tp-err-t{font-size:18px;font-weight:600}
      .tp-err-m{color:var(--tp-text-muted,#94a3b8);font-size:14px}
      .tp-btn{background:var(--tp-accent,#6366f1);border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:14px;padding:8px 16px}
      .tp-btn:hover{opacity:0.9}
      iframe{border:none;height:100%;min-height:400px;width:100%}
    </style>
    <div class="tp-c"><div class="tp-load">Loading dashboard...</div></div>`;

  class TokenPakDashboard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = TEMPLATE;
      this._state = { view: 'finops', theme: 'dark', dateRange: '7d', provider: null, model: null, compact: false };
      this._msgBound = false;
    }

    static get observedAttributes() {
      return ['data-source','view','theme','date-range','provider','model','compact','auth-token'];
    }

    connectedCallback() { this._sync(); this._render(); }

    attributeChangedCallback(name, old, val) { if (old !== val) { this._sync(); this._render(); } }

    _sync() {
      this._state.dataSource = this.getAttribute('data-source');
      this._state.view = this.getAttribute('view') || 'finops';
      this._state.theme = this.getAttribute('theme') || 'dark';
      this._state.dateRange = this.getAttribute('date-range') || '7d';
      this._state.provider = this.getAttribute('provider');
      this._state.model = this.getAttribute('model');
      this._state.compact = this.hasAttribute('compact');
      this._state.authToken = this.getAttribute('auth-token');
    }

    _render() {
      const c = this.shadowRoot.querySelector('.tp-c');
      if (!this._state.dataSource) {
        c.innerHTML = '<div class="tp-err"><div class="tp-err-t">⚠ Config Error</div><div class="tp-err-m">Missing: data-source</div></div>';
        this._emit('tp-error', { error: 'Missing data-source' });
        return;
      }

      const u = new URL('/dashboard/' + this._state.view, this._state.dataSource);
      u.searchParams.set('embed', '1');
      u.searchParams.set('days', this._parseDays(this._state.dateRange));
      if (this._state.provider) u.searchParams.set('provider', this._state.provider);
      if (this._state.model) u.searchParams.set('model', this._state.model);
      if (this._state.compact) u.searchParams.set('compact', '1');

      c.innerHTML = `<iframe src="${u}" allow=""></iframe>`;
      this._setupMsg();
      setTimeout(() => this._emit('tp-ready', { state: {...this._state} }), 500);
    }

    _parseDays(r) {
      const m={'1d':1,'7d':7,'30d':30,'90d':90};
      return m[r]||7;
    }

    _setupMsg() {
      if (this._msgBound) return;
      this._msgBound = true;
      window.addEventListener('message', e => {
        if (!this._state.dataSource || !e.origin.startsWith(new URL(this._state.dataSource).origin)) return;
        if (e.data.type === 'tp-filter-change') this._emit('tp-filter-change', e.data.payload);
        if (e.data.type === 'tp-trace-click') this._emit('tp-trace-click', e.data.payload);
      });
    }

    _emit(n, d) {
      this.dispatchEvent(new CustomEvent(n, { detail: d, bubbles: true, composed: true }));
    }

    setFilters(f) {
      if (f.provider !== undefined) { this._state.provider = f.provider; this.setAttribute('provider', f.provider||''); }
      if (f.model !== undefined) { this._state.model = f.model; this.setAttribute('model', f.model||''); }
      this._render();
      return this;
    }

    refresh() { this._render(); return this; }

    setView(v) { this._state.view = v; this.setAttribute('view', v); this._render(); return this; }

    getState() { return {...this._state}; }
  }

  if (!customElements.get('tokenpak-dashboard')) customElements.define('tokenpak-dashboard', TokenPakDashboard);
  return TokenPakDashboard;
}));
