"""
News Fetcher

Fetches news from various sources and analyzes sentiment.
Sources:
- Avanza (Swedish stocks)
- Yahoo Finance
- RSS feeds from DI, Placera
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import feedparser
import requests

logger = logging.getLogger(__name__)

# Swedish financial news RSS feeds
RSS_FEEDS = {
    'di': 'https://www.di.se/rss',
    'avanza': 'https://www.avanza.se/placera/telegram.rss.xml',
    'placera': 'https://www.avanza.se/placera/redaktionellt.rss.xml',
}

# Keywords for sentiment analysis (Swedish)
POSITIVE_KEYWORDS = [
    'ökar', 'höjer', 'vinst', 'tillväxt', 'rekord', 'stark', 'uppgång',
    'överträffar', 'bättre', 'köp', 'uppgradering', 'optimism', 'framgång',
    'order', 'kontrakt', 'avtal', 'expansion', 'lanserar', 'innovation',
]

NEGATIVE_KEYWORDS = [
    'minskar', 'sänker', 'förlust', 'nedgång', 'svag', 'varning', 'risk',
    'sämre', 'sälj', 'nedgradering', 'oro', 'problem', 'kris', 'konkurs',
    'uppsägning', 'stänger', 'avslutar', 'försenad', 'kostnad',
]

# Company name mappings for ticker detection
COMPANY_NAMES = {
    'volvo': 'VOLV-B',
    'ericsson': 'ERIC-B',
    'h&m': 'HM-B',
    'hennes': 'HM-B',
    'sandvik': 'SAND',
    'atlas copco': 'ATCO-A',
    'skf': 'SKF-B',
    'saab': 'SAAB-B',
    'hexagon': 'HEXA-B',
    'abb': 'ABB',
    'ssab': 'SSAB-A',
    'boliden': 'BOL',
    'telia': 'TELIA',
    'tele2': 'TEL2-B',
    'swedbank': 'SWED-A',
    'seb': 'SEB-A',
    'handelsbanken': 'SHB-A',
    'nordea': 'NDA-SE',
    'investor': 'INVE-B',
    'kinnevik': 'KINV-B',
    'astrazeneca': 'AZN',
    'essity': 'ESSITY-B',
    'getinge': 'GETI-B',
    'alfa laval': 'ALFA',
    'assa abloy': 'ASSA-B',
    'electrolux': 'ELUX-B',
    'husqvarna': 'HUSQ-B',
    'nibe': 'NIBE-B',
    'evolution': 'EVO',
    'skanska': 'SKA-B',
    'securitas': 'SECU-B',
    'castellum': 'CAST',
    'balder': 'BALD-B',
    'fabege': 'FABG',
    'hufvudstaden': 'HUFV-A',
    'latour': 'LATO-B',
    'lundberg': 'LUND-B',
    'sca': 'SCA-B',
    'billerud': 'BILL',
    'trelleborg': 'TREL-B',
    'thule': 'THULE',
    'mips': 'MIPS',
    'sinch': 'SINCH',
    'peab': 'PEAB-B',
    'ica': 'ICA',
    'addtech': 'ADDT-B',
    'clas ohlson': 'CLAS-B',
    'diös': 'DIOS',
    'sagax': 'SAGA-B',
    'wihlborg': 'WIHL',
}


class NewsFetcher:
    """Fetches and analyzes financial news."""
    
    def __init__(self, db):
        self.db = db
        self._load_company_names_from_db()
    
    def _load_company_names_from_db(self):
        """Augment COMPANY_NAMES with names from database."""
        global COMPANY_NAMES
        try:
            companies = self.db.query("SELECT ticker, name FROM companies")
            for c in companies:
                # Add lowercase name → ticker mapping
                name = c['name'].lower()
                if name not in COMPANY_NAMES:
                    COMPANY_NAMES[name] = c['ticker']
                # Also add first word (e.g. "Volvo" from "Volvo B")
                first_word = name.split()[0] if name else ''
                if len(first_word) > 3 and first_word not in COMPANY_NAMES:
                    COMPANY_NAMES[first_word] = c['ticker']
        except Exception as e:
            logger.warning(f"Could not load company names from DB: {e}")
    
    def fetch_all_news(self) -> List[Dict]:
        """Fetch news from all sources."""
        all_news = []
        
        for source, url in RSS_FEEDS.items():
            try:
                news = self._fetch_rss(url, source)
                all_news.extend(news)
                logger.info(f"Fetched {len(news)} articles from {source}")
            except Exception as e:
                logger.error(f"Error fetching from {source}: {e}")
        
        return all_news
    
    def _fetch_rss(self, url: str, source: str) -> List[Dict]:
        """Fetch and parse RSS feed."""
        try:
            feed = feedparser.parse(url)
            articles = []
            
            for entry in feed.entries[:20]:  # Last 20 articles
                article = {
                    'headline': entry.get('title', ''),
                    'summary': entry.get('summary', entry.get('description', '')),
                    'url': entry.get('link', ''),
                    'source': source,
                    'published_at': self._parse_date(entry.get('published')),
                }
                
                # Detect ticker from headline/summary
                article['ticker'] = self._detect_ticker(
                    article['headline'] + ' ' + article['summary']
                )
                
                # Analyze sentiment
                sentiment = self._analyze_sentiment(
                    article['headline'] + ' ' + article['summary']
                )
                article['sentiment'] = sentiment['label']
                article['sentiment_score'] = sentiment['score']
                
                articles.append(article)
            
            return articles
            
        except Exception as e:
            logger.error(f"Error parsing RSS {url}: {e}")
            return []
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse various date formats."""
        if not date_str:
            return datetime.now()
        
        try:
            # Try common formats
            for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S']:
                try:
                    return datetime.strptime(date_str, fmt)
                except:
                    continue
            return datetime.now()
        except:
            return datetime.now()
    
    def _detect_ticker(self, text: str) -> Optional[str]:
        """Detect company ticker from text."""
        text_lower = text.lower()
        
        for company, ticker in COMPANY_NAMES.items():
            if company in text_lower:
                return ticker
        
        return None
    
    def _analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Simple keyword-based sentiment analysis."""
        text_lower = text.lower()
        
        positive_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
        negative_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
        
        total = positive_count + negative_count
        if total == 0:
            return {'label': 'neutral', 'score': 0.0}
        
        score = (positive_count - negative_count) / total
        
        if score > 0.2:
            label = 'positive'
        elif score < -0.2:
            label = 'negative'
        else:
            label = 'neutral'
        
        return {'label': label, 'score': round(score, 2)}
    
    def save_news(self, articles: List[Dict]) -> int:
        """Save articles to database."""
        saved = 0
        
        for article in articles:
            try:
                # Check if already exists (by URL)
                existing = self.db.query(
                    "SELECT id FROM news WHERE url = :url",
                    {'url': article['url']}
                )
                
                if existing:
                    continue
                
                self.db.execute("""
                    INSERT INTO news (ticker, headline, summary, source, url, 
                                      sentiment, sentiment_score, published_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    article.get('ticker'),
                    article['headline'][:500],
                    article.get('summary', '')[:1000],
                    article['source'],
                    article['url'],
                    article['sentiment'],
                    article['sentiment_score'],
                    article.get('published_at'),
                ))
                saved += 1
                
            except Exception as e:
                logger.error(f"Error saving article: {e}")
        
        logger.info(f"Saved {saved} new articles")
        return saved
    
    def get_news_for_ticker(self, ticker: str, days: int = 7) -> List[Dict]:
        """Get recent news for a specific ticker."""
        cutoff = datetime.now() - timedelta(days=days)
        
        return self.db.query("""
            SELECT * FROM news 
            WHERE ticker = :ticker AND published_at >= :cutoff
            ORDER BY published_at DESC
        """, {'ticker': ticker, 'cutoff': cutoff})
    
    def get_recent_news(self, hours: int = 24) -> List[Dict]:
        """Get all recent news."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        return self.db.query("""
            SELECT * FROM news 
            WHERE published_at >= :cutoff
            ORDER BY published_at DESC
            LIMIT 50
        """, {'cutoff': cutoff})
    
    def get_sentiment_summary(self, ticker: str, days: int = 7) -> Dict[str, Any]:
        """Get sentiment summary for a ticker."""
        news = self.get_news_for_ticker(ticker, days)
        
        if not news:
            return {'ticker': ticker, 'articles': 0, 'sentiment': 'neutral', 'score': 0}
        
        total_score = sum(float(n.get('sentiment_score', 0) or 0) for n in news)
        avg_score = total_score / len(news)
        
        positive = sum(1 for n in news if n.get('sentiment') == 'positive')
        negative = sum(1 for n in news if n.get('sentiment') == 'negative')
        
        return {
            'ticker': ticker,
            'articles': len(news),
            'positive': positive,
            'negative': negative,
            'neutral': len(news) - positive - negative,
            'avg_score': round(avg_score, 2),
            'sentiment': 'positive' if avg_score > 0.1 else ('negative' if avg_score < -0.1 else 'neutral'),
        }
    
    def update_news(self) -> Dict[str, Any]:
        """Main update routine - fetch and save all news."""
        logger.info("📰 Fetching news...")
        
        articles = self.fetch_all_news()
        saved = self.save_news(articles)
        
        # Count by sentiment
        positive = sum(1 for a in articles if a['sentiment'] == 'positive')
        negative = sum(1 for a in articles if a['sentiment'] == 'negative')
        
        # Get tickers mentioned
        tickers_mentioned = set(a['ticker'] for a in articles if a['ticker'])
        
        summary = {
            'total_fetched': len(articles),
            'new_saved': saved,
            'positive': positive,
            'negative': negative,
            'neutral': len(articles) - positive - negative,
            'tickers_mentioned': list(tickers_mentioned),
        }
        
        logger.info(f"📰 News update: {saved} new, {positive} positive, {negative} negative")
        
        return summary
    
    def generate_news_briefing(self) -> str:
        """Generate a news briefing for the morning routine."""
        recent = self.get_recent_news(hours=24)
        
        if not recent:
            return "📰 Inga nyheter senaste 24h"
        
        lines = ["📰 Nyhetsöversikt (24h):"]
        lines.append("=" * 40)
        
        # Group by sentiment
        positive = [n for n in recent if n.get('sentiment') == 'positive']
        negative = [n for n in recent if n.get('sentiment') == 'negative']
        
        if positive:
            lines.append(f"\n✅ Positiva nyheter ({len(positive)}):")
            for n in positive[:3]:
                ticker = f"[{n['ticker']}] " if n.get('ticker') else ""
                lines.append(f"  {ticker}{n['headline'][:60]}...")
        
        if negative:
            lines.append(f"\n⚠️ Negativa nyheter ({len(negative)}):")
            for n in negative[:3]:
                ticker = f"[{n['ticker']}] " if n.get('ticker') else ""
                lines.append(f"  {ticker}{n['headline'][:60]}...")
        
        # Tickers mentioned
        tickers = set(n['ticker'] for n in recent if n.get('ticker'))
        if tickers:
            lines.append(f"\n📊 Bolag i nyheterna: {', '.join(sorted(tickers))}")
        
        return "\n".join(lines)
