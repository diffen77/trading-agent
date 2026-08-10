"""
Paper Trader

Handles paper trading simulation:
- Executes trades (simulated)
- Tracks portfolio
- Logs with reasoning
- Extracts learnings
"""

import logging
import math
import hashlib
import os
from time import sleep
from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ..data.market_data import (
    FreshnessPolicy,
    MarketDataError,
    assert_fresh_quote,
)
from .notifier import TelegramNotifier
from .risk import evaluate_exit
from .strategy import ActiveStrategy

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperTrader:
    """Simulated paper trading engine."""
    
    def __init__(self, db):
        self.db = db
        self.notifier = TelegramNotifier()

    def get_portfolio_value(self) -> Dict[str, Any]:
        """Calculate portfolio value from authorized provider quotes only."""
        balance = self.db.get_balance()
        portfolio = self.db.get_portfolio()
        
        cash = float(balance['cash'])
        positions_value = 0
        price_marks = []
        
        if not portfolio.empty:
            for _, pos in portfolio.iterrows():
                ticker = pos['ticker']
                shares = float(pos['shares'])
                quote = self.db.get_latest_authorized_market_quote(ticker)
                if (
                    quote is None
                    or (quote.quote_id is None) == (
                        quote.book_state_id is None
                    )
                ):
                    raise MarketDataError(
                        f"{ticker} is missing an authorized provider quote"
                    )
                current_price = float(quote.last_price)
                if (
                    not math.isfinite(shares)
                    or shares <= 0
                    or not math.isfinite(current_price)
                    or current_price <= 0
                ):
                    raise MarketDataError(
                        f"{ticker} has invalid portfolio valuation data"
                    )
                positions_value += shares * current_price
                price_marks.append({
                    'ticker': ticker,
                    'quote_id': quote.quote_id,
                    'book_state_id': quote.book_state_id,
                    'source': quote.source,
                    'event_time': quote.event_time,
                    'price': current_price,
                })
        
        total_value = cash + positions_value
        
        return {
            'cash': cash,
            'positions_value': positions_value,
            'total_value': total_value,
            'pnl': total_value - 20000,  # Starting capital
            'pnl_pct': ((total_value / 20000) - 1) * 100,
            'price_marks': price_marks,
        }
    
    def execute_trade(self, opportunity: Dict[str, Any]) -> bool:
        """
        Execute a paper trade.
        
        opportunity = {
            'ticker': 'VOLV-B',
            'action': 'BUY',
            'reasoning': 'Stålpriser ner, bättre marginaler',
            'confidence': 75,
            'hypothesis': 'Kursen stiger 5-10% inom 2 veckor',
            'position_size': 2000,  # SEK
        }
        """
        ticker = opportunity['ticker']
        action = opportunity.get('action', 'BUY')
        position_size = opportunity.get('position_size', 2000)
        idempotency_key = opportunity.get('idempotency_key')
        if not isinstance(idempotency_key, str) or not idempotency_key:
            logger.error(f"Missing idempotency key for {action} {ticker}")
            return False
        
        # Every paper order must carry exactly one freshness-validated
        # market evidence record: either a last-trade quote or an executable
        # two-sided pre-trade book state.
        current_price = opportunity.get('execution_price')
        source_quote_id = opportunity.get('source_quote_id')
        source_book_state_id = opportunity.get('source_book_state_id')
        source_ids = (source_quote_id, source_book_state_id)
        valid_source_ids = [
            value
            for value in source_ids
            if (
                not isinstance(value, bool)
                and isinstance(value, int)
                and value > 0
            )
        ]
        invalid_source_id = any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            )
            for value in source_ids
        )
        if (
            current_price is None
            or len(valid_source_ids) != 1
            or invalid_source_id
        ):
            logger.error(
                f"Missing or ambiguous exact market evidence for "
                f"{action} {ticker}"
            )
            return False
        current_price = float(current_price)
        if not math.isfinite(current_price) or current_price <= 0:
            logger.error(f"Invalid price data for {ticker}")
            return False

        if action == 'BUY':
            try:
                portfolio_value = self.get_portfolio_value()
                entry_risk = self.db.evaluate_entry_risk(
                    total_value=portfolio_value['total_value'],
                    evaluated_at=datetime.now(timezone.utc),
                )
            except Exception as exc:
                logger.error(
                    f"Entry risk guard unavailable for {ticker}: {exc}"
                )
                return False
            if not entry_risk.allowed:
                logger.warning(
                    f"Entry blocked for {ticker}: {entry_risk.reason} "
                    f"(daily return "
                    f"{entry_risk.daily_return_pct:.2f}%)"
                )
                return False

        shares = position_size / current_price
        
        # Check we have enough cash for buys
        if action == 'BUY':
            balance = self.db.get_balance()
            if balance['cash'] < position_size:
                logger.warning(f"Insufficient cash for {ticker}: need {position_size}, have {balance['cash']}")
                return False
        
        # Generate hypothesis if not provided
        hypothesis = opportunity.get('hypothesis')
        if not hypothesis:
            # Build specific hypothesis from impacts
            impacts = opportunity.get('impacts', [])
            positive_factors = [i['reason'] for i in impacts if i.get('direction') == 'positive']
            
            if positive_factors:
                factors_text = ', '.join(positive_factors[:2])
                hypothesis = f"Förväntar +5-10% inom 2 veckor. Triggers: {factors_text}"
            else:
                hypothesis = f"Förväntar +5-10% inom 2 veckor baserat på sektoranalys och momentum"
        
        target_pct = float(opportunity.get('target_pct', 10))
        stop_loss_pct = float(opportunity.get('stop_loss_pct', -5))
        if (
            not math.isfinite(target_pct)
            or not 0 < target_pct <= 100
        ):
            logger.error(f"Invalid target percentage for {ticker}")
            return False
        if (
            not math.isfinite(stop_loss_pct)
            or not -50 <= stop_loss_pct < 0
        ):
            logger.error(f"Invalid stop-loss percentage for {ticker}")
            return False
        target_price = current_price * (1 + target_pct / 100)
        stop_loss_price = current_price * (1 + stop_loss_pct / 100)
        
        # Log the trade
        trade = {
            'ticker': ticker,
            'action': action,
            'shares': shares,
            'price': current_price,
            'total_value': position_size,
            'reasoning': opportunity.get('reasoning', opportunity.get('thesis', 'Autonom handel')),
            'confidence': opportunity.get('confidence'),
            'hypothesis': hypothesis,
            'macro_context': opportunity.get('macro_context', {}),
            'target_price': target_price,
            'stop_loss': stop_loss_price,
            'target_pct': target_pct,
            'stop_loss_pct': stop_loss_pct,
            'idempotency_key': idempotency_key,
            'source_quote_id': source_quote_id,
            'source_book_state_id': source_book_state_id,
            'decision_id': opportunity.get('decision_id'),
            'decision_origin': opportunity.get(
                'decision_origin',
                'MANUAL',
            ),
            'strategy_version': opportunity.get(
                'strategy_version',
                'legacy-unversioned',
            ),
        }
        
        result = self.db.log_trade_result(trade)
        if not result.inserted:
            logger.info(
                f"Duplicate paper order ignored: {action} {ticker} "
                f"(trade {result.trade_id})"
            )
            return False
        
        logger.info(f"   📈 Target: {target_price:.2f} (+{target_pct}%) | 📉 Stop-loss: {stop_loss_price:.2f} ({stop_loss_pct}%)")
        
        logger.info(f"🤖 AGENT TRADE: {action} {shares:.2f} {ticker} @ {current_price:.2f} SEK")
        logger.info(f"   Confidence: {opportunity.get('confidence', 'N/A')}%")
        logger.info(f"   Reasoning: {trade['reasoning'][:100]}...")
        
        return True
    
    def auto_trade(
        self,
        opportunities: List[Dict],
        *,
        cycle_key: str,
        strategy: ActiveStrategy | None = None,
        min_confidence: float | None = None,
        max_positions: int | None = None,
        position_size: float = 2000,
    ) -> List[Dict]:
        """
        Autonomous trading based on opportunities.
        
        Rules:
        - Only trade if confidence >= min_confidence
        - Max max_positions open at a time
        - Fixed position_size per trade
        - Don't buy same stock twice
        - Stable order identity within one autonomous cycle
        """
        if not isinstance(cycle_key, str) or not cycle_key.strip():
            raise ValueError("cycle_key is required for trade idempotency")
        active_strategy = strategy or self.db.get_active_strategy()
        if min_confidence is None:
            min_confidence = active_strategy.config.min_confidence
        if max_positions is None:
            max_positions = active_strategy.config.max_positions

        executed = []
        
        # Get current positions
        portfolio = self.db.get_portfolio()
        current_tickers = set(portfolio['ticker'].tolist()) if not portfolio.empty else set()
        num_positions = len(current_tickers)
        
        # Filter and sort opportunities
        tradeable = [
            o for o in opportunities 
            if o['confidence'] >= min_confidence 
            and o['ticker'] not in current_tickers
        ]
        tradeable.sort(key=lambda x: x['confidence'], reverse=True)
        
        # Execute trades
        for source_opp in tradeable:
            if num_positions >= max_positions:
                logger.info(
                    f"Max positions ({max_positions}) reached, "
                    f"skipping {source_opp['ticker']}"
                )
                break

            opp = dict(source_opp)
            opp['position_size'] = position_size
            opp['action'] = 'BUY'
            key_material = (
                f"{cycle_key.strip()}|BUY|{opp['ticker'].strip().upper()}"
            ).encode('utf-8')
            opp['idempotency_key'] = (
                "auto:"
                + hashlib.sha256(key_material).hexdigest()[:40]
            )
            opp['strategy_version'] = active_strategy.version
            opp['decision_origin'] = 'AUTOMATED_SCAN'
            opp['target_pct'] = active_strategy.config.take_profit_pct
            opp['stop_loss_pct'] = active_strategy.config.stop_loss_pct
            
            if self.execute_trade(opp):
                executed.append(opp)
                num_positions += 1
                logger.info(f"✅ Executed: {opp['ticker']} @ {opp['confidence']:.0f}% confidence")
        
        if executed:
            logger.info(f"🤖 Agent executed {len(executed)} trades")
        else:
            logger.info(f"🤖 No trades executed (min confidence: {min_confidence}%)")
        
        return executed
    
    def _execute_auto_sell(
        self,
        ticker: str,
        shares: float,
        current_price: float,
        reason: str,
        opened_at: datetime,
        strategy_version: str,
        source_quote_id: int | None,
        source_book_state_id: int | None,
    ) -> bool:
        """Execute automatic sell order."""
        try:
            key_material = (
                f"{ticker}|{opened_at.isoformat()}|{reason}"
            ).encode('utf-8')
            idempotency_key = (
                "auto-exit:"
                + hashlib.sha256(key_material).hexdigest()[:40]
            )
            trade = {
                'ticker': ticker,
                'action': 'SELL',
                'shares': shares,
                'price': current_price,
                'total_value': shares * current_price,
                'reasoning': f"AUTO-SELL: {reason}",
                'confidence': 99,
                'hypothesis': f"Automatic exit: {reason}",
                'macro_context': {},
                'target_price': None,
                'stop_loss': None,
                'target_pct': 0,
                'stop_loss_pct': 0,
                'idempotency_key': idempotency_key,
                'strategy_version': strategy_version,
                'source_quote_id': source_quote_id,
                'source_book_state_id': source_book_state_id,
                'decision_id': None,
                'decision_origin': 'MECHANICAL_EXIT',
            }
            result = self.db.log_trade_result(trade)
            if not result.inserted:
                logger.info(
                    f"Duplicate automatic exit ignored for {ticker} "
                    f"(trade {result.trade_id})"
                )
                return False
            logger.info(f"✅ AUTO-SELL executed: {shares:.2f} {ticker} @ {current_price:.2f} SEK")
            return True
        except Exception as e:
            logger.error(f"Error executing auto-sell for {ticker}: {e}")
            return False
    
    def _update_trailing_stop(
        self,
        ticker: str,
        new_stop_loss: float,
        trailing_floor_pct: float,
    ):
        """Update trailing stop-loss in trades table."""
        try:
            # Update the most recent open trade for this ticker using subquery
            self.db.execute("""
                UPDATE trades SET 
                    stop_loss = %s,
                    stop_loss_pct = %s
                WHERE id = (
                    SELECT id FROM trades 
                    WHERE ticker = %s 
                    AND closed_at IS NULL 
                    AND action = 'BUY'
                    ORDER BY executed_at DESC 
                    LIMIT 1
                )
            """, (new_stop_loss, trailing_floor_pct, ticker))
            
            logger.info(
                f"📈 {ticker}: Trailing stop updated to "
                f"+{trailing_floor_pct:g}% ({new_stop_loss:.2f} SEK)"
            )
        except Exception as e:
            logger.error(f"Error updating trailing stop for {ticker}: {e}")
    
    def check_positions(
        self,
        *,
        now: datetime | None = None,
    ):
        """Check positions using one timezone-aware decision instant."""
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        checked_at = checked_at.astimezone(timezone.utc)

        try:
            strategy = self.db.get_active_strategy()
        except Exception as exc:
            logger.error(
                f"Cannot evaluate positions without an active strategy: {exc}"
            )
            return
        config = strategy.config
        portfolio = self.db.get_portfolio()
        
        if portfolio.empty:
            logger.info("No open positions")
            return
        
        for _, pos in portfolio.iterrows():
            ticker = pos['ticker']
            shares = float(pos['shares'])
            avg_price = float(pos['avg_price'])
            position_checked_at = checked_at
            if shares <= 0:
                continue
            
            execution_reader = getattr(
                self.db,
                "get_latest_authorized_execution_quote",
                None,
            )
            if callable(execution_reader):
                quote = None
                for attempt in range(4):
                    quote = execution_reader(
                        ticker,
                        action="SELL",
                        now=position_checked_at,
                    )
                    if quote is not None:
                        break
                    if attempt < 3:
                        sleep(5)
                        position_checked_at = _utc_now()
            else:
                quote = self.db.get_latest_authorized_market_quote(ticker)
            if (
                quote is None
                or (quote.quote_id is None) == (
                    quote.book_state_id is None
                )
            ):
                logger.error(
                    f"Cannot evaluate exits for {ticker}: "
                    "fresh source quote is missing"
                )
                continue
            try:
                max_delay = int(
                    os.getenv("MARKET_DATA_MAX_DELAY_MINUTES", "15")
                )
                tolerance = int(
                    os.getenv("MARKET_DATA_TOLERANCE_MINUTES", "2")
                )
                assert_fresh_quote(
                    quote,
                    now=position_checked_at,
                    policy=FreshnessPolicy(
                        max_delay=timedelta(minutes=max_delay),
                        tolerance=timedelta(minutes=tolerance),
                    ),
                )
            except (MarketDataError, ValueError) as exc:
                logger.error(
                    f"Cannot evaluate exits for {ticker}: {exc}"
                )
                continue
            current_price = float(quote.last_price)

            open_trades = self.db.query("""
                SELECT executed_at, stop_loss
                FROM trades
                WHERE ticker = :ticker
                  AND action = 'BUY'
                  AND closed_at IS NULL
                ORDER BY executed_at
                LIMIT 1
            """, {'ticker': ticker})
            if not open_trades:
                logger.error(
                    f"Cannot evaluate exits for {ticker}: open BUY trade is missing"
                )
                continue

            opened_at = open_trades[0]['executed_at']
            if isinstance(opened_at, str):
                opened_at = datetime.fromisoformat(
                    opened_at.replace('Z', '+00:00')
                )
            if (
                not isinstance(opened_at, datetime)
                or opened_at.tzinfo is None
                or opened_at.utcoffset() is None
            ):
                logger.error(
                    f"Cannot evaluate exits for {ticker}: "
                    "open trade timestamp must be timezone-aware"
                )
                continue
            opened_at = opened_at.astimezone(timezone.utc)
            stored_stop = open_trades[0].get('stop_loss')
            decision = evaluate_exit(
                entry_price=avg_price,
                current_price=current_price,
                opened_at=opened_at,
                now=position_checked_at,
                stored_stop_loss=(
                    float(stored_stop) if stored_stop is not None else None
                ),
                stop_loss_pct=config.stop_loss_pct,
                take_profit_pct=config.take_profit_pct,
                trailing_activation_pct=config.trailing_activation_pct,
                trailing_floor_pct=config.trailing_floor_pct,
                time_stop_days=config.time_stop_days,
                time_stop_min_gain_pct=config.time_stop_min_gain_pct,
            )

            if decision.should_sell:
                reason_labels = {
                    'STOP_LOSS': 'Stop-loss triggered',
                    'TAKE_PROFIT': 'Take-profit triggered',
                    'TRAILING_STOP': 'Trailing stop triggered',
                    'TIME_STOP': (
                        f'{config.time_stop_days}-day time stop triggered'
                    ),
                }
                reason = reason_labels[decision.reason]
                logger.warning(
                    f"{ticker}: {reason} ({decision.pnl_pct:.1f}%) "
                    "- EXECUTING SELL"
                )
                if self._execute_auto_sell(
                    ticker,
                    shares,
                    current_price,
                    reason,
                    opened_at,
                    strategy.version,
                    quote.quote_id,
                    quote.book_state_id,
                ):
                    self.notifier.notify_auto_sell(
                        ticker,
                        shares,
                        current_price,
                        reason,
                        decision.pnl_pct,
                    )
            elif decision.new_stop_loss is not None:
                logger.info(
                    f"{ticker}: trailing stop activated at "
                    f"{decision.pnl_pct:.1f}%"
                )
                self._update_trailing_stop(
                    ticker,
                    decision.new_stop_loss,
                    config.trailing_floor_pct,
                )
    
    def log_daily_performance(self):
        """Log end of day performance."""
        portfolio = self.get_portfolio_value()
        
        logger.info("📊 Daily Performance")
        logger.info(f"   Cash: {portfolio['cash']:.2f} SEK")
        logger.info(f"   Positions: {portfolio['positions_value']:.2f} SEK")
        logger.info(f"   Total: {portfolio['total_value']:.2f} SEK")
        logger.info(f"   P&L: {portfolio['pnl']:.2f} SEK ({portfolio['pnl_pct']:.2f}%)")
    
    def record_trade_outcome(self, trade_id: int, current_price: float, entry_price: float, shares: float):
        """
        Reject the legacy arbitrary-price checkpoint path.

        Hypotheses are evaluated by ``validate_hypotheses`` against the first
        authorized official-session bar after the registered horizon. Accepting
        a caller-provided price here would bypass provider provenance.
        """
        raise RuntimeError(
            "record_trade_outcome is disabled; use validate_hypotheses "
            "with authorized historical bars"
        )

    def run_weekly_review(
        self,
        *,
        now: datetime | None = None,
    ) -> Dict[str, Any]:
        """
        Review positions whose FIFO result was fully realized in the last
        seven days. Each completed BUY entry is counted exactly once.
        """
        logger.info("📝 Running weekly review...")

        reviewed_at = now or datetime.now(timezone.utc)
        if (
            reviewed_at.tzinfo is None
            or reviewed_at.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        reviewed_at = reviewed_at.astimezone(timezone.utc)
        review_start = reviewed_at - timedelta(days=7)
        week_start = review_start.date()
        week_end = reviewed_at.date()

        # A completed BUY owns the allocation-level net P&L. SELL rows carry
        # the same realization and must not be counted a second time.
        try:
            week_trades = self.db.query("""
                SELECT
                    t.*,
                    COALESCE(c.sector, 'Unknown') AS sector
                FROM trades t
                LEFT JOIN companies c ON c.ticker = t.ticker
                WHERE t.action = 'BUY'
                  AND t.closed_at IS NOT NULL
                  AND t.pnl IS NOT NULL
                  AND t.closed_at >= :start
                  AND t.closed_at <= :end
                ORDER BY t.closed_at, t.id
            """, {
                'start': review_start,
                'end': reviewed_at,
            })
        except Exception:
            week_trades = []
        
        if not week_trades:
            logger.info("No trades this week")
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0.0,
                'win_rate': 0.0,
            }
        
        # Analyze results
        total = len(week_trades)
        winning = sum(1 for t in week_trades if float(t.get('pnl', 0) or 0) > 0)
        losing = sum(1 for t in week_trades if float(t.get('pnl', 0) or 0) < 0)
        total_pnl = sum(float(t.get('pnl', 0) or 0) for t in week_trades)
        win_rate = (winning / total * 100) if total > 0 else 0
        
        # Sector breakdown
        sector_pnl = {}
        for t in week_trades:
            pnl = float(t.get('pnl', 0) or 0)
            sector = t.get('sector') or 'Unknown'
            sector_pnl[sector] = sector_pnl.get(sector, 0) + pnl
        
        # Best and worst
        best = max(week_trades, key=lambda t: float(t.get('pnl', 0) or 0))
        worst = min(week_trades, key=lambda t: float(t.get('pnl', 0) or 0))
        
        # Generate reflection
        patterns = []
        adjustments = []
        
        if win_rate < 40:
            adjustments.append("Höj confidence-tröskel — för många förluster")
        if win_rate > 70:
            patterns.append("Bra träffsäkerhet — behåll strategi")
        
        # Check if any sector consistently loses
        for sector, pnl in sector_pnl.items():
            if pnl < -100:
                adjustments.append(f"Undvik {sector} — negativ vecka ({pnl:.0f} kr)")
            elif pnl > 100:
                patterns.append(f"{sector} funkar bra (+{pnl:.0f} kr)")
        
        reflection = (
            f"Vecka {week_start} - {week_end}: {total} trades, "
            f"{winning}W/{losing}L, winrate {win_rate:.0f}%, "
            f"PnL {total_pnl:+.0f} kr. "
            f"Bäst: {best['ticker']} ({float(best.get('pnl', 0) or 0):+.0f} kr), "
            f"Sämst: {worst['ticker']} ({float(worst.get('pnl', 0) or 0):+.0f} kr)."
        )
        
        # Save review
        try:
            self.db.execute("""
                INSERT INTO reviews (week_start, week_end, total_trades, winning_trades, 
                    losing_trades, total_pnl, win_rate, patterns_identified, 
                    strategy_adjustments, reflection)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (week_start) DO UPDATE SET
                    total_trades = EXCLUDED.total_trades,
                    winning_trades = EXCLUDED.winning_trades,
                    losing_trades = EXCLUDED.losing_trades,
                    total_pnl = EXCLUDED.total_pnl,
                    win_rate = EXCLUDED.win_rate,
                    patterns_identified = EXCLUDED.patterns_identified,
                    strategy_adjustments = EXCLUDED.strategy_adjustments,
                    reflection = EXCLUDED.reflection
            """, (
                str(week_start), str(week_end), total, winning, losing,
                total_pnl, win_rate,
                '{' + ','.join(f'"{p}"' for p in patterns) + '}',
                '{' + ','.join(f'"{a}"' for a in adjustments) + '}',
                reflection
            ))
        except Exception as e:
            logger.error(f"Error saving review: {e}")
        
        logger.info(f"📝 {reflection}")
        logger.info(f"   Patterns: {patterns}")
        logger.info(f"   Adjustments: {adjustments}")
        logger.info("Weekly review complete")
        return {
            'total_trades': total,
            'winning_trades': winning,
            'losing_trades': losing,
            'total_pnl': total_pnl,
            'win_rate': win_rate,
        }
    
    def extract_learnings(self):
        """Extract learnings from recent trades."""
        trades = self.db.get_trades(limit=20)
        
        if trades.empty:
            return
        
        # Significant outcome-linked learnings are created by
        # validate_hypotheses. Keep this scheduled compatibility hook
        # side-effect free until a separately governed aggregate-learning
        # contract exists.
        logger.info("Learning extraction complete")
    
    def validate_hypotheses(
        self,
        days_to_check: int = 14,
        *,
        now: datetime | None = None,
    ) -> List[Dict]:
        """
        Check past hypotheses against the first governed session bar at or
        after the requested horizon.

        Outcome metadata is separate from immutable ledger P&L.
        """
        if (
            isinstance(days_to_check, bool)
            or not isinstance(days_to_check, int)
            or not 1 <= days_to_check <= 365
        ):
            raise ValueError("days_to_check must be between 1 and 365")
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        checked_at = checked_at.astimezone(timezone.utc)
        stockholm = ZoneInfo("Europe/Stockholm")

        logger.info(f"🔍 Validating hypotheses (trades older than {days_to_check} days)...")
        
        # Get trades that need validation
        trades = self.db.get_trades(limit=50)
        validated = []
        
        if trades.empty:
            return validated
        
        for _, trade in trades.iterrows():
            # Skip if already validated
            if trade.get('outcome_correct') is not None:
                continue
            
            # Skip if too recent
            trade_date = trade['executed_at']
            if isinstance(trade_date, str):
                trade_date = datetime.fromisoformat(trade_date.replace('Z', '+00:00'))
            if trade_date.tzinfo is None:
                trade_date = trade_date.replace(tzinfo=timezone.utc)
            else:
                trade_date = trade_date.astimezone(timezone.utc)

            evaluation_at = trade_date + timedelta(days=days_to_check)
            if checked_at < evaluation_at:
                continue

            # Use the first authorized official-session close at/after the
            # hypothesis horizon so a delayed rerun cannot change the answer.
            ticker = trade['ticker']
            entry_price = float(trade['price'])
            target_date = evaluation_at.astimezone(stockholm).date()
            bars = self.db.get_authorized_daily_bars(ticker, limit=250)
            eligible_bars = []
            for bar in bars:
                bar_date = bar.get("date")
                event_time = bar.get("event_time")
                if isinstance(bar_date, str):
                    try:
                        bar_date = datetime.strptime(
                            bar_date,
                            "%Y-%m-%d",
                        ).date()
                    except ValueError:
                        continue
                if (
                    bar_date is None
                    or bar_date < target_date
                    or not isinstance(event_time, datetime)
                    or event_time.tzinfo is None
                    or event_time.utcoffset() is None
                    or event_time.astimezone(timezone.utc) > checked_at
                ):
                    continue
                eligible_bars.append((bar_date, event_time, bar))
            if not eligible_bars:
                continue

            evaluation_date, event_time, evaluation_bar = min(
                eligible_bars,
                key=lambda item: (
                    item[0],
                    item[1].astimezone(timezone.utc),
                ),
            )
            current_price = float(evaluation_bar["close"])
            if (
                not math.isfinite(entry_price)
                or entry_price <= 0
                or not math.isfinite(current_price)
                or current_price <= 0
            ):
                continue
            pnl_pct = ((current_price / entry_price) - 1) * 100
            
            # Determine if hypothesis was correct
            # For BUY: correct if price went up
            action = trade['action']
            if action == 'BUY':
                correct = pnl_pct > 0
            else:  # SELL
                correct = pnl_pct < 0
            
            outcome = (
                f"{'Korrekt' if correct else 'Fel'}. "
                f"Pris: {entry_price:.2f} → {current_price:.2f} "
                f"({pnl_pct:+.1f}%) vid officiell XSTO-session "
                f"{evaluation_date} från {evaluation_bar['source']}."
            )
            
            # Update trade with outcome
            try:
                self.db.execute("""
                    UPDATE trades SET 
                        outcome = %s,
                        outcome_correct = %s
                    WHERE id = %s
                """, (outcome, correct, int(trade['id'])))
                
                validated.append({
                    'ticker': ticker,
                    'correct': correct,
                    'pnl_pct': pnl_pct,
                    'hypothesis': trade.get('hypothesis', ''),
                    'outcome': outcome,
                })
                
                # Extract learning
                self._extract_learning_from_trade(trade, correct, pnl_pct)
                
                logger.info(f"  {'✅' if correct else '❌'} {ticker}: {outcome}")
                
            except Exception as e:
                logger.error(f"Error validating {ticker}: {e}")
        
        if validated:
            logger.info(f"📊 Validated {len(validated)} trades: {sum(1 for v in validated if v['correct'])}/{len(validated)} correct")
        
        return validated
    
    def _extract_learning_from_trade(self, trade, correct: bool, pnl_pct: float):
        """Extract a learning from a validated trade."""
        ticker = trade['ticker']
        reasoning = trade.get('reasoning', '')
        hypothesis = trade.get('hypothesis', '')
        
        if correct and pnl_pct > 5:
            # Strong win - learn what worked
            learning = {
                'category': 'pattern',
                'content': f"[FUNKAR] {ticker}: {reasoning[:100]}. Resultat: {pnl_pct:+.1f}%",
                'source_trade_ids': [int(trade['id'])],
                'confidence': min(80, 50 + pnl_pct),
            }
        elif not correct and pnl_pct < -5:
            # Strong loss - learn what didn't work
            learning = {
                'category': 'mistake',
                'content': f"[UNDVIK] {ticker}: {reasoning[:100]}. Resultat: {pnl_pct:+.1f}%",
                'source_trade_ids': [int(trade['id'])],
                'confidence': min(80, 50 + abs(pnl_pct)),
            }
        else:
            return  # Not significant enough to learn from
        
        try:
            self.db.add_learning(learning)
            logger.info(f"📚 Learning added: {learning['content'][:60]}...")
        except Exception as e:
            logger.error(f"Error adding learning: {e}")
