/**
 * AUCTION TRADER DASHBOARD
 * Real-time trading dashboard with WebSocket updates
 */

// ============================================================================
// STATE
// ============================================================================

const state = {
    ws: null,
    connected: false,
    priceChart: null,
    priceHistory: [],
    va: { poc: 0, vah: 0, val: 0 },
    lastPrice: 0,
    reconnectAttempts: 0,
    maxReconnectAttempts: 10,
};

// ============================================================================
// UTILITIES
// ============================================================================

function formatPrice(price) {
    return price.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function formatTime(ts) {
    const date = new Date(ts);
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    });
}

function formatShortTime(ts) {
    const date = new Date(ts);
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    });
}

// ============================================================================
// CHART
// ============================================================================

function initChart() {
    const ctx = document.getElementById('priceChart').getContext('2d');

    // Chart.js configuration
    state.priceChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                {
                    label: 'Price',
                    data: [],
                    borderColor: '#e8e8ec',
                    backgroundColor: 'rgba(232, 232, 236, 0.05)',
                    borderWidth: 1.5,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointHoverBackgroundColor: '#00ffd5',
                },
                {
                    label: 'VAH',
                    data: [],
                    borderColor: '#ff9f43',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false,
                },
                {
                    label: 'POC',
                    data: [],
                    borderColor: '#ffd700',
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: false,
                },
                {
                    label: 'VAL',
                    data: [],
                    borderColor: '#54a0ff',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 0,
            },
            interaction: {
                intersect: false,
                mode: 'index',
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'minute',
                        displayFormats: {
                            minute: 'HH:mm',
                        },
                    },
                    grid: {
                        color: 'rgba(255, 255, 255, 0.03)',
                        drawBorder: false,
                    },
                    ticks: {
                        color: '#4a4a5a',
                        font: {
                            family: 'JetBrains Mono',
                            size: 10,
                        },
                        maxTicksLimit: 10,
                    },
                },
                y: {
                    position: 'right',
                    grid: {
                        color: 'rgba(255, 255, 255, 0.03)',
                        drawBorder: false,
                    },
                    ticks: {
                        color: '#4a4a5a',
                        font: {
                            family: 'JetBrains Mono',
                            size: 10,
                        },
                        callback: (value) => formatPrice(value),
                    },
                },
            },
            plugins: {
                legend: {
                    display: false,
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 16, 22, 0.95)',
                    titleColor: '#8a8a9a',
                    bodyColor: '#e8e8ec',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    titleFont: {
                        family: 'JetBrains Mono',
                        size: 10,
                    },
                    bodyFont: {
                        family: 'Orbitron',
                        size: 12,
                    },
                    padding: 12,
                    displayColors: false,
                    callbacks: {
                        label: (context) => {
                            if (context.datasetIndex === 0) {
                                return `$${formatPrice(context.parsed.y)}`;
                            }
                            return `${context.dataset.label}: $${formatPrice(context.parsed.y)}`;
                        },
                    },
                },
            },
        },
    });
}

function updateChart(priceHistory, va) {
    if (!state.priceChart) return;

    const chart = state.priceChart;

    // Price data
    const priceData = priceHistory.map(p => ({
        x: p.ts,
        y: p.price,
    }));

    // VA lines (constant across time range)
    const timeRange = priceHistory.length > 0
        ? [priceHistory[0].ts, priceHistory[priceHistory.length - 1].ts]
        : [Date.now() - 60000, Date.now()];

    const vahData = [
        { x: timeRange[0], y: va.vah },
        { x: timeRange[1], y: va.vah },
    ];
    const pocData = [
        { x: timeRange[0], y: va.poc },
        { x: timeRange[1], y: va.poc },
    ];
    const valData = [
        { x: timeRange[0], y: va.val },
        { x: timeRange[1], y: va.val },
    ];

    // Update datasets
    chart.data.datasets[0].data = priceData;
    chart.data.datasets[1].data = vahData;
    chart.data.datasets[2].data = pocData;
    chart.data.datasets[3].data = valData;

    chart.update('none');
}

// ============================================================================
// UI UPDATES
// ============================================================================

function updateTimestamp() {
    document.getElementById('timestamp').textContent = formatTime(Date.now());
}

function updateConnectionStatus(connected) {
    const el = document.getElementById('connection-status');
    const dot = el.querySelector('.status-dot');
    const text = el.querySelector('span:last-child');

    if (connected) {
        dot.classList.remove('disconnected');
        dot.classList.add('connected');
        text.textContent = 'CONNECTED';
    } else {
        dot.classList.remove('connected');
        dot.classList.add('disconnected');
        text.textContent = 'DISCONNECTED';
    }
}

function updatePrice(price) {
    const priceEl = document.getElementById('current-price');
    const changeEl = document.getElementById('price-change');

    priceEl.textContent = formatPrice(price);

    if (state.lastPrice > 0) {
        const change = ((price - state.lastPrice) / state.lastPrice) * 100;
        const arrow = change >= 0 ? '\u25B2' : '\u25BC';

        changeEl.querySelector('.change-arrow').textContent = arrow;
        changeEl.querySelector('.change-value').textContent = `${Math.abs(change).toFixed(3)}%`;

        changeEl.classList.remove('up', 'down');
        changeEl.classList.add(change >= 0 ? 'up' : 'down');
    }

    state.lastPrice = price;
}

function updateValueArea(va) {
    document.getElementById('va-vah').textContent = formatPrice(va.vah);
    document.getElementById('va-poc').textContent = formatPrice(va.poc);
    document.getElementById('va-val').textContent = formatPrice(va.val);
    state.va = va;
}

function updateOrderFlow(of) {
    const total = of.buy_volume + of.sell_volume;
    const buyPct = total > 0 ? (of.buy_volume / total) * 100 : 50;
    const sellPct = total > 0 ? (of.sell_volume / total) * 100 : 50;

    document.getElementById('of-bar-buy').style.width = `${buyPct}%`;
    document.getElementById('of-bar-sell').style.width = `${sellPct}%`;

    document.getElementById('of-buy').textContent = of.buy_volume.toFixed(1);
    document.getElementById('of-sell').textContent = of.sell_volume.toFixed(1);
    document.getElementById('of-delta').textContent = of.of_1m.toFixed(1);
}

function updatePosition(pos) {
    const container = document.getElementById('position-display');

    if (!pos.side) {
        container.innerHTML = `
            <div class="no-position">
                <span class="no-position-icon">&#8709;</span>
                <span>NO ACTIVE POSITION</span>
            </div>
        `;
        return;
    }

    const isLong = pos.side === 'LONG';
    const pnlClass = pos.unrealized_pnl >= 0 ? 'profit' : 'loss';

    container.innerHTML = `
        <div class="position-active">
            <div class="position-side ${isLong ? 'long' : 'short'}">
                ${pos.side}
            </div>
            <div class="position-details">
                <div class="position-detail">
                    <span class="position-detail-label">ENTRY</span>
                    <span>$${formatPrice(pos.entry_price)}</span>
                </div>
                <div class="position-detail">
                    <span class="position-detail-label">SIZE</span>
                    <span>${pos.size.toFixed(4)}</span>
                </div>
                <div class="position-detail">
                    <span class="position-detail-label">STOP</span>
                    <span>$${formatPrice(pos.stop_price)}</span>
                </div>
                <div class="position-detail">
                    <span class="position-detail-label">TP1</span>
                    <span>$${formatPrice(pos.tp1_price)}</span>
                </div>
            </div>
            <div class="position-pnl ${pnlClass}">
                ${pos.unrealized_pnl >= 0 ? '+' : ''}$${pos.unrealized_pnl.toFixed(2)}
            </div>
        </div>
    `;
}

function updateStats(stats) {
    const pnlEl = document.getElementById('stat-pnl');
    pnlEl.textContent = `$${stats.total_pnl.toFixed(2)}`;
    pnlEl.classList.remove('profit', 'loss');
    pnlEl.classList.add(stats.total_pnl >= 0 ? 'profit' : 'loss');

    document.getElementById('stat-winrate').textContent = `${(stats.win_rate * 100).toFixed(1)}%`;
    document.getElementById('stat-trades').textContent = stats.total_trades;
    document.getElementById('stat-drawdown').textContent = `${(stats.max_drawdown * 100).toFixed(1)}%`;
}

function updateSignals(signals) {
    const container = document.getElementById('signals-list');
    const countEl = document.getElementById('signal-count');

    countEl.textContent = signals.length;

    if (signals.length === 0) {
        container.innerHTML = `
            <div class="signal-empty">
                <span>AWAITING SIGNALS...</span>
            </div>
        `;
        return;
    }

    // Reverse to show newest first
    const reversed = [...signals].reverse();

    container.innerHTML = reversed.map(signal => {
        const isLong = signal.signal_type.includes('LONG');
        return `
            <div class="signal-item ${isLong ? 'long' : 'short'}">
                <span class="signal-time">${formatShortTime(signal.ts)}</span>
                <span class="signal-type">${signal.signal_type.replace(/_/g, ' ')}</span>
                <span class="signal-price">$${formatPrice(signal.price)}</span>
                <span class="signal-reason">${signal.reason}</span>
            </div>
        `;
    }).join('');
}

// ============================================================================
// WEBSOCKET
// ============================================================================

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    console.log(`Connecting to ${wsUrl}...`);

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        console.log('WebSocket connected');
        state.connected = true;
        state.reconnectAttempts = 0;
        updateConnectionStatus(true);
    };

    state.ws.onclose = () => {
        console.log('WebSocket disconnected');
        state.connected = false;
        updateConnectionStatus(false);
        scheduleReconnect();
    };

    state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    state.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleUpdate(data);
        } catch (e) {
            console.error('Failed to parse message:', e);
        }
    };
}

function scheduleReconnect() {
    if (state.reconnectAttempts >= state.maxReconnectAttempts) {
        console.log('Max reconnect attempts reached');
        return;
    }

    state.reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 30000);

    console.log(`Reconnecting in ${delay}ms (attempt ${state.reconnectAttempts})`);

    setTimeout(() => {
        if (!state.connected) {
            connectWebSocket();
        }
    }, delay);
}

function handleUpdate(data) {
    // Update all components
    updatePrice(data.price);
    updateValueArea(data.va);
    updateOrderFlow(data.order_flow);
    updatePosition(data.position);
    updateStats(data.stats);
    updateSignals(data.recent_signals);

    // Update chart
    if (data.price_history && data.price_history.length > 0) {
        state.priceHistory = data.price_history;
        updateChart(data.price_history, data.va);
    }
}

// ============================================================================
// INITIALIZATION
// ============================================================================

function init() {
    console.log('Initializing Auction Trader Dashboard...');

    // Initialize chart
    initChart();

    // Start timestamp updates
    updateTimestamp();
    setInterval(updateTimestamp, 1000);

    // Connect to WebSocket
    connectWebSocket();

    // Initial data fetch
    fetchInitialData();
}

async function fetchInitialData() {
    try {
        const response = await fetch('/api/state');
        const data = await response.json();
        handleUpdate(data);
    } catch (e) {
        console.error('Failed to fetch initial data:', e);
    }
}

// Start when DOM is ready
document.addEventListener('DOMContentLoaded', init);
