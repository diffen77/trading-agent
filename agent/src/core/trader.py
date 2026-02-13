"""
Paper Trader

Handles paper trading simulation:
- Executes trades (simulated)
- Tracks portfolio
- Logs with reasoning
- Extracts learnings
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


class PaperTrader:
    """Simulated paper trading engine."""
    
    def __init__(self, db):
        self.db = db
        
    def get_portfolio_value(self) -> Dict[str, Any]:
        """Calculate current portfolio value."""
        balance = self.db.get_balance()
        portfolio = self.db.get_portfolio()
        
        cash = balance['cash']
        positions_value = 0
        
        if not portfolio.empty:
            # Get latest prices for positions
            tickers = portfolio['ticker'].tolist()
            prices = self.db.get_latest_prices(tickers)
            
            for _, pos in portfolio.iterrows():
                ticker = pos['ticker']
                price_row = prices[prices['ticker'] == ticker]
                if not price_row.empty:
                    current_price = float(price_row.iloc[0]['close'])
                    positions_value += float(pos['shares']) * current_price
        
        total_value = cash + positions_value
        
        return {
            'cash': cash,
            'positions_value': positions_value,
            'total_value': total_value,
            'pnl': total_value - 20000,  # Starting capital
            'pnl_pct': ((total_value / 20000) - 1) * 100,
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
        
        # Get current price
        prices = self.db.get_latest_prices([ticker])
        if prices.empty:
            logger.error(f"No price data for {ticker}")
            return False
            
        current_price = float(prices.iloc[0]['close'])
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
        
        # Calculate target and stop-loss
        target_pct = 10  # +10% target
        stop_loss_pct = -5  # -5% stop-loss
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
        }
        
        self.db.log_trade(trade)
        
        logger.info(f"   📈 Target: {target_price:.2f} (+{target_pct}%) | 📉 Stop-loss: {stop_loss_price:.2f} ({stop_loss_pct}%)")
        
        logger.info(f"🤖 AGENT TRADE: {action} {shares:.2f} {ticker} @ {current_price:.2f} SEK")
        logger.info(f"   Confidence: {opportunity.get('confidence', 'N/A')}%")
        logger.info(f"   Reasoning: {trade['reasoning'][:100]}...")
        
        return True
    
    def auto_trade(self, opportunities: List[Dict], min_confidence: float = 65, max_positions: int = 5, position_size: float = 2000) -> List[Dict]:
        """
        Autonomous trading based on opportunities.
        
        Rules:
        - Only trade if confidence >= min_confidence
        - Max max_positions open at a time
        - Fixed position_size per trade
        - Don't buy same stock twice
        """
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
        for opp in tradeable:
            if num_positions >= max_positions:
                logger.info(f"Max positions ({max_positions}) reached, skipping {opp['ticker']}")
                break
            
            opp['position_size'] = position_size
            opp['action'] = 'BUY'
            
            if self.execute_trade(opp):
                executed.append(opp)
                num_positions += 1
                logger.info(f"✅ Executed: {opp['ticker']} @ {opp['confidence']:.0f}% confidence")
        
        if executed:
            logger.info(f"🤖 Agent executed {len(executed)} trades")
        else:
            logger.info(f"🤖 No trades executed (min confidence: {min_confidence}%)")
        
        return executed
    
    def _execute_auto_sell(self, ticker: str, shares: float, current_price: float, reason: str):
        """Execute automatic sell order."""
        try:
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
            }
            self.db.log_trade(trade)
            logger.info(f"✅ AUTO-SELL executed: {shares:.2f} {ticker} @ {current_price:.2f} SEK")
        except Exception as e:
            logger.error(f"Error executing auto-sell for {ticker}: {e}")
    
    def _update_trailing_stop(self, ticker: str, avg_price: float, current_pnl_pct: float):
        """Update trailing stop-loss in trades table."""
        try:
            # New stop-loss at +2% from entry price
            new_stop_loss = avg_price * 1.02
            
            # Update the most recent open trade for this ticker using subquery
            self.db.execute("""
                UPDATE trades SET 
                    stop_loss = %s,
                    stop_loss_pct = 2.0
                WHERE id = (
                    SELECT id FROM trades 
                    WHERE ticker = %s 
                    AND closed_at IS NULL 
                    AND action = 'BUY'
                    ORDER BY executed_at DESC 
                    LIMIT 1
                )
            """, (new_stop_loss, ticker))
            
            logger.info(f"📈 {ticker}: Trailing stop updated to +2% ({new_stop_loss:.2f} SEK)")
        except Exception as e:
            logger.error(f"Error updating trailing stop for {ticker}: {e}")
    
    def check_positions(self):
        """Check current positions for stop-loss or take-profit."""
        portfolio = self.db.get_portfolio()
        
        if portfolio.empty:
            logger.info("No open positions")
            return
        
        for _, pos in portfolio.iterrows():
            ticker = pos['ticker']
            shares = float(pos['shares'])
            avg_price = float(pos['avg_price'])
            
            # Get current price
            prices = self.db.get_latest_prices([ticker])
            if prices.empty:
                continue
                
            current_price = float(prices.iloc[0]['close'])
            pnl_pct = ((current_price / avg_price) - 1) * 100
            
            # Check stop-loss (-5%) - EXECUTE ACTUAL SELL
            if pnl_pct <= -5:
                logger.warning(f"🔴 {ticker}: Stop-loss triggered ({pnl_pct:.1f}%) - EXECUTING SELL")
                self._execute_auto_sell(ticker, shares, current_price, "Stop-loss triggered")
                
            # Check take-profit (+10%) - EXECUTE ACTUAL SELL  
            elif pnl_pct >= 10:
                logger.info(f"🟢 {ticker}: Take-profit triggered ({pnl_pct:.1f}%) - EXECUTING SELL")
                self._execute_auto_sell(ticker, shares, current_price, "Take-profit triggered")
                
            # Implement trailing stop: vid +5%, flytta stop-loss till +2%
            elif pnl_pct >= 5:
                logger.info(f"📈 {ticker}: Trailing stop activated at +{pnl_pct:.1f}% - monitoring for +2% floor")
                # Check if we should update stop loss in trades table
                self._update_trailing_stop(ticker, avg_price, pnl_pct)
    
    def log_daily_performance(self):
        """Log end of day performance."""
        portfolio = self.get_portfolio_value()
        
        logger.info("📊 Daily Performance")
        logger.info(f"   Cash: {portfolio['cash']:.2f} SEK")
        logger.info(f"   Positions: {portfolio['positions_value']:.2f} SEK")
        logger.info(f"   Total: {portfolio['total_value']:.2f} SEK")
        logger.info(f"   P&L: {portfolio['pnl']:.2f} SEK ({portfolio['pnl_pct']:.2f}%)")
    
    def record_trade_outcome(self, trade_id: int, current_price: float, entry_price: float, shares: float):
        """Record a trade outcome checkpoint in trade_outcomes table."""
        from datetime import date as date_type
        try:
            # Get trade date
            trades = self.db.query(
                "SELECT executed_at FROM trades WHERE id = :id",
                {'id': trade_id}
            )
            if not trades:
                return
            
            trade_date = trades[0]['executed_at']
            if isinstance(trade_date, str):
                trade_date = datetime.fromisoformat(trade_date.replace('Z', '+00:00'))
            
            days_since = (datetime.now() - trade_date.replace(tzinfo=None)).days
            pnl_pct = ((current_price / entry_price) - 1) * 100
            pnl_amount = (current_price - entry_price) * shares
            
            self.db.execute("""
                INSERT INTO trade_outcomes (trade_id, check_date, days_since_entry, price_at_check, pnl_pct, pnl_amount)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (trade_id, check_date) DO UPDATE SET
                    price_at_check = EXCLUDED.price_at_check,
                    pnl_pct = EXCLUDED.pnl_pct,
                    pnl_amount = EXCLUDED.pnl_amount
            """, (trade_id, date_type.today(), days_since, current_price, pnl_pct, pnl_amount))
            
        except Exception as e:
            logger.error(f"Error recording trade outcome: {e}")

    def run_weekly_review(self):
        """
        Weekly self-review: analyze trades, outcomes, and generate reflection.
        """
        logger.info("📝 Running weekly review...")
        
        week_start = (datetime.now() - timedelta(days=7)).date()
        week_end = datetime.now().date()
        
        # Get this week's trades
        try:
            week_trades = self.db.query("""
                SELECT * FROM trades 
                WHERE executed_at >= :start
                ORDER BY executed_at
            """, {'start': week_start})
        except:
            week_trades = []
        
        if not week_trades:
            logger.info("No trades this week")
            return
        
        # Analyze results
        total = len(week_trades)
        winning = sum(1 for t in week_trades if float(t.get('pnl', 0) or 0) > 0)
        losing = sum(1 for t in week_trades if float(t.get('pnl', 0) or 0) < 0)
        total_pnl = sum(float(t.get('pnl', 0) or 0) for t in week_trades)
        win_rate = (winning / total * 100) if total > 0 else 0
        
        # Sector breakdown
        sector_pnl = {}
        for t in week_trades:
            ticker = t['ticker']
            pnl = float(t.get('pnl', 0) or 0)
            # Get sector from company cache
            company = self.db.query(
                "SELECT sector FROM companies WHERE ticker = :t",
                {'t': ticker}
            )
            sector = company[0]['sector'] if company else 'Unknown'
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
    
    def extract_learnings(self):
        """Extract learnings from recent trades."""
        trades = self.db.get_trades(limit=20)
        
        if trades.empty:
            return
        
        # Look for patterns
        # TODO: Implement pattern recognition
        
        # Example learning extraction:
        # - If trades in sector X consistently lose money → add learning
        # - If certain macro conditions predict outcomes → add learning
        
        logger.info("Learning extraction complete")
    
    def validate_hypotheses(self, days_to_check: int = 14) -> List[Dict]:
        """
        Check if past trade hypotheses were correct.
        Updates trades with outcome and extracts learnings.
        """
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
            
            days_since = (datetime.now() - trade_date.replace(tzinfo=None)).days
            if days_since < days_to_check:
                continue
            
            # Get price now vs entry
            ticker = trade['ticker']
            entry_price = float(trade['price'])
            
            prices = self.db.get_latest_prices([ticker])
            if prices.empty:
                continue
            
            current_price = float(prices.iloc[0]['close'])
            pnl_pct = ((current_price / entry_price) - 1) * 100
            
            # Determine if hypothesis was correct
            # For BUY: correct if price went up
            action = trade['action']
            if action == 'BUY':
                correct = pnl_pct > 0
            else:  # SELL
                correct = pnl_pct < 0
            
            outcome = f"{'Korrekt' if correct else 'Fel'}. Pris: {entry_price:.2f} → {current_price:.2f} ({pnl_pct:+.1f}%)"
            
            # Update trade with outcome
            try:
                self.db.execute("""
                    UPDATE trades SET 
                        outcome = %s,
                        outcome_correct = %s,
                        pnl = %s
                    WHERE id = %s
                """, (outcome, correct, pnl_pct * float(trade['shares']) * entry_price / 100, int(trade['id'])))
                
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
    
    def generate_trade_report(self, trade_id: int) -> str:
        """
        Generate a report for a specific trade.
        
        Example:
        📊 VOLVO B — KÖP
        
        Varför: Stålpriserna ner 12% sedan oktober. 
        Volvo = stor stålkonsument → bättre marginaler Q1.
        
        Risk: EUR/SEK volatil
        Confidence: 72%
        Position: 1 800 kr
        
        Hypotes: Kursen stiger 5-10% inom 2v
        Utfall: [PENDING]
        """
        # TODO: Implement trade report generation
        return f"Trade report for #{trade_id} - pending implementation"
