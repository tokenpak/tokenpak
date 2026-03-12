/**
 * TokenPak Goals Widget
 * Real-time progress tracking and milestone alerts for savings goals
 */

/**
 * Fetch goals data from the backend
 */
async function fetchGoalsData() {
    try {
        const response = await fetch('/api/goals');
        if (!response.ok) {
            console.warn('Goals API not available');
            return null;
        }
        return await response.json();
    } catch (error) {
        console.warn('Error fetching goals:', error);
        return null;
    }
}

/**
 * Format currency value
 */
function formatCurrency(value) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(value);
}

/**
 * Format percentage
 */
function formatPercent(value) {
    return `${parseFloat(value).toFixed(1)}%`;
}

/**
 * Get status badge color and text
 */
function getStatusBadge(progress) {
    if (progress.progress_percent >= 100) {
        return { class: 'completed', text: '✅ Done' };
    }
    if (progress.pace_status === 'behind') {
        return { class: 'behind', text: '⚠️ Behind' };
    }
    if (progress.pace_status === 'ahead') {
        return { class: 'ahead', text: '🚀 Ahead' };
    }
    return { class: 'on-pace', text: '▶️ On Track' };
}

/**
 * Format value based on goal type
 */
function formatGoalValue(value, goalType) {
    if (goalType === 'savings') {
        return formatCurrency(value);
    }
    return formatPercent(value);
}

/**
 * Create a goal card HTML
 */
function createGoalCard(goal, progress) {
    const progressPercent = Math.min(progress.progress_percent, 100);
    const status = getStatusBadge(progress);
    
    const html = `
        <div class="goal-card">
            <div class="goal-card-header">
                <div class="goal-name">${escapeHtml(goal.name)}</div>
                <div class="goal-status ${status.class}">${status.text}</div>
            </div>
            
            <div class="goal-progress">
                <div class="goal-progress-label">
                    <span>Progress</span>
                    <span>${formatPercent(progressPercent)}</span>
                </div>
                <div class="goal-progress-bar">
                    <div class="goal-progress-fill" style="width: ${progressPercent}%"></div>
                </div>
            </div>
            
            <div class="goal-stats">
                <div>
                    <span class="goal-stats-label">Current:</span>
                    <span class="goal-stats-value">${formatGoalValue(progress.current_value, goal.goal_type)}</span>
                </div>
                <div>
                    <span class="goal-stats-label">Target:</span>
                    <span class="goal-stats-value">${formatGoalValue(goal.target_value, goal.goal_type)}</span>
                </div>
                <div>
                    <span class="goal-stats-label">Remaining:</span>
                    <span class="goal-stats-value">${formatGoalValue(Math.max(0, goal.target_value - progress.current_value), goal.goal_type)}</span>
                </div>
            </div>
            
            <div class="goal-pace">
                <span>Pace:</span>
                <span class="pace-indicator pace-${progress.pace_status}">
                    ${progress.pace_status.replace(/_/g, ' ').toUpperCase()}
                </span>
            </div>
        </div>
    `;
    
    return html;
}

/**
 * Escape HTML special characters
 */
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;',
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

/**
 * Update goals widget
 */
async function updateGoalsWidget() {
    const goalsGrid = document.getElementById('goalsGrid');
    if (!goalsGrid) return;
    
    const data = await fetchGoalsData();
    
    if (!data || !data.goals || data.goals.length === 0) {
        goalsGrid.innerHTML = '<p class="no-goals">No goals configured. Use `tokenpak goals --add` to create one.</p>';
        return;
    }
    
    const goalsMap = {};
    data.goals.forEach(goal => {
        goalsMap[goal.goal_id] = goal;
    });
    
    let goalsHtml = '';
    for (const goal of data.goals) {
        const progress = data.progress[goal.goal_id];
        if (progress) {
            goalsHtml += createGoalCard(goal, progress);
        }
    }
    
    goalsGrid.innerHTML = goalsHtml;
}

/**
 * Initialize goals widget
 */
function initGoalsWidget() {
    // Initial load
    updateGoalsWidget();
    
    // Refresh every 10 seconds (less frequent than other metrics)
    setInterval(() => {
        updateGoalsWidget();
    }, 10000);
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGoalsWidget);
} else {
    initGoalsWidget();
}
