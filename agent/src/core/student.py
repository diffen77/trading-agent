"""
Trading Student - Continuous Learning Module

The TradingStudent learns and evolves outside market hours (17:30-07:00 + weekends).
It runs comprehensive studies to make the trading agent smarter over time.

Studies:
1. Backtest Engine - Tests strategies against historical data
2. Report Study - Analyzes stock reactions to earnings reports  
3. Trade Review - Deep analysis of all past trades using Claude
4. News Research - Scrapes news for our companies and sentiment analysis
5. Strategy Evolution - Analyzes what types of trades work best
6. Self-Study - Researches external sources for OMX-specific patterns

Schedule:
- Regular study cycles: Every 60 minutes outside market hours
- Deep study cycles: Every 2 hours on weekends (more comprehensive)
"""

import logging
import json
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
from decimal import Decimal

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from .brain import TradingBrain

logger = logging.getLogger(__name__)


class TradingStudent:
    """Continuous learning engine for the trading agent."""
    
    def __init__(self, db, web_search_func=None):
        self.db = db
        self.web_search = web_search_func  # Function for web searches
        
        # Initialize Claude client for deep trade analysis
        self.claude_client = None
        if HAS_ANTHROPIC:
            import os
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                self.claude_client = anthropic.Anthropic(api_key=api_key)
    
    def is_market_hours(self) -> bool:
        """Check if we're currently in market hours (07:00-17:30 UTC)."""
        now = datetime.utcnow()
        hour = now.hour
        # Swedish market hours in UTC: 07:00-17:30 (09:00-18:30 CET with DST adjustments)
        return 7 <= hour <= 17 and now.weekday() < 5
    
    def should_run_study(self) -> bool:
        """Determine if we should run a study cycle now."""
        return not self.is_market_hours()
    
    def study_cycle(self) -> Dict[str, Any]:
        """
        Regular study cycle (60-minute intervals outside market hours).
        Rotates between different study types to spread the load.
        """
        if not self.should_run_study():
            logger.info("📚 Skipping study - market hours active")
            return {'status': 'skipped', 'reason': 'market_hours'}
        
        now = datetime.utcnow()
        hour = now.hour
        
        logger.info(f"📚 Starting study cycle (UTC {hour:02d}:00)")
        
        results = {
            'timestamp': now,
            'studies_completed': [],
            'insights_generated': 0,
            'learnings_added': 0,
        }
        
        # Rotate study types based on hour to spread load
        study_rotation = {
            18: 'trade_review',      # 18:00 UTC = 19:00 CET
            19: 'backtest',          # 19:00 UTC = 20:00 CET  
            20: 'report_study',      # 20:00 UTC = 21:00 CET
            21: 'news_research',     # 21:00 UTC = 22:00 CET
            22: 'strategy_evolution', # 22:00 UTC = 23:00 CET
            23: 'self_study',        # 23:00 UTC = 00:00 CET
            0: 'backtest',           # 00:00 UTC = 01:00 CET
            1: 'trade_review',       # 01:00 UTC = 02:00 CET
            2: 'strategy_evolution', # 02:00 UTC = 03:00 CET
            3: 'news_research',      # 03:00 UTC = 04:00 CET
            4: 'report_study',       # 04:00 UTC = 05:00 CET
            5: 'self_study',         # 05:00 UTC = 06:00 CET
            6: 'backtest',           # 06:00 UTC = 07:00 CET
        }
        
        study_type = study_rotation.get(hour, 'backtest')
        
        try:
            # Test database connection first
            try:
                self.db.query("SELECT 1")
                logger.info(f"📚 Database connection OK, running {study_type}")
            except Exception as db_error:
                logger.warning(f"📚 Database not available: {db_error}")
                results['error'] = f"Database connection failed: {db_error}"
                results['status'] = 'database_error'
                return results
            
            if study_type == 'backtest':
                result = self.run_backtest_engine()
                results['studies_completed'].append('backtest')
            elif study_type == 'trade_review':
                result = self.run_trade_review()
                results['studies_completed'].append('trade_review')
                results['learnings_added'] += result.get('learnings_added', 0)
            elif study_type == 'report_study':
                result = self.run_report_study()
                results['studies_completed'].append('report_study')
            elif study_type == 'news_research':
                result = self.run_news_research()
                results['studies_completed'].append('news_research')
            elif study_type == 'strategy_evolution':
                result = self.run_strategy_evolution()
                results['studies_completed'].append('strategy_evolution')
                results['insights_generated'] += result.get('insights_added', 0)
            elif study_type == 'self_study':
                result = self.run_self_study()
                results['studies_completed'].append('self_study')
                results['insights_generated'] += result.get('insights_added', 0)
                
        except Exception as e:
            logger.error(f"Error in {study_type}: {e}", exc_info=True)
            results['error'] = str(e)
        
        logger.info(f"📚 Study cycle complete: {study_type} | {results['studies_completed']}")
        return results
    
    def deep_study(self) -> Dict[str, Any]:
        """
        Deep study cycle (weekends, every 2 hours). 
        Runs multiple studies per cycle for comprehensive analysis.
        """
        if self.is_market_hours():
            logger.info("📚 Skipping deep study - market hours active")
            return {'status': 'skipped', 'reason': 'market_hours'}
        
        logger.info("📚🔬 Starting DEEP study cycle (weekend mode)")
        
        results = {
            'timestamp': datetime.utcnow(),
            'studies_completed': [],
            'insights_generated': 0,
            'learnings_added': 0,
            'backtests_run': 0,
        }
        
        # Run multiple studies
        studies = [
            ('backtest', self.run_backtest_engine),
            ('trade_review', self.run_trade_review),
            ('strategy_evolution', self.run_strategy_evolution),
            ('news_research', self.run_news_research),
            ('self_study', self.run_self_study),
        ]
        
        for study_name, study_func in studies:
            try:
                logger.info(f"📚 Running {study_name}...")
                result = study_func()
                results['studies_completed'].append(study_name)
                
                if 'learnings_added' in result:
                    results['learnings_added'] += result['learnings_added']
                if 'insights_added' in result:
                    results['insights_generated'] += result['insights_added']
                if 'backtests_run' in result:
                    results['backtests_run'] += result['backtests_run']
                    
            except Exception as e:
                logger.error(f"Error in deep study {study_name}: {e}", exc_info=True)
        
        logger.info(f"📚🔬 Deep study complete: {len(results['studies_completed'])} studies")
        return results
    
    # ==================== STUDY MODULE 1: BACKTEST ENGINE ====================
    
    def run_backtest_engine(self) -> Dict[str, Any]:
        """
        Test various strategies against historical price data.
        Examples: 'What if we bought every golden cross in the last 3 months?'
        """
        logger.info("📈 Running backtest engine...")
        
        strategies = [
            {
                'name': 'golden_cross_sma20_sma50',
                'description': 'Buy when SMA20 crosses above SMA50',
                'params': {'hold_days': 10, 'stop_loss_pct': -8, 'take_profit_pct': 12}
            },
            {
                'name': 'rsi_oversold_30',
                'description': 'Buy when RSI drops below 30',
                'params': {'rsi_threshold': 30, 'hold_days': 5, 'stop_loss_pct': -5}
            },
            {
                'name': 'momentum_breakout',
                'description': 'Buy on 20-day high breakout with volume',
                'params': {'volume_threshold': 1.5, 'hold_days': 7}
            },
            {
                'name': 'earnings_reaction',
                'description': 'Buy dips 1-2 days after earnings if RSI < 45',
                'params': {'max_days_after_earnings': 3, 'rsi_threshold': 45}
            }
        ]
        
        results = {
            'backtests_run': 0,
            'best_strategy': None,
            'best_return': -100
        }
        
        # Test 3-month period ending yesterday
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=90)
        
        for strategy in strategies[:2]:  # Limit to 2 strategies per cycle to avoid overload
            try:
                backtest_result = self._run_strategy_backtest(
                    strategy, start_date, end_date
                )
                results['backtests_run'] += 1
                
                if backtest_result['total_return_pct'] > results['best_return']:
                    results['best_return'] = backtest_result['total_return_pct']
                    results['best_strategy'] = strategy['name']
                
                # Save to database
                self.db.execute("""
                    INSERT INTO backtest_results 
                    (strategy_name, params, period_start, period_end, trades_count, 
                     win_rate, total_return_pct, avg_trade_pct, max_drawdown_pct, sharpe_ratio)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    strategy['name'],
                    json.dumps(strategy['params']),
                    start_date,
                    end_date,
                    backtest_result['trades_count'],
                    backtest_result['win_rate'],
                    backtest_result['total_return_pct'],
                    backtest_result['avg_trade_pct'],
                    backtest_result['max_drawdown_pct'],
                    backtest_result.get('sharpe_ratio')
                ))
                
                logger.info(f"📈 {strategy['name']}: {backtest_result['total_return_pct']:.1f}% return, "
                           f"{backtest_result['trades_count']} trades, {backtest_result['win_rate']:.1f}% win rate")
                
            except Exception as e:
                logger.error(f"Backtest error for {strategy['name']}: {e}")
        
        return results
    
    def _run_strategy_backtest(self, strategy: Dict, start_date: date, end_date: date) -> Dict:
        """Run a single strategy backtest."""
        strategy_name = strategy['name']
        params = strategy['params']
        
        # Get all tickers we track
        tickers = self.db.query("SELECT DISTINCT ticker FROM companies LIMIT 15")  # Limit for speed
        
        trades = []
        for ticker_row in tickers:
            ticker = ticker_row['ticker']
            
            # Get price and technical data for this ticker in the period
            price_data = self.db.query("""
                SELECT date, close FROM prices 
                WHERE ticker = %s AND date BETWEEN %s AND %s
                ORDER BY date
            """, (ticker, start_date, end_date))
            
            tech_data = self.db.query("""
                SELECT date, rsi, sma20, sma50, pattern, pattern_signal
                FROM technical_signals 
                WHERE ticker = %s AND date BETWEEN %s AND %s
                ORDER BY date
            """, (ticker, start_date, end_date))
            
            if not price_data or not tech_data:
                continue
            
            # Apply strategy logic
            if strategy_name == 'golden_cross_sma20_sma50':
                trades.extend(self._find_golden_cross_trades(ticker, price_data, tech_data, params))
            elif strategy_name == 'rsi_oversold_30':
                trades.extend(self._find_rsi_oversold_trades(ticker, price_data, tech_data, params))
            elif strategy_name == 'momentum_breakout':
                trades.extend(self._find_momentum_breakout_trades(ticker, price_data, tech_data, params))
        
        # Calculate performance
        if not trades:
            return {
                'trades_count': 0, 'win_rate': 0, 'total_return_pct': 0,
                'avg_trade_pct': 0, 'max_drawdown_pct': 0
            }
        
        total_return = sum(t['return_pct'] for t in trades)
        winning_trades = sum(1 for t in trades if t['return_pct'] > 0)
        
        return {
            'trades_count': len(trades),
            'win_rate': (winning_trades / len(trades)) * 100,
            'total_return_pct': total_return,
            'avg_trade_pct': total_return / len(trades),
            'max_drawdown_pct': min(t['return_pct'] for t in trades) if trades else 0,
        }
    
    def _find_golden_cross_trades(self, ticker: str, price_data: List, tech_data: List, params: Dict) -> List[Dict]:
        """Find trades based on golden cross strategy."""
        trades = []
        
        for i, tech in enumerate(tech_data):
            if (tech.get('pattern') == 'golden_cross' and 
                tech.get('pattern_signal') == 'bullish'):
                
                # Find entry price
                entry_date = tech['date']
                entry_price = None
                for price in price_data:
                    if price['date'] >= entry_date:
                        entry_price = float(price['close'])
                        break
                
                if not entry_price:
                    continue
                
                # Find exit price after hold_days
                exit_date = entry_date + timedelta(days=params['hold_days'])
                exit_price = None
                for price in price_data:
                    if price['date'] >= exit_date:
                        exit_price = float(price['close'])
                        break
                
                if exit_price:
                    return_pct = ((exit_price / entry_price) - 1) * 100
                    trades.append({
                        'ticker': ticker,
                        'entry_date': entry_date,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'return_pct': return_pct,
                        'strategy': 'golden_cross'
                    })
        
        return trades
    
    def _find_rsi_oversold_trades(self, ticker: str, price_data: List, tech_data: List, params: Dict) -> List[Dict]:
        """Find trades based on RSI oversold strategy."""
        trades = []
        
        for tech in tech_data:
            rsi = tech.get('rsi')
            if rsi and float(rsi) < params['rsi_threshold']:
                
                # Find entry price
                entry_date = tech['date']
                entry_price = None
                for price in price_data:
                    if price['date'] >= entry_date:
                        entry_price = float(price['close'])
                        break
                
                if not entry_price:
                    continue
                
                # Find exit price after hold_days
                exit_date = entry_date + timedelta(days=params['hold_days'])
                exit_price = None
                for price in price_data:
                    if price['date'] >= exit_date:
                        exit_price = float(price['close'])
                        break
                
                if exit_price:
                    return_pct = ((exit_price / entry_price) - 1) * 100
                    trades.append({
                        'ticker': ticker,
                        'entry_date': entry_date,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'return_pct': return_pct,
                        'strategy': 'rsi_oversold'
                    })
        
        return trades
    
    def _find_momentum_breakout_trades(self, ticker: str, price_data: List, tech_data: List, params: Dict) -> List[Dict]:
        """Find trades based on momentum breakout strategy."""
        trades = []
        
        for tech in tech_data:
            if (tech.get('pattern') == 'breakout' and 
                tech.get('pattern_signal') == 'bullish'):
                
                # Find entry price
                entry_date = tech['date']
                entry_price = None
                for price in price_data:
                    if price['date'] >= entry_date:
                        entry_price = float(price['close'])
                        break
                
                if not entry_price:
                    continue
                
                # Find exit price after hold_days
                exit_date = entry_date + timedelta(days=params.get('hold_days', 7))
                exit_price = None
                for price in price_data:
                    if price['date'] >= exit_date:
                        exit_price = float(price['close'])
                        break
                
                if exit_price:
                    return_pct = ((exit_price / entry_price) - 1) * 100
                    trades.append({
                        'ticker': ticker,
                        'entry_date': entry_date,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'return_pct': return_pct,
                        'strategy': 'momentum_breakout'
                    })
        
        return trades
    
    # ==================== STUDY MODULE 2: REPORT STUDY ====================
    
    def run_report_study(self) -> Dict[str, Any]:
        """
        Analyze how stocks react to earnings reports.
        Build database of reactions per company/sector.
        """
        logger.info("📊 Running report study...")
        
        # Get recent reports that we haven't analyzed yet
        unanalyzed_reports = self.db.query("""
            SELECT rc.ticker, rc.report_date, rc.report_type, c.name, c.sector
            FROM report_calendar rc
            LEFT JOIN companies c ON c.ticker = rc.ticker
            LEFT JOIN report_reactions rr ON rr.ticker = rc.ticker AND rr.report_date = rc.report_date
            WHERE rc.report_date >= CURRENT_DATE - INTERVAL '60 days'
            AND rc.report_date <= CURRENT_DATE - INTERVAL '3 days'  -- Need at least 3 days for analysis
            AND rr.id IS NULL  -- Not analyzed yet
            ORDER BY rc.report_date DESC
            LIMIT 10
        """)
        
        reactions_analyzed = 0
        
        for report in unanalyzed_reports:
            try:
                reaction = self._analyze_report_reaction(
                    report['ticker'], 
                    report['report_date'], 
                    report['report_type']
                )
                
                if reaction:
                    # Save to database
                    self.db.execute("""
                        INSERT INTO report_reactions 
                        (ticker, report_date, report_type, price_before, 
                         price_day1, price_day5, price_day10,
                         reaction_pct_day1, reaction_pct_day5, reaction_pct_day10)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticker, report_date, report_type) DO NOTHING
                    """, (
                        report['ticker'], report['report_date'], report['report_type'],
                        reaction['price_before'], reaction['price_day1'], 
                        reaction['price_day5'], reaction['price_day10'],
                        reaction['reaction_pct_day1'], reaction['reaction_pct_day5'],
                        reaction['reaction_pct_day10']
                    ))
                    
                    reactions_analyzed += 1
                    logger.info(f"📊 {report['ticker']} {report['report_date']}: "
                               f"D1: {reaction['reaction_pct_day1']:+.1f}%, "
                               f"D5: {reaction['reaction_pct_day5']:+.1f}%, "
                               f"D10: {reaction['reaction_pct_day10']:+.1f}%")
                
            except Exception as e:
                logger.error(f"Error analyzing report for {report['ticker']}: {e}")
        
        # Generate insights from accumulated data
        insights = self._generate_report_insights()
        
        return {
            'reactions_analyzed': reactions_analyzed,
            'insights_generated': len(insights)
        }
    
    def _analyze_report_reaction(self, ticker: str, report_date: date, report_type: str) -> Optional[Dict]:
        """Analyze how a stock reacted to an earnings report."""
        
        # Get prices around the report date
        price_data = self.db.query("""
            SELECT date, close FROM prices
            WHERE ticker = %s 
            AND date BETWEEN %s AND %s
            ORDER BY date
        """, (ticker, report_date - timedelta(days=5), report_date + timedelta(days=15)))
        
        if len(price_data) < 10:
            return None
        
        # Find price before report (last trading day before or on report date)
        price_before = None
        for price in price_data:
            if price['date'] <= report_date:
                price_before = float(price['close'])
            else:
                break
        
        if not price_before:
            return None
        
        # Find prices after report
        price_day1 = None
        price_day5 = None
        price_day10 = None
        
        for price in price_data:
            days_after = (price['date'] - report_date).days
            
            if days_after >= 1 and not price_day1:
                price_day1 = float(price['close'])
            if days_after >= 5 and not price_day5:
                price_day5 = float(price['close'])
            if days_after >= 10 and not price_day10:
                price_day10 = float(price['close'])
        
        # Calculate reactions
        reaction = {
            'price_before': price_before,
            'price_day1': price_day1,
            'price_day5': price_day5,
            'price_day10': price_day10,
            'reaction_pct_day1': ((price_day1 / price_before) - 1) * 100 if price_day1 else None,
            'reaction_pct_day5': ((price_day5 / price_before) - 1) * 100 if price_day5 else None,
            'reaction_pct_day10': ((price_day10 / price_before) - 1) * 100 if price_day10 else None,
        }
        
        return reaction
    
    def _generate_report_insights(self) -> List[Dict]:
        """Generate insights from report reaction data."""
        insights = []
        
        # Analyze patterns by sector
        sector_analysis = self.db.query("""
            SELECT c.sector, 
                   COUNT(*) as report_count,
                   AVG(rr.reaction_pct_day1) as avg_day1_reaction,
                   AVG(rr.reaction_pct_day5) as avg_day5_reaction,
                   AVG(rr.reaction_pct_day10) as avg_day10_reaction
            FROM report_reactions rr
            JOIN companies c ON c.ticker = rr.ticker
            WHERE rr.created_at >= CURRENT_DATE - INTERVAL '90 days'
            GROUP BY c.sector
            HAVING COUNT(*) >= 3
        """)
        
        for sector in sector_analysis:
            if abs(float(sector['avg_day1_reaction'] or 0)) > 2:
                insights.append({
                    'type': 'sector_report_pattern',
                    'sector': sector['sector'],
                    'pattern': f"Sektor {sector['sector']} reagerar i snitt {sector['avg_day1_reaction']:+.1f}% dag 1 efter rapport",
                    'confidence': min(80, sector['report_count'] * 10),
                    'sample_size': sector['report_count']
                })
        
        return insights
    
    # ==================== STUDY MODULE 3: TRADE REVIEW ====================
    
    def run_trade_review(self) -> Dict[str, Any]:
        """
        Deep analysis of all past trades using Claude Sonnet.
        Extract learnings about what worked and what didn't.
        """
        logger.info("🔍 Running trade review with Claude analysis...")
        
        if not self.claude_client:
            logger.warning("Claude client not available for trade review")
            return {'learnings_added': 0}
        
        # Get unreviewed closed trades (older than 7 days)
        unreviewed_trades = self.db.query("""
            SELECT t.*, 
                   (SELECT close FROM prices p WHERE p.ticker = t.ticker 
                    ORDER BY date DESC LIMIT 1) as current_price
            FROM trades t
            WHERE t.executed_at <= CURRENT_DATE - INTERVAL '7 days'
            AND (t.claude_reviewed IS NULL OR t.claude_reviewed = false)
            AND t.action = 'BUY'
            ORDER BY t.executed_at DESC
            LIMIT 5  -- Review 5 trades per cycle
        """)
        
        learnings_added = 0
        
        for trade in unreviewed_trades:
            try:
                analysis = self._claude_analyze_trade(trade)
                if analysis:
                    # Save learning if Claude found something significant
                    if analysis.get('learning'):
                        learning = {
                            'category': analysis['category'],
                            'content': analysis['learning'],
                            'source_trade_ids': [int(trade['id'])],
                            'confidence': analysis.get('confidence', 60)
                        }
                        self.db.add_learning(learning)
                        learnings_added += 1
                    
                    # Mark as reviewed
                    self.db.execute("""
                        UPDATE trades SET 
                        claude_reviewed = true,
                        claude_analysis = %s,
                        updated_at = NOW()
                        WHERE id = %s
                    """, (json.dumps(analysis), int(trade['id'])))
                    
                    logger.info(f"🔍 {trade['ticker']}: {analysis.get('summary', 'Analyzed')}")
                
            except Exception as e:
                logger.error(f"Error reviewing trade {trade['id']}: {e}")
        
        return {'learnings_added': learnings_added}
    
    def _claude_analyze_trade(self, trade: Dict) -> Optional[Dict]:
        """Use Claude to deeply analyze a single trade."""
        
        ticker = trade['ticker']
        entry_price = float(trade['price'])
        current_price = float(trade['current_price']) if trade['current_price'] else entry_price
        
        # Get context around the trade
        trade_date = trade['executed_at']
        if isinstance(trade_date, str):
            trade_date = datetime.fromisoformat(trade_date.replace('Z', '+00:00'))
        
        # Get price history around trade
        price_history = self.db.query("""
            SELECT date, close FROM prices
            WHERE ticker = %s 
            AND date BETWEEN %s AND %s
            ORDER BY date
        """, (ticker, trade_date.date() - timedelta(days=30), trade_date.date() + timedelta(days=30)))
        
        # Get macro context at the time
        macro_context = self.db.query("""
            SELECT symbol, value, change_pct FROM macro
            WHERE date <= %s
            ORDER BY date DESC
            LIMIT 5
        """, (trade_date.date(),))
        
        # Build context for Claude
        price_text = "\n".join([f"{p['date']}: {p['close']:.2f} SEK" for p in price_history[-10:]])
        macro_text = "\n".join([f"{m['symbol']}: {m['value']:.2f} ({m['change_pct']:+.1f}%)" for m in macro_context])
        
        pnl_pct = ((current_price / entry_price) - 1) * 100
        days_since = (datetime.now().date() - trade_date.date()).days
        
        prompt = f"""Analysera denna trading-beslut djupt:

TRADE DETALJER:
- Ticker: {ticker}
- Datum: {trade_date.strftime('%Y-%m-%d')}
- Entry: {entry_price:.2f} SEK
- Nuvarande: {current_price:.2f} SEK
- P&L: {pnl_pct:+.1f}% över {days_since} dagar
- Reasoning: {trade.get('reasoning', 'N/A')}
- Hypothesis: {trade.get('hypothesis', 'N/A')}
- Confidence: {trade.get('confidence', 'N/A')}%

PRISHISTORIK (senaste 10 dagarna):
{price_text}

MAKRO-KONTEXT VID TILLFÄLLET:
{macro_text}

ANALYS-INSTRUKTIONER:
1. Var hypotesen korrekt? Varför/varför inte?
2. Vad missades i analysen?
3. Vilka signaler borde vi följt istället?
4. Vad kan vi lära oss för framtida trades?

Svara med JSON:
{{
  "hypothesis_correct": true/false,
  "summary": "Kort sammanfattning av vad som hände",
  "missed_signals": ["Signal 1", "Signal 2"],
  "learning": "Konkret lärdom för framtiden (eller null om inget speciellt)",
  "category": "pattern/mistake/confirmation/timing",
  "confidence": 1-100
}}"""

        try:
            response = self.claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                system="Du är en expert trading-analytiker. Analysera trades objektivt för att extrahera lärdomar.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse JSON response
            raw_text = response.content[0].text
            if raw_text.strip().startswith("```"):
                # Remove code block formatting
                lines = raw_text.split("\n")
                raw_text = "\n".join(l for l in lines if not l.strip().startswith("```"))
            
            analysis = json.loads(raw_text.strip())
            return analysis
            
        except Exception as e:
            logger.error(f"Claude analysis error: {e}")
            return None
    
    # ==================== STUDY MODULE 4: NEWS RESEARCH ====================
    
    def run_news_research(self) -> Dict[str, Any]:
        """
        Research news about our companies, analyze sentiment trends,
        find upcoming catalysts (reports, product launches, M&A).
        """
        logger.info("📰 Running news research...")
        
        # Get our tracked companies
        companies = self.db.query("SELECT ticker, name, sector FROM companies LIMIT 20")
        
        research_notes_added = 0
        
        for company in companies:
            try:
                if self.web_search:
                    # Search for recent news about this company
                    query = f"{company['name']} OR {company['ticker']} Sweden stock news"
                    search_results = self.web_search(query, count=3, freshness='pw')  # Past week
                    
                    for result in search_results.get('web', {}).get('results', []):
                        # Analyze relevance and sentiment
                        relevance = self._analyze_news_relevance(
                            result['title'] + ' ' + result.get('snippet', ''),
                            company
                        )
                        
                        if relevance['score'] >= 60:  # Only save relevant news
                            self.db.execute("""
                                INSERT INTO research_notes 
                                (ticker, topic, content, source, relevance_score)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT DO NOTHING
                            """, (
                                company['ticker'],
                                result['title'],
                                result.get('snippet', ''),
                                result.get('url', 'web_search'),
                                relevance['score']
                            ))
                            research_notes_added += 1
                
                # Look for upcoming catalysts in our existing news
                self._identify_catalysts(company['ticker'])
                
            except Exception as e:
                logger.error(f"News research error for {company['ticker']}: {e}")
        
        return {'research_notes_added': research_notes_added}
    
    def _analyze_news_relevance(self, content: str, company: Dict) -> Dict[str, Any]:
        """Analyze how relevant a news item is for trading decisions."""
        
        # Simple keyword-based relevance scoring
        score = 0
        
        content_lower = content.lower()
        ticker_lower = company['ticker'].lower()
        name_lower = company['name'].lower()
        
        # High relevance keywords
        high_keywords = ['rapport', 'earnings', 'kvartalsrapport', 'q1', 'q2', 'q3', 'q4',
                        'förvärv', 'acquisition', 'fusion', 'merger', 'avtal', 'contract',
                        'order', 'beställning', 'lansering', 'launch']
        
        # Medium relevance keywords  
        medium_keywords = ['kurs', 'aktie', 'stock', 'börsen', 'omxs', 'rekommendation',
                          'analysdag', 'prognos', 'forecast', 'omsättning', 'vinst']
        
        # Check for company mentions
        if ticker_lower in content_lower:
            score += 30
        if name_lower in content_lower:
            score += 20
        
        # Check for relevant keywords
        for keyword in high_keywords:
            if keyword in content_lower:
                score += 25
        
        for keyword in medium_keywords:
            if keyword in content_lower:
                score += 10
        
        # Sector relevance
        if company.get('sector'):
            sector_keywords = {
                'industrials': ['industri', 'manufacturing', 'verkstad'],
                'technology': ['tech', 'mjukvara', 'software', 'ai', 'digital'],
                'basic materials': ['stål', 'steel', 'mining', 'gruv', 'råvara'],
            }
            
            sector_key = company['sector'].lower()
            if sector_key in sector_keywords:
                for keyword in sector_keywords[sector_key]:
                    if keyword in content_lower:
                        score += 15
        
        return {
            'score': min(100, score),
            'high_relevance': score >= 70
        }
    
    def _identify_catalysts(self, ticker: str):
        """Identify upcoming catalysts from research notes."""
        
        # Look for catalyst-indicating content in recent research notes
        catalyst_notes = self.db.query("""
            SELECT topic, content FROM research_notes
            WHERE ticker = %s 
            AND created_at >= CURRENT_DATE - INTERVAL '7 days'
            AND (LOWER(content) LIKE '%%rapport%%' 
                 OR LOWER(content) LIKE '%%lansering%%'
                 OR LOWER(content) LIKE '%%contract%%'
                 OR LOWER(content) LIKE '%%avtal%%')
        """, (ticker,))
        
        # This could be enhanced with NLP to extract specific dates and events
        # For now, just flag that catalysts exist
        if catalyst_notes:
            logger.info(f"📅 {ticker}: {len(catalyst_notes)} potential catalysts identified")
    
    # ==================== STUDY MODULE 5: STRATEGY EVOLUTION ====================
    
    def run_strategy_evolution(self) -> Dict[str, Any]:
        """
        Analyze what types of trades make the most money.
        Generate strategy insights for the brain to use.
        """
        logger.info("🧠 Running strategy evolution analysis...")
        
        # Analyze trade patterns from the last 6 months
        trade_analysis = self._analyze_trade_patterns()
        
        insights_added = 0
        
        # Generate insights from the analysis
        insights = self._generate_strategy_insights(trade_analysis)
        
        for insight in insights:
            try:
                self.db.execute("""
                    INSERT INTO strategy_insights (insight_type, content, confidence, evidence)
                    VALUES (%s, %s, %s, %s)
                """, (
                    insight['type'],
                    insight['content'],
                    insight['confidence'],
                    insight['evidence']
                ))
                insights_added += 1
                
                logger.info(f"💡 Strategy insight: {insight['content']}")
                
            except Exception as e:
                logger.error(f"Error saving strategy insight: {e}")
        
        return {'insights_added': insights_added}
    
    def _analyze_trade_patterns(self) -> Dict[str, Any]:
        """Analyze patterns in our trading history."""
        
        # Sector performance
        sector_performance = self.db.query("""
            SELECT c.sector, 
                   COUNT(t.id) as trade_count,
                   AVG(CASE WHEN t.pnl IS NOT NULL THEN t.pnl ELSE 0 END) as avg_pnl,
                   AVG(CASE WHEN t.confidence IS NOT NULL THEN t.confidence ELSE 0 END) as avg_confidence,
                   SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(t.id) * 100 as win_rate
            FROM trades t
            JOIN companies c ON c.ticker = t.ticker
            WHERE t.executed_at >= CURRENT_DATE - INTERVAL '6 months'
            GROUP BY c.sector
            HAVING COUNT(t.id) >= 3
            ORDER BY avg_pnl DESC
        """)
        
        # Time-based performance
        time_performance = self.db.query("""
            SELECT EXTRACT(HOUR FROM executed_at) as hour,
                   COUNT(*) as trade_count,
                   AVG(CASE WHEN pnl IS NOT NULL THEN pnl ELSE 0 END) as avg_pnl,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) * 100 as win_rate
            FROM trades
            WHERE executed_at >= CURRENT_DATE - INTERVAL '6 months'
            GROUP BY EXTRACT(HOUR FROM executed_at)
            HAVING COUNT(*) >= 3
            ORDER BY avg_pnl DESC
        """)
        
        # Confidence vs performance correlation
        confidence_analysis = self.db.query("""
            SELECT 
                CASE 
                    WHEN confidence >= 80 THEN '80-100'
                    WHEN confidence >= 70 THEN '70-79'
                    WHEN confidence >= 60 THEN '60-69'
                    ELSE '50-59'
                END as confidence_range,
                COUNT(*) as trade_count,
                AVG(CASE WHEN pnl IS NOT NULL THEN pnl ELSE 0 END) as avg_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) * 100 as win_rate
            FROM trades
            WHERE executed_at >= CURRENT_DATE - INTERVAL '6 months'
            AND confidence IS NOT NULL
            GROUP BY confidence_range
            ORDER BY avg_pnl DESC
        """)
        
        return {
            'sector_performance': sector_performance,
            'time_performance': time_performance,
            'confidence_analysis': confidence_analysis
        }
    
    def _generate_strategy_insights(self, analysis: Dict[str, Any]) -> List[Dict]:
        """Generate actionable strategy insights from trade analysis."""
        insights = []
        
        # Sector insights
        if analysis['sector_performance']:
            best_sector = analysis['sector_performance'][0]
            worst_sector = analysis['sector_performance'][-1]
            
            if float(best_sector['avg_pnl'] or 0) > 100:  # If best sector avg > 100 SEK
                insights.append({
                    'type': 'sector_preference',
                    'content': f"Prioritera {best_sector['sector']}-aktier: Snitt +{best_sector['avg_pnl']:.0f} SEK per trade, {best_sector['win_rate']:.0f}% vinst",
                    'confidence': min(90, int(best_sector['trade_count']) * 10),
                    'evidence': f"Baserat på {best_sector['trade_count']} trades senaste 6 mån"
                })
            
            if float(worst_sector['avg_pnl'] or 0) < -50:  # If worst sector avg < -50 SEK
                insights.append({
                    'type': 'sector_avoidance',
                    'content': f"Undvik {worst_sector['sector']}-aktier: Snitt {worst_sector['avg_pnl']:.0f} SEK per trade",
                    'confidence': min(85, int(worst_sector['trade_count']) * 8),
                    'evidence': f"Baserat på {worst_sector['trade_count']} förlusttrades senaste 6 mån"
                })
        
        # Time-based insights
        if analysis['time_performance']:
            best_hours = [t for t in analysis['time_performance'] if float(t['avg_pnl'] or 0) > 50]
            if best_hours:
                best_hour = best_hours[0]
                insights.append({
                    'type': 'timing_preference',
                    'content': f"Bästa trading-tid: {best_hour['hour']:02.0f}:00 UTC (snitt +{best_hour['avg_pnl']:.0f} SEK)",
                    'confidence': min(80, int(best_hour['trade_count']) * 15),
                    'evidence': f"{best_hour['trade_count']} trades med {best_hour['win_rate']:.0f}% vinst"
                })
        
        # Confidence insights
        if analysis['confidence_analysis']:
            for conf_range in analysis['confidence_analysis']:
                if float(conf_range['avg_pnl'] or 0) > 100 and int(conf_range['trade_count']) >= 5:
                    insights.append({
                        'type': 'confidence_validation',
                        'content': f"Confidence {conf_range['confidence_range']}% fungerar: Snitt +{conf_range['avg_pnl']:.0f} SEK, {conf_range['win_rate']:.0f}% vinst",
                        'confidence': int(conf_range['win_rate']),
                        'evidence': f"{conf_range['trade_count']} trades med denna confidence-nivå"
                    })
        
        return insights
    
    # ==================== STUDY MODULE 6: SELF-STUDY ====================
    
    def run_self_study(self) -> Dict[str, Any]:
        """
        Search web for trading strategies specific to Swedish stocks.
        Study OMX-specific patterns like Friday effect, earnings seasonality.
        """
        logger.info("📚 Running self-study with external research...")
        
        insights_added = 0
        
        if not self.web_search:
            logger.warning("Web search not available for self-study")
            return {'insights_added': 0}
        
        # Research topics for OMX/Swedish market patterns
        research_topics = [
            "OMX Stockholm seasonality trading patterns",
            "Swedish stock market Friday effect",
            "Nordic earnings announcement reactions",
            "Swedish small cap momentum strategies", 
            "OMX sector rotation patterns",
            "Swedish krona impact on stock prices",
            "Nordic trading volume patterns"
        ]
        
        for topic in research_topics[:3]:  # Limit to 3 topics per cycle
            try:
                results = self.web_search(topic, count=5, freshness='pm')  # Past month
                
                for result in results.get('web', {}).get('results', []):
                    insight = self._extract_trading_insight(result, topic)
                    if insight:
                        # Save insight
                        self.db.execute("""
                            INSERT INTO strategy_insights (insight_type, content, confidence, evidence)
                            VALUES (%s, %s, %s, %s)
                        """, (
                            'external_research',
                            insight['content'],
                            insight['confidence'],
                            f"Source: {result.get('title', 'External research')}"
                        ))
                        insights_added += 1
                        
                        logger.info(f"📚 External insight: {insight['content'][:80]}...")
                
            except Exception as e:
                logger.error(f"Self-study error for topic '{topic}': {e}")
        
        return {'insights_added': insights_added}
    
    def _extract_trading_insight(self, search_result: Dict, topic: str) -> Optional[Dict]:
        """Extract actionable trading insight from web search result."""
        
        title = search_result.get('title', '')
        snippet = search_result.get('snippet', '')
        content = (title + ' ' + snippet).lower()
        
        # Look for actionable patterns or insights
        insights = []
        
        # Pattern: Friday effect
        if 'friday' in content and ('effect' in content or 'pattern' in content):
            insights.append({
                'content': f"OMX Friday-mönster identifierat: {snippet[:100]}...",
                'confidence': 60,
                'type': 'timing_pattern'
            })
        
        # Pattern: Sector rotation
        if 'sector' in content and ('rotation' in content or 'outperform' in content):
            insights.append({
                'content': f"Sektorrotation på OMX: {snippet[:100]}...", 
                'confidence': 65,
                'type': 'sector_pattern'
            })
        
        # Pattern: Seasonality
        if any(month in content for month in ['january', 'december', 'q1', 'q4']) and 'effect' in content:
            insights.append({
                'content': f"Säsongseffekt identifierad: {snippet[:100]}...",
                'confidence': 70,
                'type': 'seasonal_pattern'
            })
        
        # Pattern: Volume/momentum
        if 'momentum' in content and ('volume' in content or 'breakout' in content):
            insights.append({
                'content': f"OMX momentum-strategi: {snippet[:100]}...",
                'confidence': 75,
                'type': 'momentum_pattern'
            })
        
        # Return the highest confidence insight
        if insights:
            return max(insights, key=lambda x: x['confidence'])
        
        return None