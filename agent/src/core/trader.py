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
        action = opportunity['action']
        position_size = opportunity.get('position_size', 2000)
        
        # Get current price
        prices = self.db.get_latest_prices([ticker])
        if prices.empty:
            logger.error(f"No price data for {ticker}")
            return False
            
        current_price = float(prices.iloc[0]['close'])
        shares = position_size / current_price
        
        # Log the trade
        trade = {
            'ticker': ticker,
            'action': action,
            'shares': shares,
            'price': current_price,
            'total_value': position_size,
            'reasoning': opportunity['reasoning'],
            'confidence': opportunity.get('confidence'),
            'hypothesis': opportunity.get('hypothesis'),
            'macro_context': opportunity.get('macro_context', {}),
        }
        
        self.db.log_trade(trade)
        
        logger.info(f"📝 Trade executed: {action} {shares:.2f} {ticker} @ {current_price:.2f}")
        logger.info(f"   Reasoning: {opportunity['reasoning']}")
        
        return True
    
    def check_positions(self):
        """Check current positions for stop-loss or take-profit."""
        portfolio = self.db.get_portfolio()
        
        if portfolio.empty:
            logger.info("No open positions")
            return
        
        for _, pos in portfolio.iterrows():
            ticker = pos['ticker']
            avg_price = float(pos['avg_price'])
            
            # Get current price
            prices = self.db.get_latest_prices([ticker])
            if prices.empty:
                continue
                
            current_price = float(prices.iloc[0]['close'])
            pnl_pct = ((current_price / avg_price) - 1) * 100
            
            # Check stop-loss (-5%)
            if pnl_pct <= -5:
                logger.warning(f"⚠️ {ticker}: Stop-loss triggered ({pnl_pct:.1f}%)")
                # TODO: Execute sell
                
            # Check take-profit (variable based on strategy)
            elif pnl_pct >= 10:
                logger.info(f"✅ {ticker}: Consider taking profit ({pnl_pct:.1f}%)")
    
    def log_daily_performance(self):
        """Log end of day performance."""
        portfolio = self.get_portfolio_value()
        
        logger.info("📊 Daily Performance")
        logger.info(f"   Cash: {portfolio['cash']:.2f} SEK")
        logger.info(f"   Positions: {portfolio['positions_value']:.2f} SEK")
        logger.info(f"   Total: {portfolio['total_value']:.2f} SEK")
        logger.info(f"   P&L: {portfolio['pnl']:.2f} SEK ({portfolio['pnl_pct']:.2f}%)")
    
    def run_weekly_review(self):
        """
        Weekly review - analyze trades and extract learnings.
        
        Questions to answer:
        1. Which trades were profitable? Why?
        2. Which trades lost money? Why?
        3. Were my hypotheses correct?
        4. What patterns do I see?
        5. What should I do differently?
        """
        logger.info("📝 Running weekly review...")
        
        # Get this week's trades
        trades = self.db.get_trades(limit=100)
        
        if trades.empty:
            logger.info("No trades this week")
            return
        
        # Filter to this week
        week_ago = datetime.now() - timedelta(days=7)
        # TODO: Filter trades by date
        
        # Analyze
        # TODO: Implement full weekly review logic
        
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
