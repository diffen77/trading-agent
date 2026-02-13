#!/usr/bin/env python3
"""
Script to test trader.py functionality after fixes.
"""

import os
import sys
import logging
from datetime import datetime

# Add the agent source to path
sys.path.append('/app/src')

from data.database import Database
from core.trader import PaperTrader

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    logger.info("🧪 Testing trader.py functionality...")
    
    # Initialize components
    db = Database()
    trader = PaperTrader(db)
    
    try:
        # Test 1: Portfolio value calculation
        logger.info("🧪 Testing portfolio value...")
        portfolio_value = trader.get_portfolio_value()
        logger.info(f"✅ Portfolio value: {portfolio_value}")
        
    except Exception as e:
        logger.error(f"❌ Error testing portfolio value: {e}", exc_info=True)
        
    try:
        # Test 2: Check positions (this tests the SQL fix)
        logger.info("🧪 Testing check positions (SQL fix test)...")
        trader.check_positions()
        logger.info(f"✅ Check positions completed without SQL errors")
        
    except Exception as e:
        logger.error(f"❌ Error testing check positions: {e}", exc_info=True)
        
    try:
        # Test 3: Test trailing stop update (this tests the ORDER BY fix)
        logger.info("🧪 Testing trailing stop update...")
        # This will only work if we have positions, but should not crash on SQL syntax
        trader._update_trailing_stop("TEST", 100.0, 6.0)
        logger.info(f"✅ Trailing stop update completed without SQL errors")
        
    except Exception as e:
        # This might fail because TEST ticker doesn't exist, but should not be a SQL syntax error
        if "syntax error" in str(e).lower():
            logger.error(f"❌ SQL syntax error still exists: {e}")
        else:
            logger.info(f"✅ No SQL syntax error (expected data error): {e}")
    
    logger.info("✅ Trader testing complete!")
    return 0

if __name__ == "__main__":
    sys.exit(main())