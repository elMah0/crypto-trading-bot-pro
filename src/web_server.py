"""
Módulo del Servidor Web (FastAPI) para proveer API REST, WebSockets y servir la GUI Frontend.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Referencia global al orquestador que se asignará al inicializar
bot_orchestrator = None

app = FastAPI(title="CryptoTradingBot Dashboard API", version="1.0.0")

# Gestión de conexiones WebSocket para logs en tiempo real
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for dead_conn in disconnected:
            self.disconnect(dead_conn)


ws_manager = ConnectionManager()


# Handler personalizado de Logging para retransmitir a WebSockets
class WebSocketLogHandler(logging.Handler):
    def emit(self, record):
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": record.getMessage()
            }
            # Broadcast asíncrono seguro
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(ws_manager.broadcast({"type": "log", "data": log_entry}))
            except RuntimeError:
                pass
        except Exception:
            pass


# Configuración del log handler para WebSockets
ws_log_handler = WebSocketLogHandler()
ws_log_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(ws_log_handler)


def broadcast_trade_event(event_data: dict):
    """Retransmite un evento de compra o venta a la Web GUI a través de WebSocket para notificaciones emergentes (Toasts)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(ws_manager.broadcast({"type": "trade_notification", "data": event_data}))
    except Exception as e:
        logger.debug(f"Error retransmitiendo trade_event: {e}")


# --- Modelos de Petición ---
class ConfigUpdateRequest(BaseModel):
    dry_run: Optional[bool] = None
    exchange_name: Optional[str] = None
    position_size_percent: Optional[float] = None
    stop_loss_percent: Optional[float] = None
    take_profit_percent: Optional[float] = None
    transaction_fee_percent: Optional[float] = None
    trailing_activation_profit: Optional[float] = None
    trailing_callback: Optional[float] = None
    max_concurrent_trades: Optional[int] = None
    symbols: Optional[List[str]] = None
    enable_breakeven: Optional[bool] = None
    ai_enabled: Optional[bool] = None
    ai_min_confidence_score: Optional[float] = None


class PositionLevelUpdateRequest(BaseModel):
    symbol: str
    take_profit_percent: Optional[float] = None
    stop_loss_percent: Optional[float] = None


class ChatMessageRequest(BaseModel):
    message: str


# --- Endpoints de API REST ---

@app.get("/api/status")
def get_bot_status():
    """Retorna el estado general del bot, balances, PnL y conteo de operaciones."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")

    try:
        free_balance = bot_orchestrator.exchange_client.get_free_balance()
        total_balance = bot_orchestrator.order_executor.get_total_portfolio_value()
    except Exception as e:
        logger.warning(f"No se pudo consultar balance del exchange: {e}")
        free_balance = 0.0
        total_balance = 0.0

    open_positions = []

    for sym, pos in bot_orchestrator.order_executor.open_positions.items():
        try:
            cur_price = bot_orchestrator.exchange_client.fetch_ticker_price(sym)
            pnl_amount = (cur_price - pos.entry_price) * pos.amount
            pnl_percent = ((cur_price - pos.entry_price) / pos.entry_price) * 100.0
        except Exception:
            cur_price = pos.entry_price
            pnl_amount = 0.0
            pnl_percent = 0.0

        open_positions.append({
            "id": pos.trade_id,
            "symbol": pos.symbol,
            "entry_price": pos.entry_price,
            "current_price": cur_price,
            "highest_price": pos.highest_price,
            "amount": pos.amount,
            "cost": pos.cost,
            "current_sl_price": pos.current_sl_price,
            "tp_price": pos.tp_price,
            "trailing_active": pos.trailing_active,
            "pnl_amount": pnl_amount,
            "pnl_percent": pnl_percent,
            "opened_at": pos.opened_at
        })

    today_summary = bot_orchestrator.db.get_trades_summary_today(
        is_dry_run=bot_orchestrator.config.mode.dry_run
    )
    all_time_summary = bot_orchestrator.db.get_all_time_stats(
        is_dry_run=bot_orchestrator.config.mode.dry_run
    )

    return {
        "is_running": bot_orchestrator.is_running,
        "mode": "DRY-RUN (Simulación)" if bot_orchestrator.config.mode.dry_run else "REAL",
        "is_dry_run": bot_orchestrator.config.mode.dry_run,
        "exchange": bot_orchestrator.config.exchange.name.upper(),
        "free_balance": round(free_balance, 2),
        "total_balance": round(total_balance, 2),
        "currency": bot_orchestrator.config.mode.quote_currency,
        "open_positions": open_positions,
        "today_summary": today_summary,
        "all_time_summary": all_time_summary,
        "symbols": bot_orchestrator.config.symbols,
        "transaction_fee_percent": getattr(bot_orchestrator.config.risk, "transaction_fee_percent", 0.1)
    }


@app.get("/api/signals")
def get_latest_signals():
    """Calcula y devuelve las señales multitemporales actuales para cada par configurado."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")

    results = []
    for symbol in bot_orchestrator.config.symbols:
        try:
            df_1d = bot_orchestrator.exchange_client.fetch_ohlcv(
                symbol=symbol,
                timeframe=bot_orchestrator.config.strategy.macro.timeframe,
                limit=bot_orchestrator.config.strategy.macro.limit_candles
            )
            df_1h = bot_orchestrator.exchange_client.fetch_ohlcv(
                symbol=symbol,
                timeframe=bot_orchestrator.config.strategy.micro.timeframe,
                limit=bot_orchestrator.config.strategy.micro.limit_candles
            )
            cur_price = bot_orchestrator.exchange_client.fetch_ticker_price(symbol)
            sig = bot_orchestrator.strategy.analyze(symbol, df_1d, df_1h, cur_price)

            results.append({
                "symbol": symbol,
                "action": sig.action,
                "current_price": sig.current_price,
                "macro_passed": sig.macro_passed,
                "micro_passed": sig.micro_passed,
                "sma_10d": sig.sma_10d,
                "adx_1d": sig.adx_1d,
                "rsi_1h": sig.rsi_1h,
                "current_volume_1h": sig.current_volume_1h,
                "avg_volume_1h": sig.avg_volume_1h,
                "reason": sig.reason,
                "is_in_position": symbol in bot_orchestrator.order_executor.open_positions
            })
        except Exception as e:
            logger.error(f"Error obteniendo señales para {symbol}: {e}")
            results.append({
                "symbol": symbol,
                "action": "ERROR",
                "reason": str(e),
                "is_in_position": symbol in bot_orchestrator.order_executor.open_positions
            })

    return {"signals": results}


@app.get("/api/trades")
def get_trades_history(limit: int = 50):
    """Retorna el historial de operaciones cerradas registradas en la base de datos."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")

    with bot_orchestrator.db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trades 
            WHERE is_dry_run = ?
            ORDER BY id DESC LIMIT ?
        """, (1 if bot_orchestrator.config.mode.dry_run else 0, limit))
        rows = cursor.fetchall()
        return {"trades": [dict(r) for r in rows]}


@app.get("/api/candles")
def get_candles(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 60):
    """Devuelve datos de velas OHLCV formateados para graficar en Lightweight Charts."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")

    try:
        df = bot_orchestrator.exchange_client.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        if df.empty:
            return {"candles": [], "sma": [], "volume": []}

        import ta
        df["sma_10"] = ta.trend.sma_indicator(df["close"], window=10)

        candles = []
        sma_points = []
        volume_bars = []

        for idx, row in df.iterrows():
            timestamp_sec = int(idx.timestamp())
            candles.append({
                "time": timestamp_sec,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
            if not row.isna()["sma_10"]:
                sma_points.append({
                    "time": timestamp_sec,
                    "value": float(row["sma_10"])
                })
            
            color = "rgba(38, 166, 154, 0.6)" if row["close"] >= row["open"] else "rgba(239, 83, 80, 0.6)"
            volume_bars.append({
                "time": timestamp_sec,
                "value": float(row["volume"]),
                "color": color
            })

        return_markers = []
        try:
            with bot_orchestrator.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trades 
                    WHERE symbol = ? AND is_dry_run = ?
                    ORDER BY id ASC
                """, (symbol, 1 if bot_orchestrator.config.mode.dry_run else 0))
                rows = cursor.fetchall()
                symbol_trades = [dict(r) for r in rows]

            if symbol_trades and candles:
                candle_times = [c["time"] for c in candles]
                min_candle_time = candle_times[0]

                def find_nearest_candle_time(target_ts):
                    nearest = min_candle_time
                    for ct in candle_times:
                        if ct <= target_ts:
                            nearest = ct
                        else:
                            break
                    return nearest

                for t in symbol_trades:
                    opened_at_str = t.get("opened_at")
                    if opened_at_str:
                        try:
                            dt_open = datetime.fromisoformat(opened_at_str)
                            open_ts = int(dt_open.timestamp())
                            if open_ts >= min_candle_time - 86400:
                                mapped_time = find_nearest_candle_time(open_ts)
                                return_markers.append({
                                    "time": mapped_time,
                                    "position": "belowBar",
                                    "color": "#10B981",
                                    "shape": "arrowUp",
                                    "text": f"BUY @ {t['entry_price']:.2f}"
                                })
                        except Exception:
                            pass

                    if t.get("status") == "CLOSED" and t.get("closed_at"):
                        try:
                            dt_close = datetime.fromisoformat(t["closed_at"])
                            close_ts = int(dt_close.timestamp())
                            if close_ts >= min_candle_time - 86400:
                                mapped_time = find_nearest_candle_time(close_ts)
                                pnl = t.get("pnl_amount") or 0.0
                                color = "#3B82F6" if pnl >= 0 else "#EF4444"
                                reason = t.get("exit_reason") or "SELL"
                                exit_p = t.get("exit_price") or 0.0
                                return_markers.append({
                                    "time": mapped_time,
                                    "position": "aboveBar",
                                    "color": color,
                                    "shape": "arrowDown",
                                    "text": f"SELL ({reason}) @ {exit_p:.2f}"
                                })
                        except Exception:
                            pass

                return_markers.sort(key=lambda x: (x["time"], 0 if x["position"] == "belowBar" else 1))
        except Exception as e:
            logger.warning(f"Error generando marcas de gráfico para {symbol}: {e}")

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": candles,
            "sma": sma_points,
            "volume": volume_bars,
            "markers": return_markers
        }
    except Exception as e:
        logger.error(f"Error obteniendo velas de {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/bot/start")
def start_bot():
    """Inicia o reanuda el ciclo de trading del bot."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    bot_orchestrator.is_running = True
    logger.info("Bot de trading REANUDADO desde la interfaz gráfica.")
    return {"success": True, "message": "Bot iniciado"}


@app.post("/api/bot/stop")
def stop_bot():
    """Pausa el ciclo de trading del bot."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    bot_orchestrator.is_running = False
    logger.info("Bot de trading PAUSADO desde la interfaz gráfica.")
    return {"success": True, "message": "Bot pausado"}


@app.post("/api/bot/trigger")
def trigger_scan():
    """Ejecuta inmediatamente un ciclo de análisis y actualización."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    try:
        bot_orchestrator.run_iteration()
        return {"success": True, "message": "Ciclo ejecutado exitosamente"}
    except Exception as e:
        logger.error(f"Error al ejecutar escaneo manual: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/position/close/{symbol}")
def close_position_manually(symbol: str):
    """Permite cerrar manualmente una posición abierta desde la interfaz web."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    
    clean_sym = symbol.replace("-", "/")
    if clean_sym not in bot_orchestrator.order_executor.open_positions:
        raise HTTPException(status_code=404, detail=f"No hay posición abierta para {clean_sym}")

    pos = bot_orchestrator.order_executor.open_positions[clean_sym]
    cur_price = bot_orchestrator.exchange_client.fetch_ticker_price(clean_sym)
    pnl_amount = (cur_price - pos.entry_price) * pos.amount
    pnl_percent = ((cur_price - pos.entry_price) / pos.entry_price) * 100.0

    success = bot_orchestrator.order_executor.execute_sell(
        symbol=clean_sym,
        current_price=cur_price,
        exit_reason="MANUAL_GUI",
        pnl_amount=pnl_amount,
        pnl_percent=pnl_percent
    )

    return {"success": success, "message": f"Posición {clean_sym} cerrada"}


@app.post("/api/position/update-levels")
def update_position_levels(req: PositionLevelUpdateRequest):
    """Permite ajustar Take Profit % y Stop Loss % de una posición abierta en tiempo real."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    
    clean_sym = req.symbol.replace("-", "/")
    if clean_sym not in bot_orchestrator.order_executor.open_positions:
        raise HTTPException(status_code=404, detail=f"No hay posición abierta para {clean_sym}")

    pos = bot_orchestrator.order_executor.open_positions[clean_sym]

    if req.take_profit_percent is not None and req.take_profit_percent > 0:
        pos.tp_price = pos.entry_price * (1.0 + (req.take_profit_percent / 100.0))

    if req.stop_loss_percent is not None and req.stop_loss_percent > 0:
        pos.current_sl_price = pos.entry_price * (1.0 - (req.stop_loss_percent / 100.0))

    logger.info(f"[{clean_sym}] Niveles actualizados desde GUI: TP={pos.tp_price:.4f}, SL={pos.current_sl_price:.4f}")
    return {"success": True, "message": f"Niveles para {clean_sym} actualizados"}


@app.get("/api/config")
def get_config():
    """Devuelve la configuración editable actual."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    cfg = bot_orchestrator.config
    return {
        "dry_run": cfg.mode.dry_run,
        "exchange_name": cfg.exchange.name,
        "quote_currency": cfg.mode.quote_currency,
        "symbols": cfg.symbols,
        "position_size_percent": cfg.risk.position_size_percent,
        "max_concurrent_trades": cfg.risk.max_concurrent_trades,
        "stop_loss_percent": cfg.risk.stop_loss_percent,
        "take_profit_percent": cfg.risk.take_profit_percent,
        "transaction_fee_percent": getattr(cfg.risk, "transaction_fee_percent", 0.1),
        "enable_breakeven": getattr(cfg.risk, "enable_breakeven", True),
        "ai_enabled": getattr(cfg.ai, "enabled", True),
        "ai_min_confidence_score": getattr(cfg.ai, "min_confidence_score", 70.0),
        "trailing_activation_profit": cfg.risk.trailing_stop.activation_profit_percent,
        "trailing_callback": cfg.risk.trailing_stop.callback_percent,
        "max_daily_loss_percent": cfg.risk.circuit_breaker.max_daily_loss_percent,
        "sl_cooldown_hours": cfg.risk.circuit_breaker.sl_cooldown_hours,
        "check_interval_seconds": cfg.bot_loop.check_interval_seconds
    }


@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    """Actualiza la configuración en caliente desde la interfaz Web y la guarda en config.yaml."""
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")
    
    cfg = bot_orchestrator.config

    if req.dry_run is not None:
        if req.dry_run is False:
            secret = cfg.exchange.api_secret or os.getenv("EXCHANGE_API_SECRET")
            key = cfg.exchange.api_key or os.getenv("EXCHANGE_API_KEY")
            is_placeholder = not secret or "tu_api_secret" in str(secret).lower() or len(str(secret)) < 10
            if not key or is_placeholder:
                raise HTTPException(
                    status_code=400,
                    detail="No se puede activar el MODO REAL sin configurar EXCHANGE_API_KEY y EXCHANGE_API_SECRET en tu archivo .env. Mantén el modo Simulación."
                )
        cfg.mode.dry_run = req.dry_run
        bot_orchestrator.order_executor.is_dry_run = req.dry_run

    if req.exchange_name and req.exchange_name.lower() != cfg.exchange.name.lower():
        try:
            cfg.exchange.name = req.exchange_name.lower()
            bot_orchestrator.exchange_client.reconnect_exchange(cfg.exchange)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Exchange inválido o no soportado: {e}")

    if req.position_size_percent is not None:
        if not (0.1 <= req.position_size_percent <= 100.0):
            raise HTTPException(status_code=400, detail="position_size_percent debe estar entre 0.1% y 100%")
        cfg.risk.position_size_percent = req.position_size_percent

    if req.stop_loss_percent is not None and req.stop_loss_percent > 0:
        cfg.risk.stop_loss_percent = req.stop_loss_percent

    if req.take_profit_percent is not None and req.take_profit_percent > 0:
        cfg.risk.take_profit_percent = req.take_profit_percent

    if req.transaction_fee_percent is not None and req.transaction_fee_percent >= 0:
        cfg.risk.transaction_fee_percent = req.transaction_fee_percent
        cfg.exchange.transaction_fee_percent = req.transaction_fee_percent

    if req.ai_enabled is not None:
        cfg.ai.enabled = req.ai_enabled

    if req.ai_min_confidence_score is not None and 0 <= req.ai_min_confidence_score <= 100:
        cfg.ai.min_confidence_score = req.ai_min_confidence_score

    if req.trailing_activation_profit is not None:
        cfg.risk.trailing_stop.activation_profit_percent = req.trailing_activation_profit

    if req.trailing_callback is not None:
        cfg.risk.trailing_stop.callback_percent = req.trailing_callback

    if req.max_concurrent_trades is not None and req.max_concurrent_trades >= 1:
        cfg.risk.max_concurrent_trades = req.max_concurrent_trades

    if req.symbols is not None and len(req.symbols) > 0:
        cfg.symbols = req.symbols

    if req.enable_breakeven is not None:
        cfg.risk.enable_breakeven = req.enable_breakeven

    # Guardar en archivo YAML de forma persistente
    try:
        from src.config_loader import save_config_to_yaml
        save_config_to_yaml(cfg, "config.yaml")
    except Exception as e:
        logger.error(f"Error guardando configuración en YAML: {e}")

    logger.info("Configuración del bot actualizada dinámicamente desde la interfaz Web.")
    return {"success": True, "message": "Configuración actualizada y guardada correctamente"}


@app.post("/api/chat")
def process_chat_message(req: ChatMessageRequest):
    """
    Procesa un mensaje de chat del usuario utilizando Gemini AI Copilot.
    Puede responder consultas del mercado, buscar top gainers o ejecutar comandos de configuración.
    """
    if not bot_orchestrator:
        raise HTTPException(status_code=503, detail="Orquestador no inicializado")

    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
        load_dotenv()
    except Exception:
        pass

    cfg = bot_orchestrator.config
    user_msg = req.message.strip()
    if not user_msg:
        return {"reply": "Por favor ingresa una pregunta o comando válido."}

    # 1. Consultar balance y top gainers para el contexto
    try:
        free_balance = bot_orchestrator.exchange_client.get_free_balance()
    except Exception:
        free_balance = 0.0

    open_positions_dict = bot_orchestrator.order_executor.open_positions
    pos_details_list = []
    for s, p in open_positions_dict.items():
        try:
            curr_p = bot_orchestrator.exchange_client.fetch_ticker_price(s)
            pnl_p = ((curr_p - p.entry_price) / p.entry_price) * 100.0
        except Exception:
            curr_p = p.entry_price
            pnl_p = 0.0
        pos_details_list.append(
            f"- {s}: Entrada: {p.entry_price:.4f} | Actual: {curr_p:.4f} ({pnl_p:+.2f}%) | "
            f"TP: {p.tp_price:.4f} ({p.take_profit_percent}%) | SL: {p.current_sl_price:.4f} ({p.stop_loss_percent}%) | "
            f"Breakeven: {'Activo' if p.enable_breakeven else 'Inactivo'} | Trailing: {'Activo' if p.trailing_active else 'Pendiente'}"
        )
    pos_details_str = "\n".join(pos_details_list) if pos_details_list else "Ninguna posición abierta en este momento."

    top_gainers = bot_orchestrator.exchange_client.fetch_top_gainers(top_n=5)
    gainers_str = "\n".join([f"- {g['symbol']}: {g['price']:.4f} USDT (+{g['change_24h']:.2f}%)" for g in top_gainers]) if top_gainers else "No disponible"

    system_prompt = f"""
Eres "CryptoBot Copilot AI", el asistente inteligente con PERMISOS COMPLETOS DE CONTROL Y TRADING para CryptoBot Pro.
El usuario ha enviado la siguiente consulta: "{user_msg}"

Estado y Configuración Actual del Bot:
- Modo de Ejecución: {'SIMULACIÓN (dry_run=True)' if cfg.mode.dry_run else 'REAL MONEY (dry_run=False)'}
- Símbolos Activos Monitoreados: {cfg.symbols}
- Saldo Disponible: {free_balance:.2f} USDT
- Posiciones Abiertas Actualmente con Parámetros Individuales:
{pos_details_str}

Top 5 Criptomonedas con Mayor Alza (24h) en Binance:
{gainers_str}

Instrucciones y Capacidades:
1. Responde amablemente en español con formato Markdown profesional, claro y directo.
2. Tienes permisos para manipular el bot. Si el usuario te pide:
   - ABRIR POSICIÓN (ej: "compra SOL", "abre posición en ETH con 5 USDT, TP 5% y SL 2%"):
     Debes explicar la acción y generar un bloque JSON con action "open_position".
   - CERRAR POSICIÓN (ej: "cierra SOL", "vende BTC", "cierra todas"):
     Debes explicar la acción y generar un bloque JSON con action "close_position".
   - MODIFICAR UNA POSICIÓN ESPECÍFICA (ej: "sube el TP de SOL al 8%", "ajusta el SL de ETH a 1%"):
     Debes explicar que el cambio se aplica ÚNICAMENTE a esa posición y generar un bloque JSON con action "update_position".
   - CAMBIOS GLOBALES (ej: "pon posición al 10%", "modo real"):
     Generar action "update_config".

Formatos JSON de Acción (debes incluir solo UNO al final de tu respuesta en formato ```json ... ``` si aplica):

Para abrir posición:
```json
{{
  "action": "open_position",
  "params": {{
    "symbol": "BTC/USDT",
    "amount_quote": 5.0,
    "custom_tp_percent": 4.0,
    "custom_sl_percent": 2.0
  }}
}}
```

Para cerrar posición:
```json
{{
  "action": "close_position",
  "params": {{
    "symbol": "BTC/USDT"
  }}
}}
```
(Para cerrar todas: `"symbol": "ALL"`)

Para modificar una posición abierta de forma aislada:
```json
{{
  "action": "update_position",
  "params": {{
    "symbol": "BTC/USDT",
    "take_profit_percent": 6.0,
    "stop_loss_percent": 1.5,
    "enable_breakeven": true
  }}
}}
```

Para configuración global:
```json
{{
  "action": "update_config",
  "params": {{
    "dry_run": true_o_false_si_cambia,
    "position_size_percent": numero_si_cambia,
    "stop_loss_percent": numero_si_cambia,
    "take_profit_percent": numero_si_cambia,
    "symbols": ["lista_si_cambia"]
  }}
}}
```

Si el usuario solo hace una pregunta informativa, NO incluyas bloque JSON.
"""

    try:
        import json, re, requests
        evaluator = bot_orchestrator.ai_evaluator
        reply = None

        # 1. Prioridad: Groq AI (Ultra rápido y sin restricciones de cuota)
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key and not groq_key.startswith("tu_"):
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={
                        "model": "openai/gpt-oss-120b",
                        "messages": [{"role": "user", "content": system_prompt}],
                        "temperature": 0.3
                    },
                    timeout=10
                )
                if resp.status_code == 200:
                    reply = resp.json()["choices"][0]["message"]["content"].strip()
                else:
                    logger.warning(f"Groq API status {resp.status_code}: {resp.text[:120]}")
            except Exception as g_err:
                logger.warning(f"Error consultando Groq AI ({g_err}). Intentando Gemini...")

        # 2. Alternativa: Google Gemini AI
        if not reply and evaluator and getattr(evaluator, "gemini_api_key", None):
            import google.generativeai as genai
            for model_candidate in ["models/gemini-flash-lite-latest", "models/gemini-flash-latest"]:
                try:
                    m = genai.GenerativeModel(model_candidate)
                    resp = m.generate_content(system_prompt)
                    reply = resp.text.strip()
                    break
                except Exception as m_err:
                    logger.warning(f"Error con modelo {model_candidate} ({m_err}). Intentando siguiente...")

        if not reply:
            # Motor Financiero Inteligente de Respaldo (Offline)
            msg_lower = user_msg.lower()
            if "duplicar" in msg_lower or "5" in msg_lower or "estrategia" in msg_lower or "ganar" in msg_lower:
                reply = (
                    "### 📈 Estrategia Cuantitativa para Crecer Capital Pequeño ($5 USDT)\n\n"
                    "Para multiplicar un capital pequeño en criptomonedas sin liquidar la cuenta, la mejor estrategia matemática es el **Momentum Scalping con Interés Compuesto**:\n\n"
                    "1. **Enfoque en Monedas de Alta Volatilidad (Top Gainers)**:\n"
                    "   - Operar pares con rupturas de volumen > 1.5x su promedio de 1 hora.\n"
                    "2. **Gestión de Riesgo Asimétrica (Ratio 2:1 o 3:1)**:\n"
                    "   - **Take Profit (TP):** +3.5% a +5.0%\n"
                    "   - **Stop Loss (SL):** -1.5% a -2.0%\n"
                    "3. **Matemática del Interés Compuesto**:\n"
                    "   - Con 5 a 10 operaciones de micro-tendencia consecutivas, $5 se transforman en $10+ USDT.\n"
                    "4. **Recomendación de Parámetros**:\n"
                    "   - Posición: **100%** de saldo libre por orden (para cumplir el mínimo de Binance).\n"
                    "   - Trailing Stop activado al +1.5%.\n\n"
                    "¿Deseas que abra una posición ahora en alguna de las monedas de alta ganancia?"
                )
            elif "gainer" in msg_lower or "alza" in msg_lower or "subido" in msg_lower:
                reply = (
                    f"### 🚀 Criptomonedas con Mayor Alza Hoy en Binance:\n\n"
                    f"{gainers_str}\n\n"
                    f"Saldo disponible: **{free_balance:.2f} USDT** | Símbolos activos: `{', '.join(cfg.symbols)}`."
                )
            else:
                reply = (
                    f"🤖 **CryptoBot Copilot**\n\n"
                    f"He procesado tu consulta: *\"{user_msg}\"*.\n"
                    f"- **Saldo disponible:** {free_balance:.2f} USDT\n"
                    f"- **Modo actual:** {'Simulación' if cfg.mode.dry_run else 'Real'}\n"
                    f"- **Posiciones abiertas:** {len(open_positions_dict)}\n\n"
                    f"¿Deseas abrir una posición, cerrarla o cambiar parámetros de alguna de ellas?"
                )

        # Detectar y ejecutar comandos de acción en JSON devueltos por la IA
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", reply, re.DOTALL)
        if json_match:
            try:
                cmd_data = json.loads(json_match.group(1))
                action = cmd_data.get("action")
                params = cmd_data.get("params", {})

                # A) Abrir Posición Manual
                if action == "open_position":
                    sym = str(params.get("symbol", "")).upper()
                    if sym:
                        amt_q = float(params.get("amount_quote")) if params.get("amount_quote") else None
                        tp_p = float(params.get("custom_tp_percent")) if params.get("custom_tp_percent") else None
                        sl_p = float(params.get("custom_sl_percent")) if params.get("custom_sl_percent") else None
                        res_pos = bot_orchestrator.order_executor.open_manual_position(
                            symbol=sym,
                            quote_amount=amt_q,
                            custom_tp_percent=tp_p,
                            custom_sl_percent=sl_p,
                            reason="Orden Ejecutada por Copiloto AI"
                        )
                        if res_pos:
                            logger.info(f"IA Copilot abrió exitosamente posición en {sym}")
                        else:
                            logger.warning(f"IA Copilot no pudo abrir posición en {sym}")

                # B) Cerrar Posición
                elif action == "close_position":
                    sym = str(params.get("symbol", "")).upper()
                    if sym == "ALL":
                        for s in list(bot_orchestrator.order_executor.open_positions.keys()):
                            bot_orchestrator.order_executor.close_position_by_symbol(s, reason="Cierre Total por Copiloto AI")
                    elif sym:
                        bot_orchestrator.order_executor.close_position_by_symbol(sym, reason="Cierre Manual por Copiloto AI")

                # C) Modificar Posición Aislada
                elif action == "update_position":
                    sym = str(params.get("symbol", "")).upper()
                    if sym:
                        bot_orchestrator.order_executor.update_position_parameters(
                            symbol=sym,
                            take_profit_percent=params.get("take_profit_percent"),
                            stop_loss_percent=params.get("stop_loss_percent"),
                            trailing_activation_percent=params.get("trailing_activation_percent"),
                            trailing_callback_percent=params.get("trailing_callback_percent"),
                            enable_breakeven=params.get("enable_breakeven")
                        )

                # D) Modificar Configuración Global
                elif action == "update_config":
                    if "dry_run" in params:
                        cfg.mode.dry_run = bool(params["dry_run"])
                        bot_orchestrator.order_executor.is_dry_run = cfg.mode.dry_run
                    if "position_size_percent" in params and float(params["position_size_percent"]) > 0:
                        cfg.risk.position_size_percent = float(params["position_size_percent"])
                    if "stop_loss_percent" in params and float(params["stop_loss_percent"]) > 0:
                        cfg.risk.stop_loss_percent = float(params["stop_loss_percent"])
                    if "take_profit_percent" in params and float(params["take_profit_percent"]) > 0:
                        cfg.risk.take_profit_percent = float(params["take_profit_percent"])
                    if "symbols" in params and isinstance(params["symbols"], list) and len(params["symbols"]) > 0:
                        cfg.symbols = params["symbols"]
                    
                    from src.config_loader import save_config_to_yaml
                    save_config_to_yaml(cfg, "config.yaml")
                    logger.info(f"IA Copilot aplicó actualización global de configuración: {params}")

            except Exception as ex:
                logger.warning(f"No se pudo aplicar acción JSON de la IA: {ex}")

            # Limpiar la respuesta visual para el usuario del bloque json técnico
            reply_clean = re.sub(r"```json\s*(\{.*?\})\s*```", "", reply, flags=re.DOTALL).strip()
            if reply_clean:
                reply = reply_clean

        return {"reply": reply, "gainers": top_gainers}

    except Exception as e:
        logger.error(f"Error procesando mensaje de chat IA: {e}")
        return {"reply": f"Disculpa, ocurrió un error al procesar tu solicitud: {e}"}



@app.websocket("/ws/logs")
async def websocket_logs_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para transmitir logs y eventos en tiempo real a la interfaz."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Mantener conexión activa
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# Servir archivos estáticos del Frontend
web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
if os.path.exists(web_dir):
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    def serve_dashboard():
        index_file = os.path.join(web_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return "<h1>Dashboard UI en construcción</h1>"
