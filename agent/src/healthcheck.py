#!/usr/bin/env python3
"""
Healthcheck for Trading Agent

Run after deploy to verify everything works:
  python -m src.healthcheck
"""

import sys
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def check_database():
    """Verify database connection and tables."""
    from src.data.database import Database
    
    db = Database()
    
    # Check tables exist
    required_tables = ['prices', 'companies', 'technical_signals', 'portfolio', 'balance', 'trades']
    for table in required_tables:
        try:
            result = db.query(f"SELECT 1 FROM {table} LIMIT 1")
            logger.info(f"  ✅ {table}")
        except Exception as e:
            logger.error(f"  ❌ {table}: {e}")
            return False
    
    return True


def check_data_freshness():
    """Verify we have recent price data."""
    from src.data.database import Database
    
    db = Database()
    
    result = db.query("SELECT MAX(date) as latest FROM prices")
    if not result or not result[0]['latest']:
        logger.error("  ❌ No price data!")
        return False
    
    latest = result[0]['latest']
    days_old = (datetime.now().date() - latest).days
    
    # Allow weekend gap (max 4 days for long weekend)
    if days_old > 4:
        logger.warning(f"  ⚠️  Price data is {days_old} days old (latest: {latest})")
        return False
    else:
        logger.info(f"  ✅ Price data up to {latest} ({days_old} days old)")
    
    return True


def check_yahoo_connection():
    """Test Yahoo Finance rate limit status."""
    import yfinance as yf
    
    try:
        # Single lightweight request
        stock = yf.Ticker("VOLV-B.ST")
        hist = stock.history(period="1d")
        if hist.empty:
            logger.warning("  ⚠️  Yahoo returned empty data (possibly rate-limited)")
            return False
        logger.info(f"  ✅ Yahoo Finance responding")
        return True
    except Exception as e:
        if "Too Many Requests" in str(e) or "429" in str(e):
            logger.error(f"  ❌ Yahoo rate-limited: {e}")
        else:
            logger.error(f"  ❌ Yahoo error: {e}")
        return False


def check_llm_connection():
    """Verify LLM (LM Studio) is reachable."""
    import os
    import requests
    
    ollama_url = os.getenv('OLLAMA_URL', 'http://192.168.99.19:1234')
    
    try:
        resp = requests.get(f"{ollama_url}/v1/models", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get('data', [])
            logger.info(f"  ✅ LLM available ({len(models)} models)")
            return True
        else:
            logger.warning(f"  ⚠️  LLM returned {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"  ❌ LLM unreachable: {e}")
        return False


def main():
    """Run all healthchecks."""
    logger.info("🏥 Trading Agent Healthcheck\n")
    
    checks = [
        ("Database", check_database),
        ("Data freshness", check_data_freshness),
        ("Yahoo Finance", check_yahoo_connection),
        ("LLM (LM Studio)", check_llm_connection),
    ]
    
    results = []
    for name, check_fn in checks:
        logger.info(f"📋 {name}:")
        try:
            passed = check_fn()
            results.append((name, passed))
        except Exception as e:
            logger.error(f"  ❌ Exception: {e}")
            results.append((name, False))
        logger.info("")
    
    # Summary
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    logger.info("=" * 40)
    if passed == total:
        logger.info(f"✅ All checks passed ({passed}/{total})")
        return 0
    else:
        failed = [name for name, ok in results if not ok]
        logger.error(f"❌ {total - passed} check(s) failed: {', '.join(failed)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
