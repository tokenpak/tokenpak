/**
 * CountUp.js — Lightweight number animation library
 * For animating KPI values in the TokenPak Dashboard
 * 
 * Usage:
 *   const counter = new CountUp('stat-value', startValue, endValue, decimals, duration);
 *   counter.start();
 */

(function(root) {
  'use strict';

  function CountUp(target, startVal, endVal, decimals, duration) {
    this.options = {
      useEasing: true,
      easingFn: this.easeOutQuad,
      separator: ',',
      decimal: '.',
      prefix: '',
      suffix: ''
    };

    this.version = function() { return '2.1.0'; };
    this.target = typeof target === 'string' ? document.getElementById(target) : target;
    this.startVal = Number(startVal);
    this.endVal = Number(endVal);
    this.countDown = this.startVal > this.endVal;
    this.decimals = decimals || 0;
    this.duration = (duration / 1000) || 2;
    this.easingFn = this.options.easingFn;
    this.rAF = null;
    this.frameVal = this.startVal;
  }

  CountUp.prototype.determineDirectionAndSmartEasing = function() {
    var animationDirection = this.countDown ? -1 : 1;
    this.acceleration = 1.5;
    this.fps = 60;
    this.frameInterval = 1000 * animationDirection / (this.fps);
    this.totalFrameCount = Math.ceil(this.duration * this.fps);
    this.easeCounter = 0;
  };

  CountUp.prototype.easeOutQuad = function(t, b, c, d) {
    return -c *(t/=d)*(t-2) + b;
  };

  CountUp.prototype.start = function(callback) {
    if (!this.target) { console.warn('CountUp target not found'); return false; }
    
    this.callback = typeof callback === 'function' ? callback : function(){};
    this.determineDirectionAndSmartEasing();
    
    this.target.classList.add('animating');
    this.animate(new Date().getTime());
    
    return true;
  };

  CountUp.prototype.animate = function(timestamp) {
    if(!this.startTime) { this.startTime = timestamp; }
    this.timestamp = timestamp;
    var progress = (timestamp - this.startTime) / (this.duration * 1000);
    
    if (progress >= 1) {
      progress = 1;
    } else {
      progress = this.easingFn(progress, 0, 1, 1);
    }

    var frameVal = this.startVal + ((this.endVal - this.startVal) * progress);
    if (this.countDown) {
      frameVal = this.startVal - ((this.startVal - this.endVal) * progress);
    }

    this.frameVal = frameVal;
    this.render();

    if (progress < 1) {
      var self = this;
      this.rAF = requestAnimationFrame(function(newTimestamp){
        self.animate(newTimestamp);
      });
    } else {
      this.callback();
      this.target.classList.remove('animating');
    }
  };

  CountUp.prototype.render = function() {
    var displayValue = this.formatNumber(this.frameVal);
    this.target.innerHTML = displayValue;
  };

  CountUp.prototype.formatNumber = function(num) {
    var neg = (num < 0) ? "-" : "";
    var base = String(Math.abs(num).toFixed(this.decimals));
    var len = base.split(".")[0].length;
    var result = "";
    var i = 0;

    for (; i < len; i++) {
      if ((len - i) % 3 === 0 && i !== 0) {
        result += this.options.separator;
      }
      result += base[i];
    }

    if (this.decimals > 0) {
      result += this.options.decimal + base.split(".")[1];
    }

    return neg + this.options.prefix + result + this.options.suffix;
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = CountUp;
  }
  
  root.CountUp = CountUp;
})(typeof window !== 'undefined' ? window : global);
