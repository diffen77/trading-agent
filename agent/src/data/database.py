"""
Database Layer

Handles all PostgreSQL interactions for the trading agent.
"""

import os
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


class Database:
    """Database interface for trading agent."""
    
    def __init__(self):
        self.database_url = os.getenv(
            'DATABASE_URL',
            'postgresql://trading:trading_dev_123@localhost:5432/trading_agent'
        )
        self.engine = create_engine(self.database_url)
        self.Session = sessionmaker(bind=self.engine)
        
    def save_prices(self, df: pd.DataFrame):
        """Save price data to database."""
        with self.Session() as session:
            for idx, row in df.iterrows():
                session.execute(text("""
                    INSERT INTO prices (ticker, date, open, high, low, close, volume)
                    VALUES (:ticker, :date, :open, :high, :low, :close, :volume)
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume
                """), {
                    'ticker': row['ticker'],
                    'date': idx.date() if hasattr(idx, 'date') else idx,
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': int(row['Volume']),
                })
            session.commit()
    
    def save_fundamentals(self, data: dict):
        """Save fundamental data."""
        with self.Session() as session:
            session.execute(text("""
                INSERT INTO fundamentals (ticker, date, pe_ratio, pb_ratio, eps, 
                    dividend_yield, market_cap, revenue, profit_margin, data)
                VALUES (:ticker, :date, :pe_ratio, :pb_ratio, :eps,
                    :dividend_yield, :market_cap, :revenue, :profit_margin, :data)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    pe_ratio = EXCLUDED.pe_ratio,
                    pb_ratio = EXCLUDED.pb_ratio,
                    eps = EXCLUDED.eps,
                    dividend_yield = EXCLUDED.dividend_yield,
                    market_cap = EXCLUDED.market_cap,
                    revenue = EXCLUDED.revenue,
                    profit_margin = EXCLUDED.profit_margin,
                    data = EXCLUDED.data
            """), {
                'ticker': data['ticker'],
                'date': date.today(),
                'pe_ratio': data.get('pe_ratio'),
                'pb_ratio': data.get('pb_ratio'),
                'eps': data.get('eps'),
                'dividend_yield': data.get('dividend_yield'),
                'market_cap': data.get('market_cap'),
                'revenue': data.get('revenue'),
                'profit_margin': data.get('profit_margin'),
                'data': str(data),  # Store full data as JSON
            })
            session.commit()
            
            # Also update company info if we have it
            if data.get('sector') or data.get('description'):
                session.execute(text("""
                    INSERT INTO companies (ticker, name, sector, industry, description)
                    VALUES (:ticker, :ticker, :sector, :industry, :description)
                    ON CONFLICT (ticker) DO UPDATE SET
                        sector = COALESCE(EXCLUDED.sector, companies.sector),
                        industry = COALESCE(EXCLUDED.industry, companies.industry),
                        description = COALESCE(EXCLUDED.description, companies.description),
                        updated_at = NOW()
                """), {
                    'ticker': data['ticker'],
                    'sector': data.get('sector'),
                    'industry': data.get('industry'),
                    'description': data.get('description'),
                })
                session.commit()
    
    def save_macro(self, df: pd.DataFrame):
        """Save macro data."""
        with self.Session() as session:
            for _, row in df.iterrows():
                session.execute(text("""
                    INSERT INTO macro (symbol, type, date, value, change_pct)
                    VALUES (:symbol, :type, :date, :value, :change_pct)
                    ON CONFLICT (symbol, date) DO UPDATE SET
                        value = EXCLUDED.value,
                        change_pct = EXCLUDED.change_pct
                """), {
                    'symbol': row['symbol'],
                    'type': row['type'],
                    'date': row['date'],
                    'value': float(row['value']),
                    'change_pct': float(row['change_pct']),
                })
            session.commit()
    
    def get_latest_prices(self, tickers: Optional[List[str]] = None) -> pd.DataFrame:
        """Get latest prices for tickers."""
        query = """
            SELECT DISTINCT ON (ticker) *
            FROM prices
            {}
            ORDER BY ticker, date DESC
        """
        where = ""
        params = {}
        if tickers:
            where = "WHERE ticker = ANY(:tickers)"
            params['tickers'] = tickers
            
        with self.Session() as session:
            result = session.execute(text(query.format(where)), params)
            return pd.DataFrame(result.fetchall(), columns=result.keys())
    
    def get_portfolio(self) -> pd.DataFrame:
        """Get current portfolio positions."""
        with self.Session() as session:
            result = session.execute(text("SELECT * FROM portfolio"))
            return pd.DataFrame(result.fetchall(), columns=result.keys())
    
    def get_balance(self) -> dict:
        """Get current cash balance and total value."""
        with self.Session() as session:
            result = session.execute(text(
                "SELECT cash, total_value, updated_at FROM balance ORDER BY id DESC LIMIT 1"
            ))
            row = result.fetchone()
            return {
                'cash': float(row[0]),
                'total_value': float(row[1]),
                'updated_at': row[2],
            }
    
    def log_trade(self, trade: dict):
        """Log a trade with reasoning and update portfolio/balance."""
        with self.Session() as session:
            # Log the trade
            session.execute(text("""
                INSERT INTO trades (ticker, action, shares, price, total_value,
                    reasoning, confidence, hypothesis, macro_context)
                VALUES (:ticker, :action, :shares, :price, :total_value,
                    :reasoning, :confidence, :hypothesis, :macro_context)
            """), {
                'ticker': trade['ticker'],
                'action': trade['action'],
                'shares': trade['shares'],
                'price': trade['price'],
                'total_value': trade['total_value'],
                'reasoning': trade['reasoning'],
                'confidence': trade.get('confidence'),
                'hypothesis': trade.get('hypothesis'),
                'macro_context': trade.get('macro_context', '{}'),
            })
            
            # Update balance
            if trade['action'] == 'BUY':
                session.execute(text("""
                    UPDATE balance SET cash = cash - :amount, updated_at = NOW()
                """), {'amount': trade['total_value']})
                
                # Add to portfolio (or update existing position)
                session.execute(text("""
                    INSERT INTO portfolio (ticker, shares, avg_price, current_price)
                    VALUES (:ticker, :shares, :price, :price)
                    ON CONFLICT (ticker) DO UPDATE SET
                        shares = portfolio.shares + EXCLUDED.shares,
                        avg_price = (portfolio.avg_price * portfolio.shares + :price * :shares) 
                                    / (portfolio.shares + :shares),
                        updated_at = NOW()
                """), {
                    'ticker': trade['ticker'],
                    'shares': trade['shares'],
                    'price': trade['price'],
                })
            elif trade['action'] == 'SELL':
                session.execute(text("""
                    UPDATE balance SET cash = cash + :amount, updated_at = NOW()
                """), {'amount': trade['total_value']})
                
                # Remove from portfolio
                session.execute(text("""
                    UPDATE portfolio SET shares = shares - :shares, updated_at = NOW()
                    WHERE ticker = :ticker
                """), {'ticker': trade['ticker'], 'shares': trade['shares']})
            
            session.commit()
    
    def get_trades(self, limit: int = 50) -> pd.DataFrame:
        """Get recent trades."""
        with self.Session() as session:
            result = session.execute(text(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT :limit"
            ), {'limit': limit})
            return pd.DataFrame(result.fetchall(), columns=result.keys())
    
    def get_learnings(self, active_only: bool = True) -> List[dict]:
        """Get agent learnings."""
        with self.Session() as session:
            query = "SELECT * FROM learnings"
            if active_only:
                query += " WHERE active = true"
            query += " ORDER BY confidence DESC"
            result = session.execute(text(query))
            return [dict(row._mapping) for row in result.fetchall()]
    
    def add_learning(self, learning: dict):
        """Add a new learning."""
        with self.Session() as session:
            session.execute(text("""
                INSERT INTO learnings (category, content, source_trade_ids, confidence)
                VALUES (:category, :content, :source_trade_ids, :confidence)
            """), {
                'category': learning['category'],
                'content': learning['content'],
                'source_trade_ids': learning.get('source_trade_ids'),
                'confidence': learning.get('confidence', 50),
            })
            session.commit()
