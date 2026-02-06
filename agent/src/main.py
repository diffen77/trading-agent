"""
Trading Agent - Main Entry Point

This is the core agent that:
1. Fetches market data from Yahoo Finance
2. Analyzes companies and macro factors
3. Makes paper trading decisions
4. Logs everything with reasoning
5. Learns from outcomes

Schedule:
- 07:00: Pre-market analysis, macro update
- 09:00: Market open, scan for entries
- 12:00: Midday check, update prices
- 17:30: Market close, EOD analysis
- 22:00: Evening review, learning

Run modes:
- python -m agent.src.main              # Auto-detect based on time
- python -m agent.src.main morning      # Force morning routine
- python -m agent.src.main analyze      # Run full analysis
- python -m agent.src.main snapshot     # Save portfolio snapshot
"""

import os
import sys
import time
import logging
from datetime import datetime

from .data.yahoo import YahooDataFetcher
from .data.database import Database
from .data.news import NewsFetcher
from .core.analyzer import MarketAnalyzer
from .core.trader import PaperTrader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Schedule: which hours (CET) to run which routines
# Docker runs in UTC, so we offset by +1 (CET) or +2 (CEST)
SCHEDULE_UTC = {
    6: 'morning',    # 07:00 CET
    8: 'open',       # 09:00 CET
    11: 'midday',    # 12:00 CET
    16: 'close',     # 17:00 CET (close enough to 17:30)
    21: 'evening',   # 22:00 CET
}


def main():
    """Main agent entry point."""
    logger.info("🤖 Trading Agent starting up...")
    
    # Initialize components
    db = Database()
    yahoo = YahooDataFetcher()
    analyzer = MarketAnalyzer(db)
    trader = PaperTrader(db)
    
    logger.info("✅ Components initialized")
    
    # Check for command-line mode override
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    
    if mode and mode == 'daemon':
        run_daemon(yahoo, db, analyzer, trader)
    elif mode:
        run_mode(mode, yahoo, db, analyzer, trader)
        logger.info("✅ Agent routine complete")
    else:
        # Default: run as daemon (keeps container alive)
        run_daemon(yahoo, db, analyzer, trader)


def run_daemon(yahoo, db, analyzer, trader):
    """Run as a long-lived daemon with scheduled routines."""
    logger.info("🔄 Running in daemon mode — checking every 5 minutes")
    
    last_run_hour = -1
    
    while True:
        try:
            now = datetime.utcnow()
            current_hour = now.hour
            
            # Run scheduled routine once per hour
            if current_hour != last_run_hour:
                if current_hour in SCHEDULE_UTC:
                    mode = SCHEDULE_UTC[current_hour]
                    logger.info(f"⏰ Scheduled run: {mode} (UTC {current_hour}:00)")
                    run_mode(mode, yahoo, db, analyzer, trader)
                else:
                    # Off-schedule: just update prices during market hours (07-17 UTC)
                    if 7 <= current_hour <= 17:
                        logger.info(f"📊 Hourly price update (UTC {current_hour}:00)")
                        yahoo.update_all_prices(db)
                        db.save_portfolio_snapshot()
                    else:
                        logger.debug(f"💤 Outside market hours (UTC {current_hour}:00)")
                
                last_run_hour = current_hour
            
            # Sleep 5 minutes between checks
            time.sleep(300)
            
        except KeyboardInterrupt:
            logger.info("🛑 Agent shutting down...")
            break
        except Exception as e:
            logger.error(f"❌ Error in daemon loop: {e}", exc_info=True)
            time.sleep(60)  # Wait 1 min on error, then retry


def run_mode(mode: str, yahoo, db, analyzer, trader):
    """Run a specific mode."""
    logger.info(f"🎯 Running mode: {mode}")
    
    if mode == 'morning':
        run_morning_routine(yahoo, db, analyzer)
    elif mode == 'open':
        run_market_open_routine(yahoo, db, analyzer, trader)
    elif mode == 'midday':
        run_midday_routine(yahoo, db, trader)
    elif mode == 'close':
        run_eod_routine(yahoo, db, analyzer, trader)
    elif mode == 'evening':
        run_evening_routine(db, analyzer, trader)
    elif mode == 'analyze':
        run_full_analysis(yahoo, db, analyzer)
    elif mode == 'snapshot':
        db.save_portfolio_snapshot()
    elif mode == 'update':
        yahoo.update_all_prices(db)
        yahoo.update_macro_data(db)
    elif mode == 'prospects':
        analyzer.update_prospects()
    else:
        logger.warning(f"Unknown mode: {mode}")
        logger.info("Available modes: morning, open, midday, close, evening, analyze, snapshot, update, prospects")


def run_scheduled(hour: int, yahoo, db, analyzer, trader):
    """Run routine based on current hour."""
    
    if hour == 7:
        run_morning_routine(yahoo, db, analyzer)
    elif hour == 9:
        run_market_open_routine(yahoo, db, analyzer, trader)
    elif hour == 12:
        run_midday_routine(yahoo, db, trader)
    elif hour in [17, 18]:
        run_eod_routine(yahoo, db, analyzer, trader)
    elif hour == 22:
        run_evening_routine(db, analyzer, trader)
    else:
        # Ad-hoc run: just update data and snapshot
        logger.info("🔄 Ad-hoc run - updating data...")
        yahoo.update_all_prices(db)
        yahoo.update_macro_data(db)
        db.save_portfolio_snapshot()


def run_morning_routine(yahoo, db, analyzer):
    """
    Pre-market analysis routine (07:00).
    - Update macro data
    - Fetch news and analyze sentiment
    - Generate morning briefing
    - Update prospects
    """
    logger.info("🌅 Morning routine starting...")
    
    # Update macro data first
    yahoo.update_macro_data(db)
    
    # Update stock prices
    yahoo.update_all_prices(db)
    
    # Fetch and analyze news
    logger.info("📰 Fetching news...")
    news_fetcher = NewsFetcher(db)
    try:
        news = news_fetcher.fetch_all_news()
        news_fetcher.save_news(news)
        news_briefing = news_fetcher.generate_news_briefing()
        logger.info(news_briefing)
    except Exception as e:
        logger.warning(f"News fetch failed: {e}")
        news_briefing = "Nyheter ej tillgängliga"
    
    # Generate morning briefing
    briefing = analyzer.generate_morning_briefing()
    
    # Update prospects based on new data
    analyzer.update_prospects()
    
    logger.info("✅ Morning routine complete")
    return briefing


def run_market_open_routine(yahoo, db, analyzer, trader):
    """
    Market open routine (09:00).
    - Fresh price update
    - Find opportunities (NO auto-trade — Börje decides)
    - Check stop-loss/take-profit on existing positions
    """
    logger.info("📈 Market open routine starting...")
    
    # Fresh price update
    yahoo.update_all_prices(db)
    
    # Find opportunities (for Börje to review)
    opportunities = analyzer.find_opportunities()
    
    if opportunities:
        logger.info(f"📋 {len(opportunities)} opportunities found (awaiting Börje's decision)")
        for opp in opportunities[:5]:
            logger.info(f"   {opp['ticker']}: {opp['confidence']:.0f}% — {opp.get('thesis', 'N/A')}")
    
    # Auto stop-loss/take-profit on existing positions (mechanical, no brain needed)
    trader.check_positions()
    
    # Update prospects
    analyzer.update_prospects()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Market open routine complete")
    return opportunities


def run_midday_routine(yahoo, db, trader):
    """
    Midday check (12:00).
    - Update prices
    - Check positions for stop-loss/take-profit
    - Save snapshot
    """
    logger.info("☀️ Midday routine starting...")
    
    # Update prices
    yahoo.update_all_prices(db)
    
    # Check positions
    trader.check_positions()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Midday routine complete")


def run_eod_routine(yahoo, db, analyzer, trader):
    """
    End of day routine (17:30).
    - Final price update
    - Daily performance log
    - Day analysis
    - Update prospects
    """
    logger.info("🌆 End of day routine starting...")
    
    # Final price update
    yahoo.update_all_prices(db)
    yahoo.update_macro_data(db)
    
    # Log daily performance
    trader.log_daily_performance()
    
    # Run day analysis
    day_stats = analyzer.analyze_day()
    
    # Update prospects
    analyzer.update_prospects()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ End of day routine complete")
    return day_stats


def run_evening_routine(db, analyzer, trader):
    """
    Evening routine (22:00).
    - Validate old hypotheses (learning!)
    - Weekly review (if Friday)
    - Extract learnings
    """
    logger.info("🌙 Evening routine starting...")
    
    # Validate hypotheses from trades 14+ days old
    validated = trader.validate_hypotheses(days_to_check=14)
    
    # Weekly review on Fridays
    if datetime.now().weekday() == 4:
        logger.info("📝 Friday - running weekly review...")
        trader.run_weekly_review()
    
    # Extract learnings
    trader.extract_learnings()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Evening routine complete")
    return validated


def run_full_analysis(yahoo, db, analyzer):
    """
    Full analysis routine (ad-hoc).
    - Update all data
    - Full market scan
    - Generate reports
    """
    logger.info("🔬 Full analysis starting...")
    
    # Update everything
    yahoo.update_all_prices(db)
    yahoo.update_macro_data(db)
    
    # Morning briefing
    briefing = analyzer.generate_morning_briefing()
    print("\n" + briefing + "\n")
    
    # Find all opportunities
    opportunities = analyzer.find_opportunities()
    
    print("\n📊 Top Opportunities:")
    print("=" * 60)
    
    for i, opp in enumerate(opportunities[:10], 1):
        print(f"\n{i}. {opp['ticker']} ({opp['name']})")
        print(f"   Confidence: {opp['confidence']:.0f}%")
        print(f"   Price: {opp['current_price']:.2f} SEK")
        print(f"   Thesis: {opp['thesis']}")
        print(f"   Entry: {opp['entry_trigger']}")
    
    # Update prospects
    analyzer.update_prospects()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Full analysis complete")


if __name__ == "__main__":
    main()
