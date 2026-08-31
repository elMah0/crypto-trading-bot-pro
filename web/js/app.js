/**
 * CRYPTOBOT PRO - FRONTEND JAVASCRIPT CONTROLLER
 * Integración en tiempo real con FastAPI, Lightweight Charts y WebSockets.
 */

document.addEventListener("DOMContentLoaded", () => {
    // --- Referencias DOM ---
    const navButtons = document.querySelectorAll(".nav-item");
    const tabPanes = document.querySelectorAll(".tab-pane");
    const pageTitle = document.getElementById("page-title");

    const btnToggleBot = document.getElementById("btn-toggle-bot");
    const btnToggleText = document.getElementById("btn-toggle-text");
    const btnTriggerScan = document.getElementById("btn-trigger-scan");
    const globalStatusDot = document.getElementById("global-status-dot");
    const globalStatusText = document.getElementById("global-status-text");
    const modeBadge = document.getElementById("mode-badge");

    const kpiTotalBalance = document.getElementById("kpi-total-balance");
    const kpiFreeBalance = document.getElementById("kpi-free-balance");
    const kpiDailyPnl = document.getElementById("kpi-daily-pnl");
    const kpiDailyWinrate = document.getElementById("kpi-daily-winrate");
    const kpiOpenPositions = document.getElementById("kpi-open-positions");
    const kpiClosedToday = document.getElementById("kpi-closed-today");

    const symbolSelector = document.getElementById("symbol-selector");
    const tfButtons = document.querySelectorAll(".tf-btn");
    const currentChartPair = document.getElementById("current-chart-pair");
    const currentTimeframeTag = document.getElementById("current-timeframe-tag");

    const positionsList = document.getElementById("positions-list");
    const badgeOpenCount = document.getElementById("badge-open-count");
    const consoleLogs = document.getElementById("console-logs");
    const btnClearLogs = document.getElementById("btn-clear-logs");

    const signalsCardsGrid = document.getElementById("signals-cards-grid");
    const btnRefreshSignals = document.getElementById("btn-refresh-signals");
    const tradesTableBody = document.getElementById("trades-table-body");

    // --- Variables de Estado del Gráfico ---
    let chartInstance = null;
    let candleSeries = null;
    let smaLineSeries = null;
    let volumeSeries = null;
    let currentSelectedSymbol = "BTC/USDT";
    let currentSelectedTf = "1h";
    let isBotRunning = true;

    // ==========================================
    // 1. GESTIÓN DE TABS
    // ==========================================
    const tabTitles = {
        "dashboard-tab": "Centro de Control Algorítmico",
        "signals-tab": "Diagnóstico Técnico Multitemporal",
        "trades-tab": "Historial de Operaciones Registradas",
        "config-tab": "Configuración del Sistema y Riesgo"
    };

    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.dataset.tab;

            navButtons.forEach(b => b.classList.remove("active"));
            tabPanes.forEach(pane => pane.classList.remove("active"));

            btn.classList.add("active");
            const activePane = document.getElementById(targetTab);
            if (activePane) activePane.classList.add("active");

            if (tabTitles[targetTab]) pageTitle.textContent = tabTitles[targetTab];

            // Cargas específicas por tab
            if (targetTab === "signals-tab") fetchSignals();
            if (targetTab === "trades-tab") fetchTrades();
            if (targetTab === "config-tab") fetchConfig();
            if (targetTab === "dashboard-tab" && chartInstance) {
                setTimeout(() => {
                    chartInstance.timeScale().fitContent();
                }, 100);
            }
        });
    });

    // ==========================================
    // 2. INICIALIZACIÓN DEL GRÁFICO (TradingView Lightweight Charts)
    // ==========================================
    function initChart() {
        const container = document.getElementById("tv-chart-container");
        if (!container) return;

        container.innerHTML = "";

        chartInstance = LightweightCharts.createChart(container, {
            layout: {
                background: { color: "#151A23" },
                textColor: "#9CA3AF",
                fontSize: 12,
                fontFamily: "Inter, sans-serif"
            },
            grid: {
                vertLines: { color: "rgba(255, 255, 255, 0.05)" },
                horzLines: { color: "rgba(255, 255, 255, 0.05)" },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: "rgba(255, 255, 255, 0.08)",
            },
            timeScale: {
                borderColor: "rgba(255, 255, 255, 0.08)",
                timeVisible: true,
                secondsVisible: false,
            },
        });

        // Serie de Velas Japonesas
        candleSeries = chartInstance.addCandlestickSeries({
            upColor: "#10B981",
            downColor: "#EF4444",
            borderVisible: false,
            wickUpColor: "#10B981",
            wickDownColor: "#EF4444",
        });

        // Serie de Media Móvil (SMA 10 días)
        smaLineSeries = chartInstance.addLineSeries({
            color: "#3B82F6",
            lineWidth: 2,
            title: "SMA 10",
        });

        // Serie de Volumen
        volumeSeries = chartInstance.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: "",
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });

        // Ajuste responsivo al redimensionar ventana
        window.addEventListener("resize", () => {
            if (chartInstance && container) {
                chartInstance.applyOptions({
                    width: container.clientWidth,
                    height: container.clientHeight
                });
            }
        });

        loadChartData();
    }

    async function loadChartData() {
        if (!chartInstance) return;
        try {
            const res = await fetch(`/api/candles?symbol=${encodeURIComponent(currentSelectedSymbol)}&timeframe=${currentSelectedTf}&limit=80`);
            if (!res.ok) return;
            const data = await res.json();

            if (data.candles && data.candles.length > 0) {
                candleSeries.setData(data.candles);
                if (data.sma && data.sma.length > 0) smaLineSeries.setData(data.sma);
                if (data.volume && data.volume.length > 0) volumeSeries.setData(data.volume);
                chartInstance.timeScale().fitContent();
            }
        } catch (err) {
            console.error("Error cargando velas:", err);
        }
    }

    // Selector de Símbolo y Timeframe del Gráfico
    symbolSelector.addEventListener("change", (e) => {
        currentSelectedSymbol = e.target.value;
        currentChartPair.textContent = currentSelectedSymbol;
        loadChartData();
    });

    tfButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            tfButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentSelectedTf = btn.dataset.tf;
            currentTimeframeTag.textContent = `${currentSelectedTf.toUpperCase()} (${currentSelectedTf === '1d' ? 'Macro' : 'Micro'})`;
            loadChartData();
        });
    });

    // ==========================================
    // 3. CONSULTA DE ESTADO Y MÉTRICAS (POLLING)
    // ==========================================
    async function fetchStatus() {
        try {
            const res = await fetch("/api/status");
            if (!res.ok) return;
            const data = await res.json();

            isBotRunning = data.is_running;

            // Actualizar controles de estado
            if (isBotRunning) {
                globalStatusDot.className = "status-indicator-dot online";
                globalStatusText.textContent = "Motor Operando";
                btnToggleBot.className = "btn btn-danger";
                btnToggleText.textContent = "Pausar Bot";
                btnToggleBot.querySelector("i").className = "fa-solid fa-pause";
            } else {
                globalStatusDot.className = "status-indicator-dot paused";
                globalStatusText.textContent = "Motor Pausado";
                btnToggleBot.className = "btn btn-success";
                btnToggleText.textContent = "Reanudar Bot";
                btnToggleBot.querySelector("i").className = "fa-solid fa-play";
            }

            modeBadge.textContent = data.is_dry_run ? "SIMULACIÓN" : "REAL";

            // KPI Cards
            kpiTotalBalance.innerHTML = `${data.total_balance.toLocaleString(undefined, {minimumFractionDigits: 2})} <span class="curr">${data.currency}</span>`;
            kpiFreeBalance.textContent = `Disponible: ${data.free_balance.toLocaleString(undefined, {minimumFractionDigits: 2})} ${data.currency}`;

            const summary = data.today_summary || {};
            const pnl = summary.total_pnl || 0.0;
            kpiDailyPnl.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} ${data.currency}`;
            kpiDailyPnl.className = `kpi-value ${pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'pnl-neutral'}`;
            kpiDailyWinrate.textContent = `Win Rate: ${(summary.win_rate || 0).toFixed(1)}% (${summary.win_count || 0}W / ${summary.loss_count || 0}L)`;

            kpiOpenPositions.textContent = data.open_positions ? data.open_positions.length : 0;
            kpiClosedToday.textContent = `Cerradas hoy: ${summary.count || 0}`;

            // Renderizar Posiciones Abiertas
            renderPositions(data.open_positions || []);

        } catch (err) {
            console.error("Error consultando estado:", err);
        }
    }

    function renderPositions(positions) {
        badgeOpenCount.textContent = positions.length;

        if (positions.length === 0) {
            positionsList.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-box-open"></i>
                    <p>No hay posiciones abiertas en este momento.</p>
                    <span>El bot entrará automáticamente cuando se cumplan los filtros técnicos.</span>
                </div>
            `;
            return;
        }

        let html = "";
        positions.forEach(pos => {
            const pnlClass = pos.pnl_amount >= 0 ? "pnl-positive" : "pnl-negative";
            const pnlSign = pos.pnl_amount >= 0 ? "+" : "";
            const trailingTag = pos.trailing_active ? `<span class="badge-pro" style="background:#10B981;color:#fff;">Trailing ON</span>` : "";

            html += `
                <div class="position-item-card">
                    <div class="pos-info">
                        <div class="pos-info-header">
                            <span class="pos-symbol">${pos.symbol}</span>
                            <span class="pos-badge-buy">COMPRA</span>
                            ${trailingTag}
                        </div>
                        <div class="pos-details">
                            Entrada: <b>${pos.entry_price.toFixed(4)}</b> | Actual: <b>${pos.current_price.toFixed(4)}</b><br>
                            SL: <b>${pos.current_sl_price.toFixed(4)}</b> | TP: <b>${pos.tp_price.toFixed(4)}</b>
                        </div>
                    </div>
                    <div class="pos-pnl-box">
                        <span class="pos-pnl-val ${pnlClass}">
                            ${pnlSign}${pos.pnl_amount.toFixed(2)} USDT (${pnlSign}${pos.pnl_percent.toFixed(2)}%)
                        </span>
                        <button class="btn-close-pos" onclick="window.closePosition('${pos.symbol.replace('/', '-')}')">
                            Cerrar Posición
                        </button>
                    </div>
                </div>
            `;
        });
        positionsList.innerHTML = html;
    }

    window.closePosition = async (symEscaped) => {
        if (!confirm(`¿Estás seguro de cerrar la posición de ${symEscaped}?`)) return;
        try {
            const res = await fetch(`/api/position/close/${symEscaped}`, { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                appendLog("info", `[MANUAL] ${data.message}`);
                fetchStatus();
                loadChartData();
            } else {
                alert(`Error: ${data.detail || data.message}`);
            }
        } catch (err) {
            console.error("Error cerrando posición:", err);
        }
    };

    // ==========================================
    // 4. SEÑALES MULTITEMPORALES
    // ==========================================
    async function fetchSignals() {
        signalsCardsGrid.innerHTML = `<div class="empty-state"><i class="fa-solid fa-spinner fa-spin"></i><p>Calculando indicadores multitemporales...</p></div>`;
        try {
            const res = await fetch("/api/signals");
            if (!res.ok) return;
            const data = await res.json();

            let html = "";
            (data.signals || []).forEach(sig => {
                const isBuy = sig.action === "BUY";
                const cardClass = isBuy ? "signal-card buy-signal" : "signal-card";
                const badgeClass = isBuy ? "badge-action-buy" : "badge-action-hold";

                html += `
                    <div class="${cardClass}">
                        <div class="signal-card-header">
                            <span class="signal-pair">${sig.symbol}</span>
                            <span class="signal-action-badge ${badgeClass}">${sig.action}</span>
                        </div>
                        <div class="signal-metrics-list">
                            <div class="sig-metric-item">
                                <span class="sig-m-label">Precio Actual</span>
                                <span class="sig-m-val">${sig.current_price ? sig.current_price.toFixed(4) : '--'}</span>
                            </div>
                            <div class="sig-metric-item">
                                <span class="sig-m-label">SMA (10D Macro)</span>
                                <span class="sig-m-val">${sig.sma_10d ? sig.sma_10d.toFixed(4) : '--'}</span>
                            </div>
                            <div class="sig-metric-item">
                                <span class="sig-m-label">ADX (14D)</span>
                                <span class="sig-m-val">${sig.adx_1d ? sig.adx_1d.toFixed(1) : '--'}</span>
                            </div>
                            <div class="sig-metric-item">
                                <span class="sig-m-label">RSI (1H Micro)</span>
                                <span class="sig-m-val">${sig.rsi_1h ? sig.rsi_1h.toFixed(1) : '--'}</span>
                            </div>
                            <div class="sig-metric-item">
                                <span class="sig-m-label">Volumen 1H</span>
                                <span class="sig-m-val">${sig.current_volume_1h ? sig.current_volume_1h.toFixed(1) : '--'}</span>
                            </div>
                            <div class="sig-metric-item">
                                <span class="sig-m-label">Promedio Vol (10)</span>
                                <span class="sig-m-val">${sig.avg_volume_1h ? sig.avg_volume_1h.toFixed(1) : '--'}</span>
                            </div>
                        </div>
                        <div class="signal-reason-box">
                            <b>Diagnóstico:</b> ${sig.reason}
                        </div>
                    </div>
                `;
            });
            signalsCardsGrid.innerHTML = html;
        } catch (err) {
            console.error("Error consultando señales:", err);
            signalsCardsGrid.innerHTML = `<div class="empty-state"><p>Error al obtener señales técnicas.</p></div>`;
        }
    }

    if (btnRefreshSignals) btnRefreshSignals.addEventListener("click", fetchSignals);

    // ==========================================
    // 5. HISTORIAL DE TRADES (SQLITE)
    // ==========================================
    async function fetchTrades() {
        try {
            const res = await fetch("/api/trades?limit=50");
            if (!res.ok) return;
            const data = await res.json();
            const trades = data.trades || [];

            if (trades.length === 0) {
                tradesTableBody.innerHTML = `<tr><td colspan="9" class="text-center">No hay operaciones registradas aún.</td></tr>`;
                return;
            }

            let html = "";
            trades.forEach(t => {
                const pnl = t.pnl_amount || 0.0;
                const pnlPct = t.pnl_percent || 0.0;
                const pnlClass = pnl >= 0 ? "pnl-positive" : "pnl-negative";
                const pnlSign = pnl >= 0 ? "+" : "";

                const openTime = t.opened_at ? new Date(t.opened_at).toLocaleTimeString() : "--";
                const closeTime = t.closed_at ? new Date(t.closed_at).toLocaleTimeString() : "--";

                html += `
                    <tr>
                        <td>#${t.id}</td>
                        <td><b>${t.symbol}</b></td>
                        <td>${t.entry_price.toFixed(4)}</td>
                        <td>${t.exit_price ? t.exit_price.toFixed(4) : '--'}</td>
                        <td>${t.amount.toFixed(4)}</td>
                        <td class="${pnlClass}"><b>${pnlSign}${pnl.toFixed(2)} USDT (${pnlSign}${pnlPct.toFixed(2)}%)</b></td>
                        <td><span class="badge-pro" style="background:#1F2937;color:#E5E7EB;">${t.exit_reason || t.status}</span></td>
                        <td>${openTime}</td>
                        <td>${closeTime}</td>
                    </tr>
                `;
            });
            tradesTableBody.innerHTML = html;
        } catch (err) {
            console.error("Error obteniendo trades:", err);
        }
    }

    // ==========================================
    // 6. CONFIGURACIÓN
    // ==========================================
    async function fetchConfig() {
        try {
            const res = await fetch("/api/config");
            if (!res.ok) return;
            const cfg = await res.json();

            document.getElementById("cfg-pos-size").value = cfg.position_size_percent;
            document.getElementById("cfg-sl").value = cfg.stop_loss_percent;
            document.getElementById("cfg-tp").value = cfg.take_profit_percent;
            document.getElementById("cfg-ts-act").value = cfg.trailing_activation_profit;
            document.getElementById("cfg-ts-cb").value = cfg.trailing_callback;

            document.getElementById("cfg-mode").value = cfg.dry_run ? "Simulación (Dry-Run)" : "Dinero Real";
            document.getElementById("cfg-quote").value = cfg.quote_currency;
            document.getElementById("cfg-max-trades").value = cfg.max_concurrent_trades;
            document.getElementById("cfg-max-daily-loss").value = `${cfg.max_daily_loss_percent}%`;
            document.getElementById("cfg-interval").value = cfg.check_interval_seconds;
        } catch (err) {
            console.error("Error consultando configuración:", err);
        }
    }

    // ==========================================
    // 7. BOTONES DE ACCIÓN (START/STOP/SCAN)
    // ==========================================
    btnToggleBot.addEventListener("click", async () => {
        const endpoint = isBotRunning ? "/api/bot/stop" : "/api/bot/start";
        try {
            const res = await fetch(endpoint, { method: "POST" });
            if (res.ok) {
                fetchStatus();
            }
        } catch (err) {
            console.error("Error cambiando estado del bot:", err);
        }
    });

    btnTriggerScan.addEventListener("click", async () => {
        btnTriggerScan.disabled = true;
        btnTriggerScan.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Escaneando...`;
        try {
            const res = await fetch("/api/bot/trigger", { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                appendLog("info", `[SCAN MANUAL] ${data.message}`);
                fetchStatus();
                loadChartData();
            }
        } catch (err) {
            console.error("Error en escaneo manual:", err);
        } finally {
            btnTriggerScan.disabled = false;
            btnTriggerScan.innerHTML = `<i class="fa-solid fa-arrows-rotate"></i> <span>Escanear Ahora</span>`;
        }
    });

    // ==========================================
    // 8. WEBSOCKET LOGS EN VIVO
    // ==========================================
    function appendLog(level, message, timestamp) {
        const time = timestamp || new Date().toLocaleTimeString();
        const line = document.createElement("div");
        line.className = `log-line ${level.toLowerCase()}`;
        line.textContent = `[${time}] [${level.toUpperCase()}] ${message}`;
        consoleLogs.appendChild(line);
        consoleLogs.scrollTop = consoleLogs.scrollHeight;
    }

    btnClearLogs.addEventListener("click", () => {
        consoleLogs.innerHTML = "";
    });

    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            appendLog("success", "Conexión WebSocket establecida con el motor de trading.");
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "log" && msg.data) {
                    appendLog(msg.data.level, msg.data.message, msg.data.timestamp);
                }
            } catch (e) {
                // Mensaje en texto plano
            }
        };

        ws.onclose = () => {
            appendLog("warning", "WebSocket desconectado. Reintentando en 3s...");
            setTimeout(connectWebSocket, 3000);
        };
    }

    // ==========================================
    // 9. ARRANQUE DEL DASHBOARD
    // ==========================================
    initChart();
    fetchStatus();
    connectWebSocket();

    // Polling regular de estado y velas cada 10s
    setInterval(() => {
        fetchStatus();
        loadChartData();
    }, 10000);
});
