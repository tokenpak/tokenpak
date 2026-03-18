/**
 * TokenPak Dashboard — Motion Choreography Orchestration
 * Handles dynamic animation triggers for filters, charts, drilldowns, and mode switches
 * Philosophy: Users feel like they're "zooming into data", not switching pages
 */

const MotionChoreography = {
  // Track animation state to prevent overlapping/janky transitions
  animatingElements: new Set(),
  filterChangeTimeout: null,
  lastFilterChangeTime: 0,
  
  /**
   * 1. NUMBER VALUE TRANSITIONS
   * Animate KPI/metric numbers when values change (typically via HTMX swap)
   */
  initNumberTransitions() {
    // Observer to detect number value changes
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === 'childList' || mutation.type === 'characterData') {
          const target = mutation.target.closest('.kpi-value, .metric-number, [data-animate-number]');
          if (target && !this.animatingElements.has(target)) {
            this.animateNumberChange(target);
          }
        }
      });
    });

    // Observe all KPI and metric elements
    document.querySelectorAll('.kpi-value, .metric-number, [data-animate-number]').forEach((el) => {
      observer.observe(el, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    });
  },

  animateNumberChange(element) {
    if (this.animatingElements.has(element)) return;
    this.animatingElements.add(element);

    // Pulse animation to indicate change
    element.classList.add('animating');
    
    // Ensure tabular numerals for consistent width (already in CSS, but enforce)
    element.style.fontVariantNumeric = 'tabular-nums';

    // Clear animation class after duration
    const duration = parseInt(
      window.getComputedStyle(element).getPropertyValue('--duration-base') || '300'
    );
    setTimeout(() => {
      element.classList.remove('animating');
      this.animatingElements.delete(element);
    }, duration + 50);
  },

  /**
   * 2. CHART TRANSITIONS
   * Fade out old chart, fade in new chart during HTMX swap
   */
  initChartTransitions() {
    // Hook into HTMX swap events to detect chart updates
    document.addEventListener('htmx:beforeSwap', (event) => {
      const charts = event.detail.xhr.responseText.includes('canvas') ||
                    event.detail.xhr.responseText.includes('chartjs') ||
                    event.detail.xhr.responseText.includes('chart-');
      
      if (charts && event.detail.target) {
        // Fade out existing charts before swap
        const existingCharts = event.detail.target.querySelectorAll('.chart-container');
        existingCharts.forEach((chart) => {
          chart.classList.add('chart-fade-out');
        });
      }
    });

    // After swap completes, fade in new charts
    document.addEventListener('htmx:afterSwap', (event) => {
      const newCharts = event.detail.target.querySelectorAll('.chart-container');
      newCharts.forEach((chart) => {
        // Remove old fade-out, apply fade-in
        chart.classList.remove('chart-fade-out');
        chart.classList.add('chart-fade-in');
        
        // Remove animation class after duration
        setTimeout(() => {
          chart.classList.remove('chart-fade-in');
        }, 200 + 50);
      });
    });
  },

  /**
   * 3. FILTER CHANGE ANIMATION
   * Pulse the filter that changed, fade affected sections
   */
  initFilterChangeAnimation() {
    const filterItems = document.querySelectorAll('.filter-item, [data-filter]');
    
    filterItems.forEach((filterItem) => {
      filterItem.addEventListener('change', (event) => {
        this.handleFilterChange(filterItem);
      });

      // Support click on label/button filters
      if (filterItem.classList.contains('filter-option') || filterItem.classList.contains('filter-button')) {
        filterItem.addEventListener('click', (event) => {
          this.handleFilterChange(filterItem);
        });
      }
    });

    // Listen for filter-changed event (broadcast by filter.js)
    document.addEventListener('filter-changed', (event) => {
      this.fadeAffectedSections();
    });
  },

  handleFilterChange(filterElement) {
    const now = Date.now();
    if (now - this.lastFilterChangeTime < 100) return; // Debounce
    this.lastFilterChangeTime = now;

    // Pulse the changed filter
    filterElement.classList.add('changed');
    setTimeout(() => {
      filterElement.classList.remove('changed');
    }, 300 + 50);

    // Fade affected sections
    this.fadeAffectedSections();
  },

  fadeAffectedSections() {
    const mainContent = document.getElementById('main-content');
    if (!mainContent) return;

    // Add fade-out animation to affected areas
    mainContent.classList.add('affected-section');
    mainContent.classList.add('loading');

    // Remove classes after swap completes
    document.addEventListener('htmx:afterSwap', () => {
      mainContent.classList.remove('affected-section');
      mainContent.classList.remove('loading');
      mainContent.classList.add('loaded');
      
      setTimeout(() => {
        mainContent.classList.remove('loaded');
      }, 200 + 50);
    }, { once: true });
  },

  /**
   * 4. DRILLDOWN "ZOOM" FEEL
   * Highlight clicked chart element, show breadcrumb
   */
  initDrilldownZoom() {
    document.addEventListener('click', (event) => {
      const chartElement = event.target.closest('[data-drill], .chart-bar, .chart-line, .bar, canvas');
      if (!chartElement) return;

      // Only drill on actual data elements
      if (!chartElement.dataset.drill && !chartElement.classList.contains('chart-bar')) return;

      // Pulse the clicked element
      const canvasParent = chartElement.closest('.chart-container, [role="img"]');
      if (canvasParent) {
        canvasParent.classList.add('chart-element');
        canvasParent.classList.add('clicked');
        
        setTimeout(() => {
          canvasParent.classList.remove('clicked');
        }, 300 + 50);
      }

      // Breadcrumb appears automatically via CSS animation
      // (triggered by server-side rendering of breadcrumb element)
    });
  },

  /**
   * 5. DEPTH LEVEL TRANSITIONS
   * Manage nested views sliding in/out (L1→L2→L3→L4)
   */
  initDepthLevelTransitions() {
    // Listen for depth change events (from router/navigation)
    document.addEventListener('depth-change', (event) => {
      const { entering, exiting } = event.detail;
      
      if (exiting) {
        exiting.classList.add('exiting');
        setTimeout(() => {
          exiting.remove();
        }, 300 + 50);
      }

      if (entering) {
        entering.classList.add('depth-level', 'entering');
        setTimeout(() => {
          entering.classList.remove('entering');
        }, 300 + 50);
      }
    });

    // Manual depth-stack toggles (e.g., collapsible depth breadcrumbs)
    document.addEventListener('click', (event) => {
      const collapseBtn = event.target.closest('[data-collapse-depth]');
      if (!collapseBtn) return;

      const stack = collapseBtn.closest('.depth-stack') || document.querySelector('.depth-stack');
      if (!stack) return;

      const isExpanded = stack.classList.contains('expanded');
      if (isExpanded) {
        stack.classList.remove('expanded');
        stack.classList.add('collapsed');
      } else {
        stack.classList.remove('collapsed');
        stack.classList.add('expanded');
      }
    });
  },

  /**
   * 6. DRAWER TRANSITIONS
   * Slide drawer in/out with overlay dimming
   */
  initDrawerTransitions() {
    // Listen for drawer open/close events
    document.addEventListener('drawer-open', (event) => {
      const drawer = event.detail.drawer || event.target;
      this.openDrawer(drawer);
    });

    document.addEventListener('drawer-close', (event) => {
      const drawer = event.detail.drawer || event.target;
      this.closeDrawer(drawer);
    });

    // Auto-close drawer on overlay click
    document.addEventListener('click', (event) => {
      if (event.target.classList.contains('drawer-overlay')) {
        const drawer = event.target.nextElementSibling;
        if (drawer && drawer.classList.contains('drawer')) {
          this.closeDrawer(drawer);
        }
      }
    });

    // ESC key to close drawer
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        const openDrawer = document.querySelector('.drawer:not(.closing)');
        if (openDrawer) {
          this.closeDrawer(openDrawer);
        }
      }
    });
  },

  openDrawer(drawer) {
    if (!drawer) return;

    const overlay = drawer.previousElementSibling;
    if (overlay && overlay.classList.contains('drawer-overlay')) {
      overlay.classList.remove('closing');
    }

    drawer.classList.remove('closing');
    
    // Content fades in after slide animation
    const content = drawer.querySelector('.drawer-content');
    if (content) {
      setTimeout(() => {
        content.style.opacity = '1';
      }, 50);
    }
  },

  closeDrawer(drawer) {
    if (!drawer) return;

    drawer.classList.add('closing');
    
    const overlay = drawer.previousElementSibling;
    if (overlay && overlay.classList.contains('drawer-overlay')) {
      overlay.classList.add('closing');
    }

    // Remove drawer from DOM after animation
    setTimeout(() => {
      drawer.remove();
      if (overlay) overlay.remove();
    }, 250 + 50);
  },

  /**
   * 7. HOVER MICRO-INTERACTIONS
   * Already handled by CSS, but can enhance with JS if needed
   */
  initHoverEffects() {
    // CSS handles most hover effects via :hover pseudo-classes
    // This is for any dynamic hover scenarios that need JS

    document.addEventListener('mouseenter', (event) => {
      const card = event.target.closest('.card, .metric-card, .chart-card');
      if (card) {
        card.style.transition = 'all 150ms var(--ease-out)';
      }
    }, { capture: true });

    // Add glow to chart elements on hover
    document.addEventListener('mouseover', (event) => {
      const chartElement = event.target.closest('.chart-bar, .chart-line-point, canvas');
      if (chartElement) {
        chartElement.classList.add('hover-glow');
      }
    });

    document.addEventListener('mouseout', (event) => {
      const chartElement = event.target.closest('.chart-bar, .chart-line-point, canvas');
      if (chartElement) {
        chartElement.classList.remove('hover-glow');
      }
    });
  },

  /**
   * 8. MODE SWITCH ANIMATION
   * Animate between Basic ↔ Advanced view modes
   */
  initModeSwitchAnimation() {
    const modeToggle = document.querySelector('[data-mode-toggle], .mode-toggle-button');
    if (!modeToggle) return;

    modeToggle.addEventListener('click', (event) => {
      const currentMode = document.body.dataset.viewMode || 'basic';
      const newMode = currentMode === 'basic' ? 'advanced' : 'basic';

      // Find sections that toggle based on mode
      const modeSections = document.querySelectorAll('[data-mode="advanced"], [data-mode="basic"]');
      
      modeSections.forEach((section) => {
        if (section.dataset.mode === newMode) {
          // Show this section
          section.classList.remove('hidden');
          section.classList.add('visible');
        } else {
          // Hide this section
          section.classList.add('hidden');
          section.classList.remove('visible');
        }
      });

      // Update body attribute
      document.body.dataset.viewMode = newMode;
      
      // Persist preference
      localStorage.setItem('dashboard-view-mode', newMode);
    });
  },

  /**
   * 9 & 10. PERFORMANCE & REDUCED MOTION
   * Media query for prefers-reduced-motion is already in motion.css
   * Ensure animations use GPU-accelerated properties
   */
  optimizeForPerformance() {
    // Ensure all animated elements use GPU acceleration
    const animatedElements = document.querySelectorAll(
      '.chart-container, .depth-level, .drawer, .card, button, [data-animate-number]'
    );

    animatedElements.forEach((el) => {
      el.classList.add('gpu-accelerated');
      // transform: translate3d(0,0,0) is already in CSS via .gpu-accelerated
    });

    // Monitor for janky animations (frame drops)
    if (window.requestAnimationFrame) {
      let lastFrameTime = performance.now();
      const frameRateMeter = () => {
        const now = performance.now();
        const frameTime = now - lastFrameTime;
        
        // If frame time > 16.67ms (60fps), log performance warning
        if (frameTime > 16.67 && frameTime < 100) {
          console.debug(`[Motion] Potential frame drop: ${frameTime.toFixed(2)}ms`);
        }
        
        lastFrameTime = now;
        requestAnimationFrame(frameRateMeter);
      };
      
      // Only run meter during animations to save CPU
      document.addEventListener('htmx:xhr:loadstart', () => {
        requestAnimationFrame(frameRateMeter);
      });
      document.addEventListener('htmx:xhr:loadend', () => {
        // Meter will stop being called after load completes
      });
    }
  },

  /**
   * INITIALIZATION
   * Called on page load and after HTMX swaps
   */
  init() {
    console.log('[Motion] Initializing choreography...');
    
    this.initNumberTransitions();
    this.initChartTransitions();
    this.initFilterChangeAnimation();
    this.initDrilldownZoom();
    this.initDepthLevelTransitions();
    this.initDrawerTransitions();
    this.initHoverEffects();
    this.initModeSwitchAnimation();
    this.optimizeForPerformance();

    console.log('[Motion] Choreography ready');
  },

  // Re-initialize after HTMX swaps
  reinit() {
    console.log('[Motion] Re-initializing after swap...');
    // Note: Some initializations use event delegation and don't need reinit
    // Only re-initialize element-specific observers
    this.initNumberTransitions();
    this.initDrilldownZoom(); // Re-attach to new elements
  },
};

// Initialize on page load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    MotionChoreography.init();
  });
} else {
  MotionChoreography.init();
}

// Reinitialize after HTMX swaps
document.addEventListener('htmx:afterSwap', () => {
  MotionChoreography.reinit();
});

// Expose globally for debugging/testing
window.MotionChoreography = MotionChoreography;
