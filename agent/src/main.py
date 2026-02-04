"""
Trading Agent - Main Entry Point

This is the core agent that:
1. Fetches market data from Yahoo Finance
2. Analyzes companies and macro factors
3. Makes paper trading decisions
4. Logs everything with reasoning
5. Learns from outcomes
"""

import os
import logging
from datetime import datetime

from .data.yahoo import YahooDataFetcher
from .data.database import Database
from .core.analyzer import MarketAnalyzer
from .core.trader import PaperTrader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main agent loop."""
    logger.info("🤖 Trading Agent starting up...")
    
    # Initialize components
    db = Database()
    yahoo = YahooDataFetcher()
    analyzer = MarketAnalyzer(db)
    trader = PaperTrader(db)
    
    logger.info("✅ Components initialized")
    
    # Check what time it is and run appropriate routine
    hour = datetime.now().hour
    
    if hour == 7:
        # Morning: Pre-market analysis
        logger.info("🌅 Running morning pre-market analysis...")
        run_morning_routine(yahoo, db, analyzer)
        
    elif hour == 9:
        # Market open: Look for entries
        logger.info("📈 Market open - scanning for opportunities...")
        run_market_open_routine(analyzer, trader)
        
    elif hour == 12:
        # Midday: Check positions
        logger.info("☀️ Midday check...")
        run_midday_routine(yahoo, db, trader)
        
    elif hour == 17 or hour == 18:
        # Market close: End of day analysis
        logger.info("🌆 Market closed - running end of day analysis...")
        run_eod_routine(yahoo, db, analyzer, trader)
        
    elif hour == 22:
        # Evening: Deep analysis, US market, learning
        logger.info("🌙 Evening analysis and learning...")
        run_evening_routine(db, analyzer, trader)
        
    else:
        # Ad-hoc run: Full update
        logger.info("🔄 Ad-hoc run - updating data...")
        yahoo.update_all_prices(db)
        
    logger.info("✅ Agent routine complete")


def run_morning_routine(yahoo, db, analyzer):
    """Pre-market analysis routine."""
    # Update macro data
    yahoo.update_macro_data(db)
    
    # Check overnight news
    # TODO: News fetcher
    
    # Generate morning briefing
    analyzer.generate_morning_briefing()


def run_market_open_routine(analyzer, trader):
    """Market open routine - look for trades."""
    opportunities = analyzer.find_opportunities()
    
    for opp in opportunities:
        if opp['confidence'] >= 70:
            trader.execute_trade(opp)


def run_midday_routine(yahoo, db, trader):
    """Midday check on positions."""
    yahoo.update_all_prices(db)
    trader.check_positions()


def run_eod_routine(yahoo, db, analyzer, trader):
    """End of day routine."""
    yahoo.update_all_prices(db)
    yahoo.update_fundamentals(db)
    
    trader.log_daily_performance()
    analyzer.analyze_day()


def run_evening_routine(db, analyzer, trader):
    """Evening deep analysis and learning."""
    # Check US market impact
    # TODO: US market analysis
    
    # Weekly review on Fridays
    if datetime.now().weekday() == 4:
        trader.run_weekly_review()
        analyzer.update_strategies()
    
    # Update knowledge base
    trader.extract_learnings()


if __name__ == "__main__":
    main()
