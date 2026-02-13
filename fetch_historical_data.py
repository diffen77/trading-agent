#!/usr/bin/env python3
"""
Script to fetch 6 months of historical data for all tracked companies.
Run this manually to populate the database with enough data for technical analysis.
"""

import os
import sys
import logging
from datetime import datetime

# Add the agent source to path
sys.path.append('/app/src')

from data.yahoo import YahooDataFetcher
from data.database import Database

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    logger.info("🚀 Starting historical data fetch for 6 months...")
    
    # Initialize components
    db = Database()
    yahoo = YahooDataFetcher()
    
    try:
        # Fetch 6 months of data for all tickers
        logger.info(f"Fetching 6 months of data for {len(yahoo.tickers)} tickers...")
        prices_df = yahoo.fetch_all_prices(period="6mo")
        
        if not prices_df.empty:
            logger.info(f"Got {len(prices_df)} price records")
            
            # Save to database
            db.save_prices(prices_df)
            logger.info("✅ Historical data saved successfully!")
            
            # Check what we got
            total_rows = db.query("SELECT COUNT(*) as count FROM prices")[0]['count']
            date_range = db.query("SELECT MIN(date) as min_date, MAX(date) as max_date FROM prices")[0]
            
            logger.info(f"📊 Database now has {total_rows} price records")
            logger.info(f"📅 Date range: {date_range['min_date']} to {date_range['max_date']}")
            
        else:
            logger.error("❌ No data fetched!")
            
    except Exception as e:
        logger.error(f"❌ Error fetching historical data: {e}", exc_info=True)
        return 1
    
    logger.info("✅ Historical data fetch complete!")
    return 0

if __name__ == "__main__":
    sys.exit(main())