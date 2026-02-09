"""
Report Calendar Tracker

Fetches upcoming earnings report dates for tracked companies.
Sources: börskollen.se, EarningsWhispers, or similar.
"""

import logging
import re
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Map quarter months to report types
QUARTER_MAP = {
    1: 'Q4', 2: 'Q4', 3: 'Q4',       # Jan-Mar reports = Q4 previous year
    4: 'Q1', 5: 'Q1', 6: 'Q1',       # Apr-Jun reports = Q1
    7: 'Q2', 8: 'Q2', 9: 'Q2',       # Jul-Sep reports = Q2
    10: 'Q3', 11: 'Q3', 12: 'Q3',    # Oct-Dec reports = Q3
}


class ReportTracker:
    """Tracks upcoming earnings reports for Swedish companies."""
    
    def __init__(self, db):
        self.db = db
        self._company_cache = {}
        self._load_companies()
    
    def _load_companies(self):
        """Load tracked companies from DB."""
        try:
            companies = self.db.query("SELECT ticker, name FROM companies")
            for c in companies:
                self._company_cache[c['ticker']] = c['name']
        except Exception as e:
            logger.error(f"Error loading companies: {e}")
    
    def fetch_report_calendar(self) -> List[Dict]:
        """Fetch upcoming report dates from börskollen.se."""
        reports = []
        
        try:
            url = "https://www.borskollen.se/rapportkalender"
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            }
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Find report entries - structure may vary
            # Try common patterns
            rows = soup.select('tr, .report-row, .calendar-item')
            
            for row in rows:
                try:
                    text = row.get_text(' ', strip=True)
                    report = self._parse_report_row(text)
                    if report:
                        reports.append(report)
                except Exception:
                    continue
            
            logger.info(f"📅 Fetched {len(reports)} report dates from börskollen")
            
        except Exception as e:
            logger.warning(f"Could not fetch from börskollen: {e}")
        
        # Fallback: try to scrape from Avanza
        if not reports:
            reports = self._fetch_from_avanza()
        
        return reports
    
    def _parse_report_row(self, text: str) -> Optional[Dict]:
        """Try to parse a report row text into structured data."""
        # Look for date patterns (YYYY-MM-DD or DD/MM)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if not date_match:
            date_match = re.search(r'(\d{1,2})[/.](\d{1,2})', text)
        
        if not date_match:
            return None
        
        # Try to match company name to our tracked tickers
        text_lower = text.lower()
        matched_ticker = None
        
        for ticker, name in self._company_cache.items():
            name_lower = name.lower()
            # Check company name or ticker in text
            if name_lower in text_lower or ticker.lower().replace('-', '') in text_lower.replace('-', ''):
                matched_ticker = ticker
                break
            # Check first word of name
            first_word = name_lower.split()[0]
            if len(first_word) > 3 and first_word in text_lower:
                matched_ticker = ticker
                break
        
        if not matched_ticker:
            return None
        
        # Parse date
        try:
            if '-' in date_match.group(0):
                report_date = datetime.strptime(date_match.group(0), '%Y-%m-%d').date()
            else:
                day, month = int(date_match.group(1)), int(date_match.group(2))
                year = datetime.now().year
                report_date = date(year, month, day)
        except (ValueError, IndexError):
            return None
        
        # Determine report type from date
        report_type = self._guess_report_type(text, report_date)
        
        return {
            'ticker': matched_ticker,
            'report_date': report_date,
            'report_type': report_type,
            'source': 'borskollen',
        }
    
    def _guess_report_type(self, text: str, report_date: date) -> str:
        """Guess report type from text and date."""
        text_lower = text.lower()
        
        if 'bokslut' in text_lower or 'årsredovisning' in text_lower:
            return 'bokslut'
        
        for q in ['q1', 'q2', 'q3', 'q4']:
            if q in text_lower:
                return q.upper()
        
        # Default based on report month
        return QUARTER_MAP.get(report_date.month, 'Q4')
    
    def _fetch_from_avanza(self) -> List[Dict]:
        """Fallback: try Avanza's report calendar."""
        try:
            url = "https://www.avanza.se/placera/rapportkalender.html"
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                rows = soup.select('tr')
                reports = []
                for row in rows:
                    text = row.get_text(' ', strip=True)
                    report = self._parse_report_row(text)
                    if report:
                        report['source'] = 'avanza'
                        reports.append(report)
                logger.info(f"📅 Fetched {len(reports)} report dates from Avanza")
                return reports
        except Exception as e:
            logger.warning(f"Could not fetch from Avanza: {e}")
        
        return []
    
    def save_reports(self, reports: List[Dict]) -> int:
        """Save report dates to database."""
        saved = 0
        for r in reports:
            try:
                self.db.execute("""
                    INSERT INTO report_calendar (ticker, report_date, report_type, source)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (ticker, report_date) DO UPDATE SET
                        report_type = EXCLUDED.report_type,
                        source = EXCLUDED.source
                """, (r['ticker'], r['report_date'], r['report_type'], r['source']))
                saved += 1
            except Exception as e:
                logger.error(f"Error saving report for {r['ticker']}: {e}")
        
        logger.info(f"📅 Saved {saved} report dates")
        return saved
    
    def get_upcoming_reports(self, days: int = 7) -> List[Dict]:
        """Get reports within the next N days."""
        cutoff = date.today() + timedelta(days=days)
        return self.db.query("""
            SELECT rc.*, c.name 
            FROM report_calendar rc
            LEFT JOIN companies c ON c.ticker = rc.ticker
            WHERE rc.report_date BETWEEN CURRENT_DATE AND :cutoff
            ORDER BY rc.report_date
        """, {'cutoff': cutoff})
    
    def flag_upcoming_in_prospects(self, days: int = 2) -> int:
        """Flag companies reporting within N days in prospects."""
        upcoming = self.get_upcoming_reports(days=days)
        flagged = 0
        
        for r in upcoming:
            try:
                # Update prospect entry_trigger if exists
                self.db.execute("""
                    UPDATE prospects SET
                        entry_trigger = CONCAT('⚠️ RAPPORT ', %s, ' den ', %s, '. ', entry_trigger),
                        updated_at = NOW()
                    WHERE ticker = %s
                    AND entry_trigger NOT LIKE '%%RAPPORT%%'
                """, (r['report_type'], str(r['report_date']), r['ticker']))
                flagged += 1
            except Exception as e:
                logger.error(f"Error flagging {r['ticker']}: {e}")
        
        if flagged:
            logger.info(f"📅 Flagged {flagged} upcoming reports in prospects")
        return flagged
    
    def update_report_calendar(self) -> Dict:
        """Main update routine."""
        logger.info("📅 Updating report calendar...")
        reports = self.fetch_report_calendar()
        saved = self.save_reports(reports)
        flagged = self.flag_upcoming_in_prospects()
        
        return {
            'fetched': len(reports),
            'saved': saved,
            'flagged': flagged,
        }
