"""
Yahoo Finance Data Fetcher

Fetches stock prices, fundamentals, and macro data for all Stockholm stocks.
Rate-limited to avoid Yahoo blocking.
"""

import yfinance as yf
import pandas as pd
import logging
import time
import random
from typing import List, Optional
from datetime import datetime, timedelta

# Rate limiting config
REQUEST_DELAY_MIN = 3.0  # seconds between requests
REQUEST_DELAY_MAX = 6.0  # randomized to avoid detection
BATCH_SIZE = 5           # pause longer every N requests
BATCH_PAUSE_MIN = 15.0   # longer pause between batches
BATCH_PAUSE_MAX = 30.0
MAX_RETRIES = 2          # retry failed fetches

logger = logging.getLogger(__name__)

# Stockholm Stock Exchange tickers (suffix .ST)
# This is a starter list - will be expanded
STOCKHOLM_TICKERS = [
    # OMX30 Large Caps
    "ABB.ST", "ALFA.ST", "ASSA-B.ST", "ATCO-A.ST", "ATCO-B.ST",
    "AZN.ST", "BOL.ST", "ELUX-B.ST", "ERIC-B.ST", "ESSITY-B.ST",
    "EVO.ST", "GETI-B.ST", "HEXA-B.ST", "HM-B.ST", "INVE-B.ST",
    "KINV-B.ST", "NIBE-B.ST", "SAND.ST", "SCA-B.ST", "SEB-A.ST",
    "SHB-A.ST", "SKA-B.ST", "SKF-B.ST", "SSAB-A.ST", "SWED-A.ST",
    "TEL2-B.ST", "TELIA.ST", "VOLV-B.ST",
    # Additional interesting stocks
    "SAAB-B.ST", "ADDT-B.ST", "BALD-B.ST", "BILL.ST",
    "CAST.ST", "CLAS-B.ST", "DIOS.ST",
    "FABG.ST", "HUFV-A.ST", "HUSQ-B.ST",
    "JM.ST", "LATO-B.ST", "LUND-B.ST", "MIPS.ST",
    "NDA-SE.ST", "ORES.ST", "PEAB-B.ST", "SAGA-B.ST",
    "SECU-B.ST", "SINCH.ST", "THULE.ST", "TREL-B.ST",
    "WIHL.ST",
]

# Macro symbols
MACRO_SYMBOLS = {
    # Commodities
    "GC=F": ("Gold", "commodity"),
    "SI=F": ("Silver", "commodity"),
    "CL=F": ("Oil (WTI)", "commodity"),
    "BZ=F": ("Oil (Brent)", "commodity"),
    "HG=F": ("Copper", "commodity"),
    "NG=F": ("Natural Gas", "commodity"),
    # Currencies
    "EURSEK=X": ("EUR/SEK", "currency"),
    "USDSEK=X": ("USD/SEK", "currency"),
    "EURUSD=X": ("EUR/USD", "currency"),
    # Indices
    "^OMX": ("OMX30", "index"),
    "^OMXSPI": ("OMXS All-Share", "index"),
    "^GSPC": ("S&P 500", "index"),
}


class YahooDataFetcher:
    """Fetches data from Yahoo Finance."""
    
    def __init__(self):
        self.tickers = STOCKHOLM_TICKERS
        
    def get_all_tickers(self) -> List[str]:
        """Return all tracked Stockholm tickers."""
        return self.tickers
    
    def fetch_price(self, ticker: str, period: str = "1d") -> Optional[pd.DataFrame]:
        """Fetch price data for a single ticker."""
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)
            return hist
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            return None
    
    def fetch_all_prices(self, period: str = "6mo") -> pd.DataFrame:
        """Fetch prices for all Stockholm stocks with rate limiting and batching."""
        logger.info(f"Fetching prices for {len(self.tickers)} stocks (rate-limited, batch={BATCH_SIZE})...")
        
        all_data = []
        failed = []
        for i, ticker in enumerate(self.tickers):
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period=period)
                if not hist.empty:
                    hist['ticker'] = ticker.replace('.ST', '')
                    all_data.append(hist)
                    logger.debug(f"Fetched {ticker} ({i+1}/{len(self.tickers)})")
                else:
                    failed.append(ticker)
            except Exception as e:
                logger.warning(f"Could not fetch {ticker}: {e}")
                failed.append(ticker)
            
            # Rate limiting - batch pause every BATCH_SIZE requests
            if i < len(self.tickers) - 1:
                if (i + 1) % BATCH_SIZE == 0:
                    pause = random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX)
                    logger.info(f"Batch pause {pause:.0f}s after {i+1}/{len(self.tickers)} tickers...")
                    time.sleep(pause)
                else:
                    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                    time.sleep(delay)
        
        # Retry failed tickers with longer delays
        if failed:
            logger.info(f"Retrying {len(failed)} failed tickers with longer delays...")
            time.sleep(random.uniform(30, 60))
            for i, ticker in enumerate(failed):
                for attempt in range(MAX_RETRIES):
                    try:
                        stock = yf.Ticker(ticker)
                        hist = stock.history(period=period)
                        if not hist.empty:
                            hist['ticker'] = ticker.replace('.ST', '')
                            all_data.append(hist)
                            logger.info(f"Retry OK: {ticker} (attempt {attempt+1})")
                            break
                    except Exception as e:
                        logger.warning(f"Retry {attempt+1} failed for {ticker}: {e}")
                    time.sleep(random.uniform(10, 20))
                time.sleep(random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX))
                
        if all_data:
            return pd.concat(all_data)
        return pd.DataFrame()
    
    def fetch_fundamentals(self, ticker: str) -> dict:
        """Fetch fundamental data for a ticker."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return {
                'ticker': ticker.replace('.ST', ''),
                'pe_ratio': info.get('trailingPE'),
                'pb_ratio': info.get('priceToBook'),
                'eps': info.get('trailingEps'),
                'dividend_yield': info.get('dividendYield'),
                'market_cap': info.get('marketCap'),
                'revenue': info.get('totalRevenue'),
                'profit_margin': info.get('profitMargins'),
                'sector': info.get('sector'),
                'industry': info.get('industry'),
                'description': info.get('longBusinessSummary'),
            }
        except Exception as e:
            logger.error(f"Error fetching fundamentals for {ticker}: {e}")
            return {}
    
    def fetch_macro(self) -> pd.DataFrame:
        """Fetch macro data (commodities, currencies)."""
        logger.info("Fetching macro data...")
        
        data = []
        for symbol, (name, type_) in MACRO_SYMBOLS.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2] if len(hist) > 1 else hist.iloc[-1]
                    change_pct = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
                    
                    data.append({
                        'symbol': symbol,
                        'name': name,
                        'type': type_,
                        'value': latest['Close'],
                        'change_pct': change_pct,
                        'date': hist.index[-1].date(),
                    })
            except Exception as e:
                logger.warning(f"Could not fetch {symbol}: {e}")
                
        return pd.DataFrame(data)
    
    def update_all_prices(self, db):
        """Update all prices in database."""
        prices_df = self.fetch_all_prices(period="6mo")
        if not prices_df.empty:
            db.save_prices(prices_df)
            logger.info(f"Updated {len(prices_df)} price records")
    
    def update_fundamentals(self, db):
        """Update fundamentals for all stocks with rate limiting."""
        for i, ticker in enumerate(self.tickers):
            fundamentals = self.fetch_fundamentals(ticker)
            if fundamentals:
                db.save_fundamentals(fundamentals)
            # Rate limiting
            if i < len(self.tickers) - 1:
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                time.sleep(delay)
        logger.info("Updated fundamentals")
    
    def update_macro_data(self, db):
        """Update macro data in database."""
        macro_df = self.fetch_macro()
        if not macro_df.empty:
            db.save_macro(macro_df)
            logger.info(f"Updated {len(macro_df)} macro records")
