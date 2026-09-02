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
    let activePriceLines = [];
    let currentSelectedSymbol = "BTC/USDT";
    let currentSelectedTf = "1h";
    let isBotRunning = true;
    let latestOpenPositions = [];

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
                barSpacing: 6,
                minBarSpacing: 3,
                rightOffset: 8
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

    function clearPriceLines() {
        if (candleSeries && activePriceLines.length > 0) {
            activePriceLines.forEach(line => {
                try {
                    candleSeries.removePriceLine(line);
                } catch (e) {}
            });
            activePriceLines = [];
        }
    }

    async function loadChartData() {
        if (!chartInstance) return;
        try {
            const res = await fetch(`/api/candles?symbol=${encodeURIComponent(currentSelectedSymbol)}&timeframe=${currentSelectedTf}&limit=110`);
            if (!res.ok) return;
            const data = await res.json();

            clearPriceLines();

            if (data.candles && data.candles.length > 0) {
                candleSeries.setData(data.candles);
                if (data.sma && data.sma.length > 0) smaLineSeries.setData(data.sma);
                if (data.volume && data.volume.length > 0) volumeSeries.setData(data.volume);

                // Establecer marcas de compra / venta en el gráfico
                if (data.markers && data.markers.length > 0) {
                    candleSeries.setMarkers(data.markers);
                } else {
                    candleSeries.setMarkers([]);
                }

                // Si hay una posición abierta activa para este símbolo, dibujar líneas de precio (Entrada, SL, TP)
                const openPos = latestOpenPositions.find(p => p.symbol === currentSelectedSymbol);
                if (openPos) {
                    const entryLine = candleSeries.createPriceLine({
                        price: openPos.entry_price,
                        color: "#10B981",
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: "ENTRADA",
                    });
                    const slLine = candleSeries.createPriceLine({
                        price: openPos.current_sl_price,
                        color: "#EF4444",
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: "STOP LOSS",
                    });
                    const tpLine = candleSeries.createPriceLine({
                        price: openPos.tp_price,
                        color: "#3B82F6",
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: "TAKE PROFIT",
                    });
                    activePriceLines.push(entryLine, slLine, tpLine);
                }

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

    window.selectPositionOnChart = (symbol) => {
        currentSelectedSymbol = symbol;
        if (symbolSelector) {
            symbolSelector.value = symbol;
            // Si el símbolo no está en el selector, agregarlo temporalmente
            if (symbolSelector.value !== symbol) {
                const opt = document.createElement("option");
                opt.value = symbol;
                opt.textContent = symbol;
                symbolSelector.appendChild(opt);
                symbolSelector.value = symbol;
            }
        }
        if (currentChartPair) currentChartPair.textContent = symbol;
        loadChartData();

        // Desplazar vista al gráfico
        const chartCard = document.querySelector(".chart-section-card");
        if (chartCard) {
            chartCard.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    };

    // ==========================================
    // 3. CONSULTA DE ESTADO Y MÉTRICAS (POLLING)
    // ==========================================
    async function fetchStatus() {
        try {
            const res = await fetch("/api/status");
            if (!res.ok) return;
            const data = await res.json();

            isBotRunning = data.is_running;
            latestOpenPositions = data.open_positions || [];

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
            const allTime = data.all_time_summary || {};
            const pnl = summary.total_pnl || 0.0;
            kpiDailyPnl.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} ${data.currency}`;
            kpiDailyPnl.className = `kpi-value ${pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'pnl-neutral'}`;
            kpiDailyWinrate.textContent = `Win Rate: ${(summary.win_rate || 0).toFixed(1)}% (${summary.win_count || 0}W / ${summary.loss_count || 0}L)`;

            kpiOpenPositions.textContent = latestOpenPositions.length;
            kpiClosedToday.textContent = `Cerradas hoy: ${summary.count || 0}`;

            // Métricas de Comisiones y Riesgo
            const kpiTotalFees = document.getElementById("kpi-total-fees");
            const kpiFeePercent = document.getElementById("kpi-fee-percent");
            if (kpiTotalFees) {
                const totalFees = allTime.total_fees || summary.total_fees || 0.0;
                kpiTotalFees.textContent = `${totalFees.toFixed(2)} ${data.currency}`;
            }
            if (kpiFeePercent && data.transaction_fee_percent !== undefined) {
                kpiFeePercent.textContent = `Fee: ${data.transaction_fee_percent.toFixed(2)}% | Exchange: ${data.exchange}`;
            }

            if (data.symbols && data.symbols.length > 0 && symbolSelector) {
                const currentVal = symbolSelector.value || currentSelectedSymbol;
                const existingOptions = Array.from(symbolSelector.options).map(o => o.value);
                data.symbols.forEach(sym => {
                    if (!existingOptions.includes(sym)) {
                        const opt = document.createElement("option");
                        opt.value = sym;
                        opt.textContent = sym;
                        symbolSelector.appendChild(opt);
                    }
                });
                if (existingOptions.includes(currentVal)) {
                    symbolSelector.value = currentVal;
                }
            }

            // Renderizar Posiciones Abiertas
            renderPositions(latestOpenPositions);

            // Renderizar Operaciones en Cola / Solicitadas
            renderOperationsQueue(data.queue || []);

        } catch (err) {
            console.error("Error consultando estado:", err);
        }
    }

    function renderOperationsQueue(queue) {
        const badgeQueue = document.getElementById("badge-queue-count");
        const queueList = document.getElementById("queue-list");
        if (!queueList) return;

        if (badgeQueue) badgeQueue.textContent = queue.length;

        if (!queue || queue.length === 0) {
            queueList.innerHTML = `
                <div class="empty-state" style="padding: 14px;">
                    <i class="fa-solid fa-inbox"></i>
                    <p>No hay operaciones recientes en cola.</p>
                    <span>Las órdenes y ajustes pedidos al Copiloto AI se listarán aquí.</span>
                </div>
            `;
            return;
        }

        let html = "";
        queue.forEach(item => {
            let badgeCls = "badge-adjust";
            if (item.type === "APERTURA") badgeCls = "badge-open";
            else if (item.type.includes("CIERRE")) badgeCls = "badge-close";
            else if (item.type === "CONFIG") badgeCls = "badge-config";

            const okCls = item.status === "EJECUTADA" ? "ok" : "";

            html += `
                <div class="queue-item-card">
                    <div class="queue-item-left">
                        <span class="queue-type-badge ${badgeCls}">${item.type}</span>
                        <span class="queue-symbol">${item.symbol}</span>
                        <span class="queue-details">${item.details}</span>
                    </div>
                    <div class="queue-item-right">
                        <span class="queue-time">${item.time}</span>
                        <span class="queue-status-tag ${okCls}">${item.status}</span>
                    </div>
                </div>
            `;
        });
        queueList.innerHTML = html;
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
            const trailingTag = pos.trailing_active ? `<span class="badge-pro" style="background:#10B981;color:#fff;font-size:9px;padding:1px 4px;">TS ON</span>` : "";
            const activeHighlight = pos.symbol === currentSelectedSymbol ? 'style="border-color:#3B82F6; background: rgba(59,130,246,0.08);"' : '';

            html += `
                <div class="position-item-card" ${activeHighlight} onclick="window.selectPositionOnChart('${pos.symbol}')" title="Haz clic para ver esta posición en el gráfico">
                    <div class="pos-card-top">
                        <div class="pos-info-header-compact">
                            <span class="pos-symbol">${pos.symbol}</span>
                            <span class="pos-badge-buy">COMPRA</span>
                            ${trailingTag}
                        </div>
                        <span class="pos-pnl-val ${pnlClass}">
                            ${pnlSign}${pos.pnl_amount.toFixed(2)} (${pnlSign}${pos.pnl_percent.toFixed(2)}%)
                        </span>
                    </div>
                    <div class="pos-card-bottom">
                        <div class="pos-details-compact">
                            Ent: <b>${pos.entry_price.toFixed(2)}</b> | SL: <b style="color:#EF4444;">${pos.current_sl_price.toFixed(2)}</b> | TP: <b style="color:#3B82F6;">${pos.tp_price.toFixed(2)}</b>
                        </div>
                        <div class="pos-buttons-group">
                            <button type="button" class="btn-config-pos-compact" onclick="event.stopPropagation(); window.openPositionConfigModal('${pos.symbol}')" title="Configurar parámetros individuales">
                                <i class="fa-solid fa-gear"></i>
                            </button>
                            <button type="button" class="btn-close-pos-compact" onclick="event.stopPropagation(); window.closePosition('${pos.symbol.replace('/', '-')}')" title="Cerrar posición a mercado">
                                Cerrar
                            </button>
                        </div>
                    </div>
                </div>
            `;
        });
        positionsList.innerHTML = html;
    }

    // ==========================================
    // MODAL DE CONFIGURACIÓN AISLADA POR POSICIÓN
    // ==========================================
    window.openPositionConfigModal = (symbol) => {
        const modal = document.getElementById("position-config-modal");
        const pos = latestOpenPositions.find(p => p.symbol === symbol);
        if (!modal || !pos) return;

        document.getElementById("modal-pos-symbol").textContent = pos.symbol;
        document.getElementById("modal-pos-symbol-input").value = pos.symbol;
        document.getElementById("modal-pos-tp").value = pos.take_profit_percent || 2.0;
        document.getElementById("modal-pos-sl").value = pos.stop_loss_percent || 1.5;
        document.getElementById("modal-pos-ts-act").value = pos.trailing_activation_percent || 1.2;
        document.getElementById("modal-pos-ts-cb").value = pos.trailing_callback_percent || 0.8;
        document.getElementById("modal-pos-enable-ts").checked = pos.trailing_stop_enabled !== false;
        document.getElementById("modal-pos-enable-breakeven").checked = pos.enable_breakeven !== false;

        modal.style.display = "flex";
        modal.classList.remove("hidden");
    };

    window.closePositionConfigModal = () => {
        const modal = document.getElementById("position-config-modal");
        if (modal) {
            modal.style.display = "none";
            modal.classList.add("hidden");
        }
    };

    window.savePositionConfig = async () => {
        const sym = document.getElementById("modal-pos-symbol-input").value;
        if (!sym) return;

        const payload = {
            take_profit_percent: parseFloat(document.getElementById("modal-pos-tp").value),
            stop_loss_percent: parseFloat(document.getElementById("modal-pos-sl").value),
            trailing_activation_percent: parseFloat(document.getElementById("modal-pos-ts-act").value),
            trailing_callback_percent: parseFloat(document.getElementById("modal-pos-ts-cb").value),
            trailing_stop_enabled: document.getElementById("modal-pos-enable-ts").checked,
            enable_breakeven: document.getElementById("modal-pos-enable-breakeven").checked
        };

        const btnSave = document.getElementById("btn-save-pos-config");
        if (btnSave) {
            btnSave.disabled = true;
            btnSave.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Guardando...`;
        }

        try {
            const symSlug = sym.replace("/", "-");
            const res = await fetch(`/api/positions/${symSlug}/config`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (res.ok) {
                showToast("success", "Parámetros de Posición Guardados", `Configuración aislada para ${sym} actualizada correctamente.`);
                window.closePositionConfigModal();
                fetchStatus();
                loadChartData();
            } else {
                showToast("error", "Error", data.detail || "No se pudo actualizar la posición.");
            }
        } catch (err) {
            console.error("Error guardando posición:", err);
            showToast("error", "Error de red", "No se pudo conectar con el servidor.");
        } finally {
            if (btnSave) {
                btnSave.disabled = false;
                btnSave.innerHTML = `<i class="fa-solid fa-check"></i> Aplicar a esta Posición`;
            }
        }
    };

    window.closePosition = async (symEscaped) => {
        const rawSym = symEscaped.replace("-", "/");
        if (!confirm(`¿Estás seguro de cerrar la posición de ${rawSym}?`)) return;
        try {
            const res = await fetch(`/api/positions/${symEscaped}/close`, { method: "POST" });
            const data = await res.json();
            if (res.ok) {
                showToast("success", "Posición Cerrada", data.message || `Posición ${rawSym} cerrada con éxito.`);
                fetchStatus();
                loadChartData();
                fetchTrades();
            } else {
                showToast("error", "Error al Cerrar", data.detail || data.message);
            }
        } catch (err) {
            console.error("Error cerrando posición:", err);
            showToast("error", "Error de Conexión", "No se pudo cerrar la posición.");
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
    // 5. HISTORIAL DE TRADES (SQLITE CON COMISIONES)
    // ==========================================
    async function fetchTrades() {
        try {
            const res = await fetch("/api/trades?limit=50");
            if (!res.ok) return;
            const data = await res.json();
            const trades = data.trades || [];

            if (trades.length === 0) {
                tradesTableBody.innerHTML = `<tr><td colspan="10" class="text-center">No hay operaciones registradas aún.</td></tr>`;
                return;
            }

            let html = "";
            trades.forEach(t => {
                const pnl = t.pnl_amount || 0.0;
                const pnlPct = t.pnl_percent || 0.0;
                const pnlClass = pnl >= 0 ? "pnl-positive" : "pnl-negative";
                const pnlSign = pnl >= 0 ? "+" : "";
                const fee = t.fee || 0.0;

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
                        <td style="color:#9CA3AF;">${fee.toFixed(4)} USDT</td>
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
    // 6. CONFIGURACIÓN INTERACTIVA
    // ==========================================
    async function fetchConfig() {
        try {
            const res = await fetch("/api/config");
            if (!res.ok) return;
            const cfg = await res.json();

            document.getElementById("cfg-tp").value = cfg.take_profit_percent;
            document.getElementById("cfg-sl").value = cfg.stop_loss_percent;
            document.getElementById("cfg-fee").value = cfg.transaction_fee_percent;
            document.getElementById("cfg-pos-size").value = cfg.position_size_percent;
            document.getElementById("cfg-max-trades").value = cfg.max_concurrent_trades;
            document.getElementById("cfg-enable-breakeven").checked = cfg.enable_breakeven !== false;

            document.getElementById("cfg-exchange").value = cfg.exchange_name || "binance";
            document.getElementById("cfg-mode-dry").value = cfg.dry_run ? "true" : "false";
            document.getElementById("cfg-symbols").value = (cfg.symbols || []).join(", ");
            document.getElementById("cfg-ts-act").value = cfg.trailing_activation_profit;
            document.getElementById("cfg-ts-cb").value = cfg.trailing_callback;
        } catch (err) {
            console.error("Error consultando configuración:", err);
        }
    }

    const btnSaveConfig = document.getElementById("btn-save-config");
    if (btnSaveConfig) {
        btnSaveConfig.addEventListener("click", async () => {
            btnSaveConfig.disabled = true;
            btnSaveConfig.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Guardando...`;

            const rawSymbols = document.getElementById("cfg-symbols").value;
            const symbolsList = rawSymbols.split(",").map(s => s.trim().toUpperCase()).filter(s => s.length > 0);

            const payload = {
                take_profit_percent: parseFloat(document.getElementById("cfg-tp").value),
                stop_loss_percent: parseFloat(document.getElementById("cfg-sl").value),
                transaction_fee_percent: parseFloat(document.getElementById("cfg-fee").value),
                position_size_percent: parseFloat(document.getElementById("cfg-pos-size").value),
                max_concurrent_trades: parseInt(document.getElementById("cfg-max-trades").value),
                enable_breakeven: document.getElementById("cfg-enable-breakeven").checked,
                exchange_name: document.getElementById("cfg-exchange").value,
                dry_run: document.getElementById("cfg-mode-dry").value === "true",
                symbols: symbolsList,
                trailing_activation_profit: parseFloat(document.getElementById("cfg-ts-act").value),
                trailing_callback: parseFloat(document.getElementById("cfg-ts-cb").value)
            };

            try {
                const res = await fetch("/api/config", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    showToast("success", "Configuración Guardada", "Los cambios se aplicaron y guardaron en config.yaml correctamente.");
                    fetchStatus();
                } else {
                    showToast("error", "Error de Validación", data.detail || "No se pudo actualizar la configuración.");
                }
            } catch (err) {
                console.error("Error guardando configuración:", err);
                showToast("error", "Error de Red", "No se pudo conectar con el servidor para guardar la configuración.");
            } finally {
                btnSaveConfig.disabled = false;
                btnSaveConfig.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> Guardar Cambios en Configuración`;
            }
        });
    }

    // ==========================================
    // 7. NOTIFICACIONES EMERGENTES (TOASTS)
    // ==========================================
    function showToast(type, title, message) {
        const container = document.getElementById("toast-container");
        if (!container) return;

        const toast = document.createElement("div");
        let typeClass = "toast-buy";
        let iconHtml = `<i class="fa-solid fa-cart-shopping"></i>`;

        if (type === "BUY") {
            typeClass = "toast-buy";
            iconHtml = `<i class="fa-solid fa-cart-shopping"></i>`;
        } else if (type === "SELL_TP" || type === "success") {
            typeClass = "toast-sell-tp";
            iconHtml = `<i class="fa-solid fa-circle-check"></i>`;
        } else if (type === "SELL_SL" || type === "error") {
            typeClass = "toast-sell-sl";
            iconHtml = `<i class="fa-solid fa-triangle-exclamation"></i>`;
        }

        toast.className = `toast-card ${typeClass}`;
        toast.innerHTML = `
            <div class="toast-icon">${iconHtml}</div>
            <div class="toast-body">
                <div class="toast-title">
                    <span>${title}</span>
                    <button class="toast-close" onclick="this.parentElement.parentElement.parentElement.remove()">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>
                <div class="toast-message">${message}</div>
            </div>
        `;

        container.appendChild(toast);

        // Auto remover después de 6 segundos
        setTimeout(() => {
            if (toast.parentNode) {
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(10px)';
                setTimeout(() => toast.remove(), 300);
            }
        }, 6000);
    }

    // ==========================================
    // 8. BOTONES DE ACCIÓN (START/STOP/SCAN)
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
    // 9. WEBSOCKET LOGS & NOTIFICACIONES EN VIVO
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
                } else if (msg.type === "trade_notification" && msg.data) {
                    const trade = msg.data;
                    if (trade.action === "BUY") {
                        showToast("BUY", `🚀 COMPRA: ${trade.symbol}`, `Precio: ${trade.price.toFixed(4)} USDT | Costo: ${trade.cost.toFixed(2)} USDT | Fee: ${trade.fee.toFixed(4)} USDT`);
                    } else if (trade.action === "SELL") {
                        const toastType = trade.pnl_amount >= 0 ? "SELL_TP" : "SELL_SL";
                        const pnlSign = trade.pnl_amount >= 0 ? "+" : "";
                        showToast(toastType, `💰 VENTA: ${trade.symbol} (${trade.exit_reason})`, `Precio: ${trade.exit_price.toFixed(4)} USDT | PnL Neto: ${pnlSign}${trade.pnl_amount.toFixed(2)} USDT (${pnlSign}${trade.pnl_percent.toFixed(2)}%)`);
                    }
                    fetchStatus();
                    loadChartData();
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
    // 10. ARRANQUE DEL DASHBOARD
    // ==========================================
    initChart();
    fetchStatus();
    connectWebSocket();

    // Polling regular de estado y velas cada 10s
    setInterval(() => {
        fetchStatus();
        loadChartData();
    }, 10000);

    // ==========================================
    // 11. COPILOTO AI CHAT INTERACTIVO
    // ==========================================
    const chatToggleBtn = document.getElementById("ai-chat-toggle-btn");
    const chatCloseBtn = document.getElementById("ai-chat-close-btn");
    const chatWindow = document.getElementById("ai-chat-window");
    const chatMessages = document.getElementById("ai-chat-messages");
    const chatInput = document.getElementById("ai-chat-input");
    const chatSendBtn = document.getElementById("ai-chat-send-btn");

    if (chatToggleBtn && chatWindow) {
        chatToggleBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (chatWindow.style.display === "none" || chatWindow.classList.contains("hidden")) {
                chatWindow.style.display = "flex";
                chatWindow.classList.remove("hidden");
                if (chatInput) chatInput.focus();
            } else {
                chatWindow.style.display = "none";
                chatWindow.classList.add("hidden");
            }
        });
    }

    if (chatCloseBtn && chatWindow) {
        chatCloseBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            chatWindow.style.display = "none";
            chatWindow.classList.add("hidden");
        });
    }

    function appendChatMessage(sender, text) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `chat-message ${sender}`;
        const avatarIcon = sender === "user" ? "fa-user" : "fa-robot";
        msgDiv.innerHTML = `
            <div class="msg-avatar"><i class="fa-solid ${avatarIcon}"></i></div>
            <div class="msg-content">${text.replace(/\n/g, "<br>")}</div>
        `;
        chatMessages.appendChild(msgDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    async function sendChatMessage(messageText) {
        const msg = messageText || chatInput.value.trim();
        if (!msg) return;

        appendChatMessage("user", msg);
        if (!messageText) chatInput.value = "";

        // Indicador de escribiendo
        const typingDiv = document.createElement("div");
        typingDiv.className = "chat-message bot typing";
        typingDiv.id = "chat-typing-indicator";
        typingDiv.innerHTML = `
            <div class="msg-avatar"><i class="fa-solid fa-robot fa-spin"></i></div>
            <div class="msg-content">CryptoBot AI está pensando...</div>
        `;
        chatMessages.appendChild(typingDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        try {
            const res = await fetch("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: msg })
            });

            const typingEl = document.getElementById("chat-typing-indicator");
            if (typingEl) typingEl.remove();

            if (res.ok) {
                const data = await res.json();
                appendChatMessage("bot", data.reply || "Respuesta no disponible.");
                fetchStatus();
                fetchConfig();
                if (typeof fetchPositions === "function") fetchPositions();
                if (typeof fetchTrades === "function") fetchTrades();
                if (typeof fetchHistory === "function") fetchHistory();
                if (typeof renderCharts === "function") renderCharts();
            } else {
                appendChatMessage("bot", "Ocurrió un problema de conexión al procesar la solicitud.");
            }
        } catch (err) {
            const typingEl = document.getElementById("chat-typing-indicator");
            if (typingEl) typingEl.remove();
            appendChatMessage("bot", "Error de red al conectar con el asistente de IA.");
        }
    }

    if (chatSendBtn) {
        chatSendBtn.addEventListener("click", () => sendChatMessage());
    }

    if (chatInput) {
        chatInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                sendChatMessage();
            }
        });
    }

    window.sendQuickChatMessage = function(promptText) {
        if (chatWindow) {
            chatWindow.style.display = "flex";
            chatWindow.classList.remove("hidden");
        }
        sendChatMessage(promptText);
    };
});

