#!/usr/bin/env python3
"""
Script to run technical analysis and populate technical_signals table.
"""

import os
import sys
import logging
from datetime import datetime

# Add the agent source to path
sys.path.append('/app/src')

from data.database import Database
from core.analyzer import MarketAnalyzer

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    logger.info("📈 Starting technical analysis...")
    
    # Initialize components
    db = Database()
    analyzer = MarketAnalyzer(db)
    
    try:
        # Run technical analysis
        alerts = analyzer.run_technical_analysis()
        
        # Check results
        total_signals = db.query("SELECT COUNT(*) as count FROM technical_signals")[0]['count']
        latest_date = db.query("SELECT MAX(date) as max_date FROM technical_signals")[0]['max_date']
        
        logger.info(f"✅ Technical analysis complete!")
        logger.info(f"📊 Generated {total_signals} technical signals")
        logger.info(f"📅 Latest signal date: {latest_date}")
        logger.info(f"⚠️ Generated {len(alerts)} alerts")
        
        # Show some alerts
        for alert in alerts[:5]:
            logger.info(f"🔔 {alert['ticker']}: {alert['type']} ({alert['signal']})")
            
    except Exception as e:
        logger.error(f"❌ Error running technical analysis: {e}", exc_info=True)
        return 1
    
    logger.info("✅ Technical analysis complete!")
    return 0

if __name__ == "__main__":
    sys.exit(main())