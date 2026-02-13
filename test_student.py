#!/usr/bin/env python3
"""
Script to test student.py functionality after fixes.
"""

import os
import sys
import logging
from datetime import datetime

# Add the agent source to path
sys.path.append('/app/src')

from data.database import Database
from core.student import TradingStudent

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    logger.info("🧪 Testing student.py functionality...")
    
    # Initialize components
    db = Database()
    student = TradingStudent(db)
    
    try:
        # Test 1: Run backtest engine
        logger.info("🧪 Testing backtest engine...")
        backtest_result = student.run_backtest_engine()
        logger.info(f"✅ Backtest result: {backtest_result}")
        
        # Check if backtest results were saved
        backtest_count = db.query("SELECT COUNT(*) as count FROM backtest_results")[0]['count']
        logger.info(f"📊 Backtest results in DB: {backtest_count}")
        
    except Exception as e:
        logger.error(f"❌ Error testing backtest: {e}", exc_info=True)
        
    try:
        # Test 2: Run strategy evolution 
        logger.info("🧪 Testing strategy evolution...")
        evolution_result = student.run_strategy_evolution()
        logger.info(f"✅ Strategy evolution result: {evolution_result}")
        
        # Check insights generated
        insights_count = db.query("SELECT COUNT(*) as count FROM strategy_insights")[0]['count']
        logger.info(f"📊 Strategy insights in DB: {insights_count}")
        
    except Exception as e:
        logger.error(f"❌ Error testing strategy evolution: {e}", exc_info=True)
        
    try:
        # Test 3: Check if we can run full study cycle
        logger.info("🧪 Testing full study cycle...")
        study_result = student.study_cycle()
        logger.info(f"✅ Study cycle result: {study_result}")
        
    except Exception as e:
        logger.error(f"❌ Error testing study cycle: {e}", exc_info=True)
    
    logger.info("✅ Student testing complete!")
    return 0

if __name__ == "__main__":
    sys.exit(main())