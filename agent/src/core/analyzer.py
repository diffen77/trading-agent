"""
Market Analyzer

Analyzes companies, macro factors, and finds trading opportunities.
Understands relationships between macro events and company impacts.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


# Company input mappings - what raw materials/factors affect each sector
SECTOR_INPUTS = {
    'Industrials': ['steel', 'copper', 'energy', 'EUR/SEK'],
    'Basic Materials': ['iron', 'copper', 'gold', 'energy'],
    'Consumer Cyclical': ['cotton', 'EUR/SEK', 'consumer_confidence'],
    'Technology': ['semiconductors', 'USD/SEK', 'energy'],
    'Financial Services': ['interest_rates', 'EUR/SEK'],
    'Healthcare': ['USD/SEK', 'regulatory'],
    'Energy': ['oil', 'natural_gas', 'EUR/SEK'],
    'Real Estate': ['interest_rates', 'construction_costs'],
    'Consumer Defensive': ['food_prices', 'EUR/SEK'],
    'Communication Services': ['USD/SEK', 'advertising_spend'],
    'Utilities': ['energy', 'interest_rates'],
}

# Specific company mappings with detailed info
COMPANY_DATA = {
    'VOLV-B': {
        'name': 'Volvo B',
        'sector': 'Industrials',
        'inputs': ['steel', 'copper', 'EUR/SEK', 'oil'],
        'description': 'Lastbilar, bussar, anläggningsmaskiner',
        'export_pct': 95,
        'main_markets': ['Europa', 'Nordamerika', 'Asien'],
    },
    'SSAB-A': {
        'name': 'SSAB A', 
        'sector': 'Basic Materials',
        'inputs': ['iron', 'coal', 'energy', 'EUR/SEK'],
        'description': 'Stålproducent, fossilfritt stål (HYBRIT)',
        'export_pct': 85,
        'main_markets': ['Europa', 'Nordamerika'],
    },
    'SKF-B': {
        'name': 'SKF B',
        'sector': 'Industrials', 
        'inputs': ['steel', 'EUR/SEK'],
        'description': 'Kullager, tätningar - fordon och industri',
        'export_pct': 90,
        'main_markets': ['Global'],
    },
    'SAND': {
        'name': 'Sandvik',
        'sector': 'Industrials',
        'inputs': ['tungsten', 'cobalt', 'EUR/SEK'],
        'description': 'Verktyg, gruvutrustning, materialteknik',
        'export_pct': 95,
        'main_markets': ['Global'],
    },
    'HM-B': {
        'name': 'H&M B',
        'sector': 'Consumer Cyclical',
        'inputs': ['cotton', 'USD/SEK', 'shipping'],
        'description': 'Klädkedja, fast fashion',
        'export_pct': 80,
        'main_markets': ['Europa', 'USA', 'Asien'],
    },
    'ERIC-B': {
        'name': 'Ericsson B',
        'sector': 'Technology',
        'inputs': ['semiconductors', 'USD/SEK'],
        'description': '5G-infrastruktur, telekomutrustning',
        'export_pct': 95,
        'main_markets': ['Global'],
    },
    'SAAB-B': {
        'name': 'Saab B',
        'sector': 'Industrials',
        'inputs': ['aluminum', 'steel', 'EUR/SEK'],
        'description': 'Försvar, säkerhet - Gripen, radar, ubåtar',
        'export_pct': 60,
        'main_markets': ['Sverige', 'Europa', 'Global'],
    },
    'ABB': {
        'name': 'ABB Ltd',
        'sector': 'Industrials',
        'inputs': ['copper', 'steel', 'EUR/SEK'],
        'description': 'Automation, robotik, kraftnät',
        'export_pct': 95,
        'main_markets': ['Global'],
    },
    'ATCO-A': {
        'name': 'Atlas Copco A',
        'sector': 'Industrials',
        'inputs': ['steel', 'copper', 'EUR/SEK'],
        'description': 'Kompressorer, vakuumteknik, industriverktyg',
        'export_pct': 95,
        'main_markets': ['Global'],
    },
    'HEXA-B': {
        'name': 'Hexagon B',
        'sector': 'Technology',
        'inputs': ['semiconductors', 'EUR/SEK'],
        'description': 'Mätteknik, sensorer, mjukvara för automation',
        'export_pct': 95,
        'main_markets': ['Global'],
    },
}

# Macro symbol mappings
MACRO_SYMBOLS = {
    'GC=F': {'name': 'Guld', 'type': 'commodity'},
    'SI=F': {'name': 'Silver', 'type': 'commodity'},
    'HG=F': {'name': 'Koppar', 'type': 'commodity'},
    'BZ=F': {'name': 'Brent Olja', 'type': 'commodity'},
    'EURSEK=X': {'name': 'EUR/SEK', 'type': 'currency'},
    'USDSEK=X': {'name': 'USD/SEK', 'type': 'currency'},
    '^OMX': {'name': 'OMX Stockholm 30', 'type': 'index'},
}


class MarketAnalyzer:
    """Analyzes market conditions and finds opportunities."""
    
    def __init__(self, db):
        self.db = db
        
    def get_company_info(self, ticker: str) -> Dict[str, Any]:
        """Get company information."""
        return COMPANY_DATA.get(ticker, {})
    
    def get_company_inputs(self, ticker: str) -> List[str]:
        """Get what inputs/factors affect a company."""
        info = self.get_company_info(ticker)
        return info.get('inputs', [])
    
    def get_latest_macro(self) -> Dict[str, Dict]:
        """Get latest macro data from database."""
        try:
            result = self.db.query("""
                SELECT DISTINCT ON (symbol) symbol, value, change_pct, date
                FROM macro
                ORDER BY symbol, date DESC
            """)
            return {row['symbol']: row for row in result}
        except Exception as e:
            logger.error(f"Error fetching macro data: {e}")
            return {}
    
    def get_latest_prices(self) -> Dict[str, Dict]:
        """Get latest stock prices."""
        try:
            result = self.db.query("""
                SELECT DISTINCT ON (ticker) ticker, close, date,
                       (close - LAG(close) OVER (PARTITION BY ticker ORDER BY date)) / 
                       NULLIF(LAG(close) OVER (PARTITION BY ticker ORDER BY date), 0) * 100 as change_pct
                FROM prices
                ORDER BY ticker, date DESC
            """)
            return {row['ticker']: row for row in result}
        except Exception as e:
            logger.error(f"Error fetching prices: {e}")
            return {}
    
    def analyze_macro_impact(self, ticker: str) -> Dict[str, Any]:
        """
        Analyze how current macro conditions affect a company.
        """
        inputs = self.get_company_inputs(ticker)
        macro = self.get_latest_macro()
        
        impacts = []
        net_score = 0
        
        for input_factor in inputs:
            impact = self._analyze_input_impact(input_factor, macro)
            if impact:
                impacts.append(impact)
                net_score += impact['score']
        
        # Normalize score to -1 to 1
        if impacts:
            net_sentiment = net_score / len(impacts)
        else:
            net_sentiment = 0
        
        return {
            'ticker': ticker,
            'inputs': inputs,
            'impacts': impacts,
            'net_sentiment': net_sentiment,
        }
    
    def _analyze_input_impact(self, input_factor: str, macro: Dict) -> Optional[Dict]:
        """Analyze impact of a single input factor."""
        # Map input factors to macro symbols
        input_to_symbol = {
            'steel': None,  # No direct symbol, use iron + coal
            'iron': None,
            'copper': 'HG=F',
            'oil': 'BZ=F',
            'EUR/SEK': 'EURSEK=X',
            'USD/SEK': 'USDSEK=X',
            'gold': 'GC=F',
            'energy': 'BZ=F',
        }
        
        symbol = input_to_symbol.get(input_factor)
        if not symbol or symbol not in macro:
            return None
        
        data = macro[symbol]
        change_pct = float(data.get('change_pct', 0) or 0)
        
        # Determine impact direction
        # For costs (copper, oil): negative change = positive for company
        # For export currencies (EUR/SEK): positive change = positive for exporters
        cost_inputs = ['copper', 'oil', 'energy', 'steel', 'iron']
        
        if input_factor in cost_inputs:
            # Lower costs = good
            score = -change_pct / 10  # Normalize
            direction = 'positive' if change_pct < 0 else 'negative'
            reason = f"{input_factor.title()} {'ner' if change_pct < 0 else 'upp'} {abs(change_pct):.1f}%"
        else:
            # Currency - weaker SEK = good for exporters
            score = change_pct / 10
            direction = 'positive' if change_pct > 0 else 'negative'
            reason = f"{input_factor} {'svagare' if change_pct > 0 else 'starkare'} SEK ({change_pct:+.1f}%)"
        
        return {
            'factor': input_factor,
            'direction': direction,
            'score': max(-1, min(1, score)),  # Clamp to -1, 1
            'reason': reason,
            'change_pct': change_pct,
        }
    
    def find_opportunities(self) -> List[Dict[str, Any]]:
        """
        Find trading opportunities based on:
        1. Macro changes that benefit specific companies
        2. Price momentum
        3. Valuation (if available)
        """
        opportunities = []
        prices = self.get_latest_prices()
        
        logger.info("🔍 Scanning for opportunities...")
        
        for ticker, company in COMPANY_DATA.items():
            # Skip if no price data
            if ticker not in prices:
                continue
            
            price_data = prices[ticker]
            current_price = float(price_data.get('close', 0))
            
            if current_price <= 0:
                continue
            
            # Analyze macro impact
            analysis = self.analyze_macro_impact(ticker)
            
            # Calculate opportunity score
            score = self._calculate_opportunity_score(
                ticker, company, analysis, price_data
            )
            
            if score['total'] >= 50:  # Minimum threshold
                opp = {
                    'ticker': ticker,
                    'name': company['name'],
                    'current_price': current_price,
                    'confidence': score['total'],
                    'thesis': self._generate_thesis(ticker, company, analysis, score),
                    'entry_trigger': self._generate_entry_trigger(ticker, current_price, score),
                    'macro_sentiment': analysis['net_sentiment'],
                    'impacts': analysis['impacts'],
                    'score_breakdown': score,
                }
                opportunities.append(opp)
                logger.info(f"  📊 {ticker}: {score['total']:.0f}% confidence")
        
        # Sort by confidence
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)
        
        logger.info(f"✅ Found {len(opportunities)} opportunities")
        return opportunities
    
    def _calculate_opportunity_score(
        self, 
        ticker: str, 
        company: Dict, 
        analysis: Dict,
        price_data: Dict
    ) -> Dict[str, float]:
        """Calculate composite opportunity score."""
        
        # Macro score (0-40 points)
        macro_score = (analysis['net_sentiment'] + 1) * 20  # -1 to 1 -> 0 to 40
        
        # Momentum score (0-30 points) - based on recent price action
        price_change = float(price_data.get('change_pct', 0) or 0)
        if price_change > 0:
            momentum_score = min(30, price_change * 5)  # Positive momentum
        else:
            momentum_score = max(0, 15 + price_change * 2)  # Some points for stability
        
        # Sector score (0-30 points) - hardcoded for now based on current conditions
        sector_scores = {
            'Industrials': 25,  # Strong global demand
            'Basic Materials': 20,  # Commodity volatility
            'Technology': 22,
            'Consumer Cyclical': 15,
            'Consumer Defensive': 18,
        }
        sector_score = sector_scores.get(company.get('sector', ''), 15)
        
        total = macro_score + momentum_score + sector_score
        
        return {
            'total': min(100, total),
            'macro': macro_score,
            'momentum': momentum_score,
            'sector': sector_score,
        }
    
    def _generate_thesis(
        self, 
        ticker: str, 
        company: Dict, 
        analysis: Dict,
        score: Dict
    ) -> str:
        """Generate investment thesis."""
        parts = []
        
        # Company context
        parts.append(f"{company['name']}: {company['description']}.")
        
        # Macro impacts
        positive_impacts = [i for i in analysis['impacts'] if i['direction'] == 'positive']
        negative_impacts = [i for i in analysis['impacts'] if i['direction'] == 'negative']
        
        if positive_impacts:
            reasons = [i['reason'] for i in positive_impacts[:2]]
            parts.append(f"Positivt: {', '.join(reasons)}.")
        
        if negative_impacts:
            reasons = [i['reason'] for i in negative_impacts[:1]]
            parts.append(f"Risk: {', '.join(reasons)}.")
        
        # Export exposure
        if company.get('export_pct', 0) > 80:
            parts.append(f"Hög exportandel ({company['export_pct']}%) ger valutaexponering.")
        
        return ' '.join(parts)
    
    def _generate_entry_trigger(self, ticker: str, current_price: float, score: Dict) -> str:
        """Generate entry trigger condition."""
        if score['total'] >= 70:
            return f"Köpläge nu vid {current_price:.2f} kr"
        elif score['total'] >= 60:
            support = current_price * 0.97
            return f"Köp vid tekniskt stöd ~{support:.0f} kr eller bekräftad uppgång"
        else:
            support = current_price * 0.95
            return f"Avvakta - potentiell entry vid {support:.0f} kr"
    
    def update_prospects(self) -> int:
        """Update prospects table based on current analysis."""
        opportunities = self.find_opportunities()
        updated = 0
        
        for opp in opportunities[:10]:  # Top 10
            try:
                # Upsert prospect
                self.db.execute("""
                    INSERT INTO prospects (ticker, name, thesis, confidence, entry_trigger, priority, current_price, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (ticker, status) DO UPDATE SET
                        thesis = EXCLUDED.thesis,
                        confidence = EXCLUDED.confidence,
                        entry_trigger = EXCLUDED.entry_trigger,
                        current_price = EXCLUDED.current_price,
                        updated_at = NOW()
                """, (
                    opp['ticker'],
                    opp['name'],
                    opp['thesis'],
                    opp['confidence'],
                    opp['entry_trigger'],
                    opportunities.index(opp) + 1,  # Priority based on rank
                    opp['current_price'],
                ))
                updated += 1
            except Exception as e:
                logger.error(f"Error updating prospect {opp['ticker']}: {e}")
        
        logger.info(f"📝 Updated {updated} prospects")
        return updated
    
    def generate_morning_briefing(self) -> str:
        """Generate morning market briefing."""
        macro = self.get_latest_macro()
        
        briefing = []
        briefing.append(f"📰 Morning Briefing - {datetime.now().strftime('%Y-%m-%d')}")
        briefing.append("=" * 50)
        
        briefing.append("\n🌍 Makro:")
        for symbol, info in MACRO_SYMBOLS.items():
            if symbol in macro:
                data = macro[symbol]
                value = float(data.get('value', 0))
                change = float(data.get('change_pct', 0) or 0)
                arrow = '↑' if change >= 0 else '↓'
                briefing.append(f"  {info['name']}: {value:.2f} {arrow}{abs(change):.1f}%")
        
        briefing.append("\n🎯 Top Prospects:")
        opportunities = self.find_opportunities()[:5]
        for opp in opportunities:
            briefing.append(f"  {opp['ticker']}: {opp['confidence']:.0f}% - {opp['thesis'][:60]}...")
        
        report = "\n".join(briefing)
        logger.info(report)
        return report
    
    def analyze_day(self) -> Dict[str, Any]:
        """End of day analysis."""
        logger.info("🌆 Running end of day analysis...")
        
        # Update prospects
        self.update_prospects()
        
        # Calculate daily stats
        prices = self.get_latest_prices()
        
        gainers = []
        losers = []
        
        for ticker, data in prices.items():
            change = float(data.get('change_pct', 0) or 0)
            if change > 1:
                gainers.append((ticker, change))
            elif change < -1:
                losers.append((ticker, change))
        
        gainers.sort(key=lambda x: x[1], reverse=True)
        losers.sort(key=lambda x: x[1])
        
        return {
            'date': datetime.now().date(),
            'top_gainers': gainers[:5],
            'top_losers': losers[:5],
            'opportunities_found': len(self.find_opportunities()),
        }
