"""
Telegram Notifier for Trading Agent

Sends trade notifications, morning briefings, and alerts to Telegram.
"""

import os
import logging
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # Jörgen's DM


class TelegramNotifier:
    """Send trading notifications via Telegram."""

    def __init__(self):
        self.bot_token = BOT_TOKEN
        self.chat_id = CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            logger.warning("⚠️ Telegram notifier disabled (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    # ------------------------------------------------------------------
    # Trade notifications
    # ------------------------------------------------------------------

    def notify_trade(self, trade: Dict[str, Any]):
        """Notify about an executed trade."""
        action = trade.get("action", "?")
        ticker = trade.get("ticker", "?")
        price = trade.get("price", 0)
        shares = trade.get("shares", 0)
        total = trade.get("total_value", 0)
        confidence = trade.get("confidence", "?")
        reasoning = trade.get("reasoning", "")[:200]
        target = trade.get("target_price")
        stop_loss = trade.get("stop_loss")

        emoji = "🟢" if action == "BUY" else "🔴"
        
        msg = (
            f"{emoji} <b>{action} {ticker}</b>\n"
            f"💰 {shares:.1f} aktier @ {price:.2f} SEK = {total:.0f} kr\n"
            f"📊 Confidence: {confidence}%\n"
            f"💡 {reasoning}\n"
        )
        if target:
            msg += f"🎯 Target: {target:.2f} | 🛑 SL: {stop_loss:.2f}\n"

        self._send(msg)

    def notify_auto_sell(self, ticker: str, shares: float, price: float, reason: str, pnl_pct: float):
        """Notify about an automatic sell (stop-loss or take-profit)."""
        emoji = "🟢" if pnl_pct > 0 else "🔴"
        msg = (
            f"{emoji} <b>AUTO-SELL {ticker}</b>\n"
            f"💰 {shares:.1f} aktier @ {price:.2f} SEK\n"
            f"📈 P&L: {pnl_pct:+.1f}%\n"
            f"⚡ {reason}"
        )
        self._send(msg)

    # ------------------------------------------------------------------
    # Briefings
    # ------------------------------------------------------------------

    def notify_morning_briefing(self, outlook: str, decisions: List[Dict], portfolio_value: float, pnl: float):
        """Send morning briefing."""
        msg = f"🌅 <b>Morning Briefing</b>\n\n"
        msg += f"📊 Outlook: <b>{outlook.upper()}</b>\n"
        msg += f"💰 Portfölj: {portfolio_value:.0f} kr (P&L: {pnl:+.0f} kr)\n\n"

        if decisions:
            msg += "<b>Planerade trades:</b>\n"
            for d in decisions[:5]:
                action = d.get("action", "?")
                ticker = d.get("ticker", "?")
                conf = d.get("confidence", "?")
                reason = d.get("reason", "")[:80]
                emoji = "🟢" if action == "BUY" else "🔴"
                msg += f"{emoji} {action} {ticker} ({conf}%) — {reason}\n"
        else:
            msg += "Inga trades planerade idag.\n"

        self._send(msg)

    def notify_daily_summary(self, summary: str, portfolio_value: float, pnl: float, trades_today: int):
        """Send end-of-day summary."""
        msg = (
            f"🌆 <b>Dagssummering</b>\n\n"
            f"💰 Portfölj: {portfolio_value:.0f} kr\n"
            f"📈 P&L idag: {pnl:+.0f} kr\n"
            f"🔄 Trades idag: {trades_today}\n\n"
            f"{summary[:500]}"
        )
        self._send(msg)

    def notify_ta_alerts(self, alerts: List[Dict]):
        """Send notable TA alerts (only strong signals)."""
        if not alerts:
            return
        
        # Only send strong signals
        strong = [a for a in alerts if a.get("rsi", 50) < 30 or a.get("rsi", 50) > 80]
        if not strong:
            return

        msg = "📊 <b>Starka TA-signaler:</b>\n\n"
        for a in strong[:8]:
            ticker = a.get("ticker", "?")
            rsi = a.get("rsi", 0)
            signal_type = a.get("type", "")
            emoji = "🔴" if rsi > 70 else "🟢"
            msg += f"{emoji} <b>{ticker}</b> RSI={rsi:.0f} — {signal_type}\n"

        self._send(msg)

    def notify_error(self, error_msg: str):
        """Send error notification."""
        self._send(f"⚠️ <b>Trading Agent Error</b>\n\n{error_msg[:500]}")
