"""
Módulo del Notificador de Telegram.
Gestiona el envío de alertas formateadas, control de sonido/silencioso y resúmenes diarios.
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import requests

from .config_loader import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Cliente para la API de Bots de Telegram.
    Soporta:
    - Alertas críticas / operativas con sonido (disable_notification=False).
    - Alertas periódicas o heartbeat silenciosas (disable_notification=True).
    - Resúmenes diarios de balance y rendimiento.
    """

    BASE_URL = "https://api.telegram.org/bot"

    def __init__(self, config: TelegramConfig):
        self.config = config
        self.enabled = config.enabled and bool(config.bot_token) and bool(config.chat_id)
        if not self.enabled:
            logger.info("Telegram Notifier desactivado o credenciales no configuradas.")

    def send_message(self, text: str, disable_notification: bool = False, parse_mode: str = "HTML") -> bool:
        """
        Envía un mensaje formateado a través de la API de Telegram.
        """
        if not self.enabled:
            logger.debug(f"[Telegram Mock] (silent={disable_notification}):\n{text}")
            return False

        url = f"{self.BASE_URL}{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
            "disable_web_page_preview": True
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            else:
                logger.warning(f"Error al enviar mensaje a Telegram (HTTP {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"Excepción al conectar con Telegram API: {e}")
            return False

    def notify_buy_order(
        self,
        symbol: str,
        price: float,
        amount: float,
        cost: float,
        sl_price: float,
        tp_price: float,
        is_dry_run: bool,
        reason: str
    ) -> bool:
        """Envía alerta con sonido de nueva orden de compra ejecutada."""
        mode_tag = "🧪 [SIMULACIÓN / DRY-RUN]" if is_dry_run else "🟢 [REAL TRADING]"
        text = (
            f"<b>{mode_tag} NUEVA COMPRA EJECUTADA</b>\n\n"
            f"🔹 <b>Símbolo:</b> <code>{symbol}</code>\n"
            f"💰 <b>Precio Entrada:</b> <code>{price:.4f}</code>\n"
            f"📊 <b>Cantidad:</b> <code>{amount:.6f}</code> ({cost:.2f} USDT)\n"
            f"🛡 <b>Stop Loss Inicial:</b> <code>{sl_price:.4f}</code>\n"
            f"🎯 <b>Take Profit:</b> <code>{tp_price:.4f}</code>\n\n"
            f"💡 <i>Motivo:</i> {reason}\n"
            f"⏰ <i>Fecha:</i> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        return self.send_message(text, disable_notification=False)

    def notify_sell_order(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl_amount: float,
        pnl_percent: float,
        exit_reason: str,
        is_dry_run: bool
    ) -> bool:
        """Envía alerta con sonido de cierre de posición."""
        mode_tag = "🧪 [SIMULACIÓN]" if is_dry_run else "🟢 [REAL]"
        emoji = "🚀" if pnl_amount >= 0 else "🔻"
        status_text = "GANANCIA (PROFIT)" if pnl_amount >= 0 else "PÉRDIDA (LOSS)"

        text = (
            f"<b>{mode_tag} {emoji} POSICIÓN CERRADA - {status_text}</b>\n\n"
            f"🔹 <b>Símbolo:</b> <code>{symbol}</code>\n"
            f"📍 <b>Razón de Salida:</b> <b>{exit_reason}</b>\n"
            f"📥 <b>Precio Entrada:</b> <code>{entry_price:.4f}</code>\n"
            f"📤 <b>Precio Salida:</b> <code>{exit_price:.4f}</code>\n"
            f"💵 <b>PnL Neto:</b> <code>{pnl_amount:+.2f} USDT ({pnl_percent:+.2f}%)</code>\n\n"
            f"⏰ <i>Fecha:</i> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        return self.send_message(text, disable_notification=False)

    def notify_circuit_breaker(self, message: str) -> bool:
        """Alerta crítica con sonido sobre activación de cortocircuito."""
        text = (
            f"⚠️ <b>ALERTA DE SEGURIDAD: CORTOCIRCUITO ACTIVADO</b> ⚠️\n\n"
            f"{message}\n\n"
            f"🛑 <i>Nuevas compras pausadas automáticamente para preservar el capital.</i>\n"
            f"⏰ <i>Fecha:</i> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        return self.send_message(text, disable_notification=False)

    def notify_heartbeat(
        self,
        open_positions: int,
        free_balance: float,
        total_balance: float,
        is_dry_run: bool
    ) -> bool:
        """Chequeo de estado periódico silencioso (disable_notification=True)."""
        mode_tag = "🧪 SIMULACIÓN" if is_dry_run else "🟢 REAL"
        text = (
            f"💓 <b>Bot Status Heartbeat ({mode_tag})</b>\n\n"
            f"💼 <b>Balance Total:</b> <code>{total_balance:.2f} USDT</code>\n"
            f"💵 <b>Balance Disponible:</b> <code>{free_balance:.2f} USDT</code>\n"
            f"📈 <b>Posiciones Abiertas:</b> <code>{open_positions}</code>\n\n"
            f"🕒 <i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} - Operando con normalidad.</i>"
        )
        return self.send_message(text, disable_notification=True)

    def notify_daily_report(
        self,
        summary: Dict[str, Any],
        current_balance: float,
        is_dry_run: bool
    ) -> bool:
        """Reporte diario estructurado con métricas de rendimiento del día."""
        mode_tag = "🧪 SIMULACIÓN" if is_dry_run else "🟢 REAL"
        pnl = summary.get("total_pnl", 0.0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        text = (
            f"📊 <b>REPORTE DIARIO DE RENDIMIENTO ({mode_tag})</b>\n\n"
            f"💰 <b>Balance Actual:</b> <code>{current_balance:.2f} USDT</code>\n"
            f"{pnl_emoji} <b>PnL Total Hoy:</b> <code>{pnl:+.2f} USDT</code>\n"
            f"🔢 <b>Operaciones Hoy:</b> <code>{summary.get('count', 0)}</code>\n"
            f"✅ <b>Ganadoras:</b> <code>{summary.get('win_count', 0)}</code>\n"
            f"❌ <b>Perdedoras:</b> <code>{summary.get('loss_count', 0)}</code>\n"
            f"🎯 <b>Win Rate:</b> <code>{summary.get('win_rate', 0.0):.1f}%</code>\n"
            f"🏷 <b>Comisiones:</b> <code>{summary.get('total_fees', 0.0):.4f} USDT</code>\n\n"
            f"⏰ <i>Fecha:</i> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        return self.send_message(text, disable_notification=False)
