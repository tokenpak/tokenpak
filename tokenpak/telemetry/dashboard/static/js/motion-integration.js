/**
 * TokenPak Dashboard — Motion Integration Layer (Enhanced)
 * 
 * Orchestrates motion design across the dashboard:
 * - Filter changes trigger visual feedback + content reload animation
 * - HTMX swaps trigger KPI counting animations
 * - Chart interactions show drilldown feedback
 * - Card hovers show elevation + glow effects
 * 
 * Works with: motion.js, motion-choreography.js
 */

(function() {
  'use strict';

  const MotionIntegrationConfig = {
    filterChangeDelay: 150,
    chartLoadingDuration: 500,
    cardElevationDuration: 150,
  };

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 1. FILTER → ANIMATION ORCHESTRATION
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function onFilterChange(filterElement) {
    // Use MotionEngine if available (motion.js)
    if (window.MotionEngine && window.MotionEngine.animateFilterChange) {
      window.MotionEngine.animateFilterChange(filterElement);
    }
    // Fallback to MotionChoreography (motion-choreography.js)
    else if (window.MotionChoreography && window.MotionChoreography.triggerFilterReaction) {
      window.MotionChoreography.triggerFilterReaction(filterElement);
    }

    // Mark affected sections for fade-in animation
    document.querySelectorAll('[data-affected-by-filter]').forEach((section) => {
      section.setAttribute('data-filter-pending', 'true');
      section.classList.add('filter-updating');
    });
  }

  function setupFilterListeners() {
    // Listen for select/radio/checkbox filter changes
    document.addEventListener('change', (e) => {
      const filterControl = e.target.closest('.filter-control, .filter-select, [data-filter-item]');
      if (filterControl) {
        onFilterChange(filterControl);
      }
    });

    // Listen for chip-based filters (status, tags, etc.)
    document.addEventListener('click', (e) => {
      const filterChip = e.target.closest('.filter-chip');
      if (filterChip && filterChip.dataset.isFilter !== 'false') {
        onFilterChange(filterChip);
      }
    });

    // Listen to custom filter events
    document.addEventListener('filter:changed', (e) => {
      if (e.detail?.element) {
        onFilterChange(e.detail.element);
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 2. HTMX INTEGRATION — Trigger animations after partial swap
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function onHTMXAfterSwap(evt) {
    // Wait for DOM to settle and reflows to complete
    requestAnimationFrame(() => {
      // Start number animations on newly inserted KPI values
      if (window.MotionEngine && window.MotionEngine.initNumberAnimations) {
        window.MotionEngine.initNumberAnimations();
      }
      // Or use MotionChoreography if available
      else if (window.MotionChoreography && window.MotionChoreography.setupStatCardAnimations) {
        window.MotionChoreography.setupStatCardAnimations();
      }

      // Fade in affected sections
      document.querySelectorAll('[data-affected-by-filter][data-filter-pending="true"]').forEach((section) => {
        section.removeAttribute('data-filter-pending');
        section.classList.remove('filter-updating');
        section.classList.add('content-loaded');
        
        // Fade in animation
        setTimeout(() => {
          section.classList.remove('content-loaded');
        }, MotionIntegrationConfig.chartLoadingDuration);
      });

      // Reinitialize chart interactions
      document.querySelectorAll('[data-drilldown], .chart-bar, .chart-point').forEach((elem) => {
        elem.classList.remove('clicked', 'drilling');
      });

      // Re-setup hover animations for new content
      setupCardAnimations();
      setupHoverEffects();
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 3. CHART INTERACTION ANIMATIONS
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function setupChartAnimations() {
    document.addEventListener('click', (e) => {
      // Drilldown on chart element clicks
      const chartElement = e.target.closest('[data-drilldown], [data-chart-element], .chart-bar, .chart-point');
      if (chartElement) {
        // Use MotionChoreography if available
        if (window.MotionChoreography) {
          // Pulse effect already handled by motion-choreography.js
          chartElement.classList.add('drilling');
          setTimeout(() => {
            chartElement.classList.remove('drilling');
          }, 300);
        } else {
          // Fallback pulse effect
          chartElement.classList.add('clicked');
          setTimeout(() => {
            chartElement.classList.remove('clicked');
          }, 300);
        }
      }

      // Export/action button animations
      const actionBtn = e.target.closest('[data-action], .chart-action');
      if (actionBtn) {
        actionBtn.classList.add('executing');
        setTimeout(() => {
          actionBtn.classList.remove('executing');
        }, 500);
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 4. CARD & ELEMENT HOVER ANIMATIONS
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function setupCardAnimations() {
    document.querySelectorAll('.stat-card, .chart-container, .table-card').forEach((card) => {
      // Skip if already setup
      if (card.hasAttribute('data-hover-setup')) return;

      card.addEventListener('mouseenter', () => {
        card.classList.add('card-elevated');
      });

      card.addEventListener('mouseleave', () => {
        card.classList.remove('card-elevated');
      });

      card.setAttribute('data-hover-setup', 'true');
    });
  }

  function setupHoverEffects() {
    // Setup hover for buttons
    document.querySelectorAll('.btn, .btn-primary, .btn-secondary').forEach((btn) => {
      if (btn.hasAttribute('data-hover-setup')) return;

      btn.addEventListener('mouseenter', function() {
        this.style.willChange = 'transform';
      });

      btn.addEventListener('mouseleave', function() {
        this.style.willChange = 'auto';
      });

      btn.setAttribute('data-hover-setup', 'true');
    });

    // Setup hover for table rows
    document.querySelectorAll('tbody tr').forEach((row) => {
      if (row.hasAttribute('data-hover-setup')) return;

      row.addEventListener('mouseenter', function() {
        this.classList.add('highlighted');
      });

      row.addEventListener('mouseleave', function() {
        this.classList.remove('highlighted');
      });

      row.setAttribute('data-hover-setup', 'true');
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 5. REFRESH BUTTON ANIMATION
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function setupRefreshAnimation() {
    const refreshBtn = document.querySelector('[data-refresh-btn], #filter-refresh-btn, .refresh-btn');
    
    if (refreshBtn) {
      refreshBtn.addEventListener('click', (e) => {
        refreshBtn.classList.add('refreshing');
        
        // Trigger refresh pulse if available
        if (window.MotionChoreography && window.MotionChoreography.triggerRefresh) {
          window.MotionChoreography.triggerRefresh();
        }
      });
    }

    // Auto-remove refreshing class when HTMX finishes
    document.addEventListener('htmx:afterSwap', () => {
      if (refreshBtn) {
        refreshBtn.classList.remove('refreshing');
      }
    });

    // Also handle custom refresh events
    document.addEventListener('refresh:complete', () => {
      if (refreshBtn) {
        refreshBtn.classList.remove('refreshing');
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 6. BREADCRUMB & DEPTH NAVIGATION
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function setupBreadcrumbAnimations() {
    document.addEventListener('click', (e) => {
      const breadcrumbBtn = e.target.closest('[data-depth-navigate], .breadcrumb a');
      if (!breadcrumbBtn) return;

      const depth = breadcrumbBtn.dataset.depthNavigate || breadcrumbBtn.dataset.depth;
      if (depth !== undefined) {
        // Use global navigate function if available
        if (window.navigateToDepth) {
          window.navigateToDepth(parseInt(depth));
        } else if (window.MotionEngine && window.MotionEngine.goToDepthLevel) {
          window.MotionEngine.goToDepthLevel(parseInt(depth));
        }
      }

      // Back button
      const backBtn = e.target.closest('[data-depth-back], .breadcrumb-back');
      if (backBtn) {
        if (window.goBackDepth) {
          window.goBackDepth();
        } else if (window.MotionEngine && window.MotionEngine.goBackDepthLevel) {
          window.MotionEngine.goBackDepthLevel();
        }
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 7. DRAWER INTERACTION
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function setupDrawerAnimations() {
    // Open drawer
    document.addEventListener('click', (e) => {
      const openBtn = e.target.closest('[data-open-drawer]');
      if (openBtn) {
        const drawerId = openBtn.dataset.openDrawer;
        if (window.MotionEngine && window.MotionEngine.openDrawer) {
          window.MotionEngine.openDrawer(drawerId);
        }
        
        // Or dispatch custom event for MotionChoreography
        const drawer = document.getElementById(drawerId);
        if (drawer) {
          document.dispatchEvent(new CustomEvent('drawer:open', { detail: { element: drawer } }));
        }
      }

      // Close drawer
      const closeBtn = e.target.closest('[data-close-drawer]');
      if (closeBtn) {
        const drawerId = closeBtn.dataset.closeDrawer;
        if (window.MotionEngine && window.MotionEngine.closeDrawer) {
          window.MotionEngine.closeDrawer(drawerId);
        }

        const drawer = document.getElementById(drawerId);
        if (drawer) {
          document.dispatchEvent(new CustomEvent('drawer:close', { detail: { element: drawer } }));
        }
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // 8. INITIALIZATION & CLEANUP
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  function init() {
    console.log('🎬 Motion Integration initialized');

    setupFilterListeners();
    setupChartAnimations();
    setupCardAnimations();
    setupHoverEffects();
    setupRefreshAnimation();
    setupBreadcrumbAnimations();
    setupDrawerAnimations();

    // Hook into HTMX lifecycle if available
    if (typeof htmx !== 'undefined') {
      document.addEventListener('htmx:afterSwap', onHTMXAfterSwap);
      document.addEventListener('htmx:afterSettle', onHTMXAfterSwap);
    }

    // Initial number animation setup
    if (window.MotionEngine && window.MotionEngine.initNumberAnimations) {
      window.MotionEngine.initNumberAnimations();
    }

    // Initialize MotionChoreography if available
    if (window.MotionChoreography) {
      window.MotionChoreography.init();
    }
  }

  // Wait for DOM to be ready, then initialize
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
  // EXPORTS: Expose to global scope
  // ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

  window.MotionIntegration = {
    onFilterChange,
    onHTMXAfterSwap,
    config: MotionIntegrationConfig,
  };
})();
