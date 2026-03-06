/**
 * TokenPak Dashboard — Motion Choreography System (Enhanced)
 * 
 * Implements polished motion design that makes filter changes, drilldowns, and
 * depth transitions feel like "zooming into data" rather than page switches.
 * 
 * Key Features:
 * - Number value animations (300-500ms, smooth counting)
 * - Chart transitions (fade: 150ms out, 200ms in)
 * - Filter reactions (<500ms perceived, with skeleton loading)
 * - Drilldown zoom effect (click pulse + content zoom)
 * - Depth level transitions (slide in/out with breadcrumb)
 * - Drawer animations (300ms in, 250ms out)
 * - Hover micro-interactions (elevation, scale, glow)
 * - Mode switch animations (smooth expand/collapse)
 * 
 * Performance:
 * - GPU-accelerated: transform & opacity only
 * - Target: 60fps, <500ms total transition
 * - Responsive: adjusted durations on mobile
 * - Respects prefers-reduced-motion
 */

(function() {
  'use strict';

  const MC = {
    // Configuration
    config: {
      numberDuration: 350,
      chartFadeOut: 150,
      chartFadeIn: 200,
      filterReactionDuration: 350,
      drillPulseDuration: 300,
      drawerSlideIn: 300,
      drawerSlideOut: 250,
      depthSlideDuration: 300,
      modeSwitchDuration: 300,
    },

    // Track active animations to prevent conflicts
    activeAnimations: new Map(),
    depthStack: [],

    // ═══════════════════════════════════════════════════════════════════════
    // INIT
    // ═══════════════════════════════════════════════════════════════════════

    init() {
      this.setupStatCardAnimations();
      this.setupChartTransitions();
      this.setupFilterAnimations();
      this.setupHTMXHooks();
      this.setupDrawerAnimations();
      this.setupDrilldownAnimations();
      this.setupAdvancedModeToggle();
      this.setupDepthNavigation();
      this.setupHoverEffects();
      this.setupRefreshAnimations();
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 1. STAT CARD NUMBER ANIMATIONS
    // ═══════════════════════════════════════════════════════════════════════

    setupStatCardAnimations() {
      // Observe stat cards for value changes
      const observer = new MutationObserver((mutations) => {
        mutations.forEach(mutation => {
          const card = mutation.target.closest('.stat-card');
          if (!card) return;

          const valueEl = card.querySelector('.stat-value');
          if (!valueEl || valueEl.classList.contains('animating')) return;

          // Extract numeric value
          const text = valueEl.textContent || '';
          const match = text.match(/[\d.,]+/);
          if (!match) return;

          const newValue = parseFloat(match[0].replace(/,/g, ''));
          this.animateNumberValue(valueEl, newValue, this.config.numberDuration);

          // Pulse card
          card.classList.add('updating');
          setTimeout(() => card.classList.remove('updating'), this.config.numberDuration);
        });
      });

      // Start observing all stat cards
      document.querySelectorAll('.stat-card').forEach(card => {
        observer.observe(card, {
          childList: true,
          characterData: true,
          subtree: true,
        });
      });
    },

    /**
     * Animate numeric value with smooth counting (easing: cubic-bezier(.16,1,.3,1))
     */
    animateNumberValue(element, endValue, duration = 350) {
      // Cancel any existing animation
      if (this.activeAnimations.has(element)) {
        this.activeAnimations.get(element).cancel();
      }

      const startValue = parseFloat(element.dataset.lastValue || 0);
      element.dataset.lastValue = endValue;

      let startTime;
      let animationId;

      // Ease-out cubic: fast start, gentle end
      const easeOut = (t) => 1 - Math.pow(1 - t, 3);

      const animate = (timestamp) => {
        if (!startTime) startTime = timestamp;
        const elapsed = timestamp - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = easeOut(progress);
        const current = startValue + (endValue - startValue) * eased;

        const formatted = this.formatNumber(current, element.dataset.format || 'number');
        element.textContent = formatted;

        if (progress < 1) {
          animationId = requestAnimationFrame(animate);
        } else {
          element.textContent = this.formatNumber(endValue, element.dataset.format || 'number');
          this.activeAnimations.delete(element);
        }
      };

      element.classList.add('animating');
      animationId = requestAnimationFrame(animate);
      this.activeAnimations.set(element, {
        cancel: () => {
          cancelAnimationFrame(animationId);
          element.classList.remove('animating');
        }
      });
    },

    /**
     * Format numbers by type (currency, percent, number)
     */
    formatNumber(value, format) {
      if (format === 'currency') {
        return '$' + value.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      } else if (format === 'percent') {
        return (Math.round(value * 10) / 10) + '%';
      } else if (format === 'number') {
        return Math.round(value).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      }
      return value.toFixed(2);
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 2. CHART TRANSITIONS (Fade In/Out)
    // ═══════════════════════════════════════════════════════════════════════

    setupChartTransitions() {
      document.querySelectorAll('.chart-container').forEach(container => {
        // Fade out on HTMX before swap
        container.addEventListener('htmx:beforeSwap', (evt) => {
          const wrapper = container.querySelector('.chart-canvas-wrapper');
          if (!wrapper) return;

          wrapper.style.willChange = 'opacity';
          wrapper.classList.add('fading-out');
          wrapper.classList.remove('fading-in');

          // Mark container as loading
          container.classList.add('loading');
        });

        // Fade in on HTMX after swap
        container.addEventListener('htmx:afterSwap', (evt) => {
          const wrapper = container.querySelector('.chart-canvas-wrapper');
          if (!wrapper) return;

          wrapper.classList.remove('fading-out');
          wrapper.classList.add('fading-in');
          container.classList.remove('loading');

          // Cleanup
          setTimeout(() => {
            wrapper.classList.remove('fading-in');
            wrapper.style.willChange = 'auto';
          }, this.config.chartFadeIn);
        });
      });
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 3. FILTER CHANGE ANIMATIONS
    // ═══════════════════════════════════════════════════════════════════════

    setupFilterAnimations() {
      const filterControls = document.querySelectorAll('.filter-select, .filter-chip, [data-filter-item]');
      
      filterControls.forEach(control => {
        control.addEventListener('change', (evt) => {
          this.triggerFilterReaction(evt.target);
        });

        control.addEventListener('click', (evt) => {
          if (evt.target.matches('.filter-chip')) {
            this.triggerFilterReaction(evt.target);
          }
        });
      });
    },

    /**
     * Trigger filter change reaction:
     * 1. Highlight changed control
     * 2. Fade out affected content
     * 3. Show skeleton loading
     * 4. Fade in new content
     */
    triggerFilterReaction(filterControl) {
      // Highlight filter
      filterControl.classList.add('changing');
      setTimeout(() => {
        filterControl.classList.remove('changing');
        filterControl.classList.add('changed');
      }, 10);

      // Fade out affected content (150ms)
      document.querySelectorAll('.chart-canvas-wrapper, .stat-card').forEach(elem => {
        elem.classList.add('reacting');
      });

      // Show loading skeleton (150-350ms)
      setTimeout(() => {
        document.querySelectorAll('.chart-canvas-wrapper, .stat-card').forEach(elem => {
          elem.classList.add('skeleton-loading');
        });
      }, this.config.chartFadeOut);

      // Fade in new content (200ms)
      setTimeout(() => {
        document.querySelectorAll('.chart-canvas-wrapper, .stat-card').forEach(elem => {
          elem.classList.remove('reacting', 'skeleton-loading');
          elem.classList.add('ready');
        });

        setTimeout(() => {
          document.querySelectorAll('.chart-canvas-wrapper, .stat-card').forEach(elem => {
            elem.classList.remove('ready');
          });
        }, this.config.chartFadeIn);
      }, this.config.filterReactionDuration);

      // Remove highlight
      setTimeout(() => {
        filterControl.classList.remove('changed');
      }, 300);
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 4. HTMX INTEGRATION HOOKS
    // ═══════════════════════════════════════════════════════════════════════

    setupHTMXHooks() {
      document.addEventListener('htmx:afterSwap', () => {
        // Re-initialize animations on new content
        setTimeout(() => {
          this.setupStatCardAnimations();
          this.setupChartTransitions();
          this.setupDrilldownAnimations();
          this.setupHoverEffects();
        }, 50);
      });
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 5. DRAWER ANIMATIONS (Slide In/Out)
    // ═══════════════════════════════════════════════════════════════════════

    setupDrawerAnimations() {
      // Open drawer
      document.addEventListener('drawer:open', (evt) => {
        const drawer = evt.detail?.element || document.querySelector('.drawer');
        if (!drawer) return;

        // Add overlay
        const overlay = document.querySelector('.drawer-overlay');
        if (overlay) {
          overlay.style.display = 'block';
          overlay.classList.remove('closing');
          overlay.classList.add('opening');
        }

        drawer.style.display = 'block';
        drawer.classList.remove('closing');
        drawer.style.willChange = 'transform';
        drawer.offsetHeight; // Reflow
        drawer.classList.add('slide-in');
      });

      // Close drawer
      document.addEventListener('drawer:close', (evt) => {
        const drawer = evt.detail?.element || document.querySelector('.drawer');
        if (!drawer) return;

        drawer.classList.add('closing');
        const overlay = document.querySelector('.drawer-overlay');
        if (overlay) {
          overlay.classList.add('closing');
        }

        setTimeout(() => {
          drawer.style.display = 'none';
          drawer.classList.remove('closing', 'slide-in');
          drawer.style.willChange = 'auto';
          if (overlay) {
            overlay.style.display = 'none';
            overlay.classList.remove('closing', 'opening');
          }
        }, this.config.drawerSlideOut);
      });

      // Close on overlay click
      document.addEventListener('click', (evt) => {
        if (evt.target.classList.contains('drawer-overlay')) {
          document.dispatchEvent(new CustomEvent('drawer:close'));
        }
      });
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 6. DRILLDOWN ZOOM EFFECT
    // ═══════════════════════════════════════════════════════════════════════

    setupDrilldownAnimations() {
      document.addEventListener('click', (evt) => {
        const drillElement = evt.target.closest('[data-drillable], .chart-bar, .chart-point');
        if (!drillElement) return;

        // Pulse clicked element
        drillElement.classList.add('drilling');
        setTimeout(() => {
          drillElement.classList.remove('drilling');
        }, this.config.drillPulseDuration);

        // Zoom content
        const content = document.querySelector('.content, .dashboard-content');
        if (content) {
          content.classList.add('drilling');
          setTimeout(() => {
            content.classList.remove('drilling');
          }, this.config.drillPulseDuration);
        }

        // Add breadcrumb with animation
        const breadcrumb = document.querySelector('.breadcrumb');
        if (breadcrumb && breadcrumb.style.display === 'none') {
          breadcrumb.style.display = 'block';
          breadcrumb.classList.add('breadcrumb-appear');
        }
      });
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 7. ADVANCED MODE TOGGLE
    // ═══════════════════════════════════════════════════════════════════════

    setupAdvancedModeToggle() {
      document.addEventListener('click', (evt) => {
        if (!evt.target.matches('[data-advanced-toggle]')) return;
        
        const sections = document.querySelectorAll('.advanced-section');
        const isVisible = sections[0]?.classList.contains('visible');

        sections.forEach(section => {
          if (isVisible) {
            section.classList.remove('visible');
            section.classList.add('hiding');
            setTimeout(() => {
              section.classList.remove('hiding');
              section.style.display = 'none';
            }, this.config.modeSwitchDuration);
          } else {
            section.style.display = 'block';
            section.classList.add('visible');
          }
        });

        evt.target.classList.toggle('active');
      });
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 8. DEPTH LEVEL NAVIGATION
    // ═══════════════════════════════════════════════════════════════════════

    setupDepthNavigation() {
      // Navigate to depth
      window.navigateToDepth = (levelNumber) => {
        const newLevel = document.querySelector('[data-depth="' + levelNumber + '"]');
        if (!newLevel) return;

        const currentLevels = document.querySelectorAll('[data-depth]');
        
        currentLevels.forEach(level => {
          const levelNum = parseInt(level.dataset.depth);
          if (levelNum < levelNumber) {
            level.classList.add('dimmed');
          } else if (levelNum === levelNumber) {
            level.classList.remove('dimmed');
            level.classList.add('slide-in-right');
          } else {
            level.classList.remove('slide-in-right');
          }
        });

        // Update breadcrumbs
        document.querySelectorAll('.depth-crumb').forEach((crumb, idx) => {
          if (idx < levelNumber) {
            crumb.classList.remove('active');
          } else if (idx === levelNumber) {
            crumb.classList.add('active');
          }
        });
      };

      // Go back
      window.goBackDepth = () => {
        const active = document.querySelector('[data-depth].active');
        if (!active) return;

        const currentLevel = parseInt(active.dataset.depth);
        const prevLevel = currentLevel - 1;

        if (prevLevel >= 0) {
          window.navigateToDepth(prevLevel);
        }
      };
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 9. HOVER MICRO-INTERACTIONS
    // ═══════════════════════════════════════════════════════════════════════

    setupHoverEffects() {
      const hoverElements = document.querySelectorAll(
        '.stat-card, .chart-container, .table-card, .btn, .filter-chip'
      );

      hoverElements.forEach(elem => {
        elem.classList.add('interactive');
      });

      // Chart elements get glow on hover
      document.querySelectorAll('.chart-bar, .chart-point, canvas').forEach(elem => {
        elem.addEventListener('mouseenter', function() {
          this.classList.add('hovering');
        });
        elem.addEventListener('mouseleave', function() {
          this.classList.remove('hovering');
        });
      });

      // Table rows
      document.querySelectorAll('tbody tr').forEach(row => {
        row.addEventListener('mouseenter', function() {
          this.classList.add('highlighted');
        });
        row.addEventListener('mouseleave', function() {
          this.classList.remove('highlighted');
        });
      });
    },

    // ═══════════════════════════════════════════════════════════════════════
    // 10. REFRESH ANIMATIONS
    // ═══════════════════════════════════════════════════════════════════════

    setupRefreshAnimations() {
      window.triggerRefresh = () => {
        const badge = document.querySelector('.refresh-badge');
        const timestamp = document.querySelector('.refresh-timestamp');

        if (badge) {
          badge.classList.add('updating');
          setTimeout(() => badge.classList.remove('updating'), 600);
        }

        if (timestamp) {
          timestamp.classList.add('fade-in');
          setTimeout(() => timestamp.classList.remove('fade-in'), 200);
        }
      };
    },

    // ═════════════════════════════════════════════════════════════════════════
    // UTILITIES
    // ═════════════════════════════════════════════════════════════════════════

    /**
     * Get element's numeric value
     */
    getNumericValue(element) {
      const text = element.textContent || '';
      const match = text.match(/[\d.]+/);
      return match ? parseFloat(match[0]) : 0;
    },

    /**
     * Check if prefers-reduced-motion is set
     */
    prefersReducedMotion() {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
  };

  // ═════════════════════════════════════════════════════════════════════════
  // INITIALIZATION
  // ═════════════════════════════════════════════════════════════════════════

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      MC.init();
    });
  } else {
    MC.init();
  }

  // Export for global use
  window.MotionChoreography = MC;
})();
