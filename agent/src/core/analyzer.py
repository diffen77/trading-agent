"""
Market Analyzer

Analyzes companies, macro factors, and finds trading opportunities.
Uses company database with input dependencies for impact analysis.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


# Macro symbol display names
MACRO_SYMBOLS = {
    'GC=F': {'name': 'Guld', 'type': 'commodity'},
    'SI=F': {'name': 'Silver', 'type': 'commodity'},
    'HG=F': {'name': 'Koppar', 'type': 'commodity'},
    'BZ=F': {'name': 'Brent Olja', 'type': 'commodity'},
    'NG=F': {'name': 'Naturgas', 'type': 'commodity'},
    'EURSEK=X': {'name': 'EUR/SEK', 'type': 'currency'},
    'USDSEK=X': {'name': 'USD/SEK', 'type': 'currency'},
    '^OMX': {'name': 'OMX Stockholm 30', 'type': 'index'},
}


class MarketAnalyzer:
    """Analyzes market conditions and finds opportunities using company database."""
    
    def __init__(self, db):
        self.db = db
        self._company_cache = {}
        self._deps_cache = {}
        self._load_company_data()
        
    def _load_company_data(self):
        """Load company data and dependencies from database."""
        try:
            companies = self.db.query("SELECT * FROM companies")
            for c in companies:
                self._company_cache[c['ticker']] = c
            
            deps = self.db.query("SELECT * FROM input_dependencies")
            for d in deps:
                ticker = d['ticker']
                if ticker not in self._deps_cache:
                    self._deps_cache[ticker] = []
                self._deps_cache[ticker].append(d)
            
            logger.info(f"📊 Loaded {len(self._company_cache)} companies, {len(deps)} input dependencies from DB")
        except Exception as e:
            logger.error(f"Error loading company data from DB: {e}")
    
    def get_company_info(self, ticker: str) -> Dict[str, Any]:
        """Get company information from database cache."""
        return self._company_cache.get(ticker, {})
    
    def get_company_deps(self, ticker: str) -> List[Dict]:
        """Get input dependencies for a company."""
        return self._deps_cache.get(ticker, [])
    
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
        Uses input_dependencies from database with proper impact strength.
        """
        deps = self.get_company_deps(ticker)
        macro = self.get_latest_macro()
        
        impacts = []
        weighted_score = 0
        total_weight = 0
        
        for dep in deps:
            symbol = dep.get('macro_symbol')
            if not symbol or symbol not in macro:
                continue
            
            data = macro[symbol]
            change_pct = float(data.get('change_pct', 0) or 0)
            strength = float(dep.get('impact_strength', 0.5))
            direction = dep.get('impact_direction', 'cost')
            
            # Calculate impact score
            if direction == 'cost':
                # Rising costs = negative for company
                score = -change_pct / 10
                dir_label = 'positive' if change_pct < 0 else 'negative'
                reason = f"{dep['input_name']} {'ner' if change_pct < 0 else 'upp'} {abs(change_pct):.1f}% — {dep.get('description', '')}"
            else:  # revenue
                # Rising revenue drivers = positive
                score = change_pct / 10
                dir_label = 'positive' if change_pct > 0 else 'negative'
                reason = f"{dep['input_name']} {change_pct:+.1f}% — {dep.get('description', '')}"
            
            # Weight by impact strength
            weighted = max(-1, min(1, score)) * strength
            weighted_score += weighted
            total_weight += strength
            
            impacts.append({
                'factor': dep['input_name'],
                'direction': dir_label,
                'score': max(-1, min(1, score)),
                'strength': strength,
                'weighted_score': weighted,
                'reason': reason,
                'change_pct': change_pct,
                'macro_symbol': symbol,
            })
        
        # Normalized sentiment
        net_sentiment = weighted_score / total_weight if total_weight > 0 else 0
        
        return {
            'ticker': ticker,
            'dependencies': len(deps),
            'impacts': impacts,
            'net_sentiment': max(-1, min(1, net_sentiment)),
        }
    
    def find_opportunities(self) -> List[Dict[str, Any]]:
        """
        Find trading opportunities based on:
        1. Macro changes weighted by input dependency strength
        2. Price momentum
        3. Sector conditions
        """
        opportunities = []
        prices = self.get_latest_prices()
        
        logger.info("🔍 Scanning for opportunities...")
        
        for ticker, company in self._company_cache.items():
            # Skip if no price data
            if ticker not in prices:
                continue
            
            price_data = prices[ticker]
            current_price = float(price_data.get('close', 0))
            
            if current_price <= 0:
                continue
            
            # Analyze macro impact using DB dependencies
            analysis = self.analyze_macro_impact(ticker)
            
            # Get technical signals
            tech = self.get_technical_signals(ticker)
            
            # Calculate opportunity score
            score = self._calculate_opportunity_score(
                ticker, company, analysis, price_data, tech
            )
            
            if score['total'] >= 50:
                opp = {
                    'ticker': ticker,
                    'name': company.get('name', ticker),
                    'sector': company.get('sector', ''),
                    'current_price': current_price,
                    'confidence': score['total'],
                    'thesis': self._generate_thesis(ticker, company, analysis, score),
                    'entry_trigger': self._generate_entry_trigger(ticker, current_price, score),
                    'macro_sentiment': analysis['net_sentiment'],
                    'impacts': analysis['impacts'],
                    'score_breakdown': score,
                    'pattern': tech.get('pattern') if tech else None,
                    'pattern_signal': tech.get('pattern_signal') if tech else None,
                }
                opportunities.append(opp)
                logger.info(f"  📊 {ticker}: {score['total']:.0f}% confidence")
        
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)
        logger.info(f"✅ Found {len(opportunities)} opportunities")
        return opportunities
    
    def _calculate_opportunity_score(
        self, ticker: str, company: Dict, analysis: Dict, price_data: Dict,
        tech: Optional[Dict] = None
    ) -> Dict[str, float]:
        """Calculate composite opportunity score with weighted dependencies and technicals."""
        
        # Macro score (0-35 points) — weighted by dependency strength
        macro_score = (analysis['net_sentiment'] + 1) * 17.5  # -1..1 → 0..35
        
        # Bonus for strong impacts (many aligned factors)
        strong_positive = sum(1 for i in analysis['impacts'] 
                           if i['direction'] == 'positive' and i['strength'] >= 0.6)
        macro_score = min(35, macro_score + strong_positive * 3)
        
        # Momentum score (0-25 points)
        price_change = float(price_data.get('change_pct', 0) or 0)
        if price_change > 0:
            momentum_score = min(25, price_change * 5)
        else:
            momentum_score = max(0, 12 + price_change * 2)
        
        # Sector score (0-20 points)
        sector = company.get('sector', '')
        sector_scores = {
            'Industrials': 18,
            'Basic Materials': 15,
            'Technology': 17,
            'Consumer Cyclical': 12,
            'Consumer Defensive': 14,
            'Financial Services': 15,
            'Healthcare': 17,
            'Communication Services': 12,
            'Real Estate': 10,
        }
        sector_score = sector_scores.get(sector, 12)
        
        # Technical/pattern score (0-20 points)
        technical_score = 10  # Neutral baseline
        pattern_name = None
        if tech:
            # RSI contribution
            rsi = float(tech.get('rsi') or 50)
            if 40 <= rsi <= 60:
                technical_score += 2  # Neutral RSI = slight positive (room to run)
            elif rsi < 35:
                technical_score += 5  # Oversold = opportunity
            elif rsi > 65:
                technical_score -= 3  # Overbought = caution
            
            # Momentum score from TA
            ta_momentum = float(tech.get('momentum_score') or 0)
            technical_score += max(-5, min(5, ta_momentum / 20))
            
            # Pattern bonus/penalty
            pattern_name = tech.get('pattern')
            pattern_signal = tech.get('pattern_signal')
            if pattern_signal == 'bullish':
                pattern_bonus = {
                    'golden_cross': 8, 'breakout': 7, 'rsi_bull_divergence': 6,
                    'volume_spike_up': 5, 'support_bounce': 4,
                }.get(pattern_name, 4)
                technical_score += pattern_bonus
            elif pattern_signal == 'bearish':
                pattern_penalty = {
                    'death_cross': -8, 'breakdown': -7, 'rsi_bear_divergence': -6,
                    'volume_spike_down': -5, 'resistance_rejection': -4,
                }.get(pattern_name, -4)
                technical_score += pattern_penalty
        
        technical_score = max(0, min(20, technical_score))
        
        total = macro_score + momentum_score + sector_score + technical_score
        
        return {
            'total': min(100, total),
            'macro': macro_score,
            'momentum': momentum_score,
            'sector': sector_score,
            'technical': technical_score,
            'pattern': pattern_name,
        }
    
    def _generate_thesis(self, ticker: str, company: Dict, analysis: Dict, score: Dict) -> str:
        """Generate investment thesis from DB data."""
        parts = []
        
        name = company.get('name', ticker)
        desc = company.get('description', '')
        if desc:
            parts.append(f"{name}: {desc}.")
        else:
            parts.append(f"{name}.")
        
        positive_impacts = [i for i in analysis['impacts'] if i['direction'] == 'positive']
        negative_impacts = [i for i in analysis['impacts'] if i['direction'] == 'negative']
        
        if positive_impacts:
            # Sort by weighted score
            positive_impacts.sort(key=lambda x: abs(x['weighted_score']), reverse=True)
            reasons = [i['reason'] for i in positive_impacts[:2]]
            parts.append(f"Positivt: {'; '.join(reasons)}.")
        
        if negative_impacts:
            negative_impacts.sort(key=lambda x: abs(x['weighted_score']), reverse=True)
            reasons = [i['reason'] for i in negative_impacts[:1]]
            parts.append(f"Risk: {'; '.join(reasons)}.")
        
        # Pattern info
        pattern = score.get('pattern')
        if pattern:
            pattern_labels = {
                'golden_cross': 'Golden Cross (SMA20>SMA50)',
                'death_cross': 'Death Cross (SMA20<SMA50)',
                'breakout': 'Breakout över 20d-high',
                'breakdown': 'Breakdown under 20d-low',
                'rsi_bull_divergence': 'RSI bullish divergens',
                'rsi_bear_divergence': 'RSI bearish divergens',
                'volume_spike_up': 'Volymspike uppåt',
                'volume_spike_down': 'Volymspike nedåt',
                'support_bounce': 'Studs från stöd',
                'resistance_rejection': 'Avvisad vid motstånd',
            }
            parts.append(f"Tekniskt: {pattern_labels.get(pattern, pattern)}.")
        
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
        
        for opp in opportunities[:10]:
            try:
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
                    opportunities.index(opp) + 1,
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
        self.update_prospects()
        
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
    
    def get_sector_overview(self) -> Dict[str, Dict]:
        """Get overview by sector from database."""
        try:
            sectors = self.db.query("""
                SELECT sector, COUNT(*) as count, 
                       array_agg(ticker) as tickers
                FROM companies
                WHERE sector IS NOT NULL
                GROUP BY sector
                ORDER BY count DESC
            """)
            return {s['sector']: s for s in sectors}
        except Exception as e:
            logger.error(f"Error getting sector overview: {e}")
            return {}
    
    def run_technical_analysis(self) -> List[Dict[str, Any]]:
        """
        Calculate RSI(14), SMA20, SMA50, volume ratio, momentum score,
        and pattern recognition for all tracked companies.
        Saves to technical_signals table. Returns list of alerts.
        """
        logger.info("📈 Running technical analysis...")
        alerts = []
        
        for ticker in self._company_cache:
            try:
                # Get last 60 days of price data
                rows = self.db.query("""
                    SELECT date, open, high, low, close, volume FROM prices
                    WHERE ticker = :ticker
                    ORDER BY date DESC
                    LIMIT 60
                """, {'ticker': ticker})
                
                if len(rows) < 20:
                    continue
                
                # Reverse to chronological order
                rows = list(reversed(rows))
                closes = [float(r['close']) for r in rows]
                highs = [float(r['high'] or r['close']) for r in rows]
                lows = [float(r['low'] or r['close']) for r in rows]
                volumes = [int(r['volume'] or 0) for r in rows]
                latest_date = rows[-1]['date']
                
                # RSI (14)
                rsi = self._calc_rsi(closes, 14)
                
                # SMA 20 and SMA 50
                sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
                sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
                
                # Previous day SMAs (for crossover detection)
                prev_sma20 = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else None
                prev_sma50 = sum(closes[-51:-1]) / 50 if len(closes) >= 51 else None
                
                # Volume ratio vs 20-day average
                if len(volumes) >= 20:
                    avg_vol = sum(volumes[-20:]) / 20
                    volume_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
                else:
                    volume_ratio = 1.0
                
                # Momentum score (-100 to +100)
                momentum = self._calc_momentum(closes, rsi, sma20, sma50, volume_ratio)
                
                # Pattern recognition
                pattern, pattern_signal = self._detect_patterns(
                    closes, highs, lows, volumes, rsi,
                    sma20, sma50, prev_sma20, prev_sma50, volume_ratio
                )
                
                # Save to DB
                self.db.execute("""
                    INSERT INTO technical_signals (ticker, date, rsi, sma20, sma50, volume_ratio, momentum_score, pattern, pattern_signal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        rsi = EXCLUDED.rsi,
                        sma20 = EXCLUDED.sma20,
                        sma50 = EXCLUDED.sma50,
                        volume_ratio = EXCLUDED.volume_ratio,
                        momentum_score = EXCLUDED.momentum_score,
                        pattern = EXCLUDED.pattern,
                        pattern_signal = EXCLUDED.pattern_signal
                """, (ticker, latest_date, rsi, sma20, sma50, volume_ratio, momentum, pattern, pattern_signal))
                
                # Generate alerts
                if pattern:
                    alerts.append({
                        'ticker': ticker, 'type': f'pattern:{pattern}',
                        'signal': pattern_signal, 'rsi': rsi or 50, 'momentum': momentum
                    })
                    emoji = '🟢' if pattern_signal == 'bullish' else '🔴' if pattern_signal == 'bearish' else '⚪'
                    logger.warning(f"{emoji} {ticker} mönster: {pattern} ({pattern_signal})")
                
                if rsi is not None:
                    if rsi > 70:
                        alerts.append({'ticker': ticker, 'type': 'overbought', 'signal': 'bearish', 'rsi': rsi, 'momentum': momentum})
                        logger.warning(f"⚠️ {ticker} RSI={rsi:.1f} ÖVERKÖPT")
                    elif rsi < 30:
                        alerts.append({'ticker': ticker, 'type': 'oversold', 'signal': 'bullish', 'rsi': rsi, 'momentum': momentum})
                        logger.warning(f"⚠️ {ticker} RSI={rsi:.1f} ÖVERSÅLT")
                
            except Exception as e:
                logger.error(f"Technical analysis error for {ticker}: {e}")
        
        logger.info(f"📈 Technical analysis complete. {len(alerts)} alerts.")
        return alerts
    
    def _detect_patterns(self, closes, highs, lows, volumes, rsi,
                         sma20, sma50, prev_sma20, prev_sma50, volume_ratio) -> tuple:
        """
        Detect chart patterns. Returns (pattern_name, signal) or (None, None).
        Only returns the strongest/most significant pattern found.
        """
        patterns = []  # (name, signal, priority)
        
        current = closes[-1]
        
        # 1. Golden Cross / Death Cross (SMA20 crosses SMA50)
        if sma20 is not None and sma50 is not None and prev_sma20 is not None and prev_sma50 is not None:
            if prev_sma20 <= prev_sma50 and sma20 > sma50:
                patterns.append(('golden_cross', 'bullish', 10))
            elif prev_sma20 >= prev_sma50 and sma20 < sma50:
                patterns.append(('death_cross', 'bearish', 10))
        
        # 2. RSI Bullish Divergence (price makes lower low, RSI makes higher low)
        if rsi is not None and len(closes) >= 30:
            rsi_divergence = self._check_rsi_divergence(closes, 14)
            if rsi_divergence == 'bullish':
                patterns.append(('rsi_bull_divergence', 'bullish', 8))
            elif rsi_divergence == 'bearish':
                patterns.append(('rsi_bear_divergence', 'bearish', 8))
        
        # 3. Breakout: price breaks 20-day high with volume confirmation
        if len(highs) >= 21:
            high_20d = max(highs[-21:-1])  # Previous 20 days high (excluding today)
            low_20d = min(lows[-21:-1])
            
            if current > high_20d and volume_ratio > 1.3:
                patterns.append(('breakout', 'bullish', 9))
            elif current < low_20d and volume_ratio > 1.3:
                patterns.append(('breakdown', 'bearish', 9))
        
        # 4. Volume spike (>2x normal) confirming direction
        if volume_ratio > 2.0 and len(closes) >= 2:
            price_change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100
            if price_change_pct > 1.0:
                patterns.append(('volume_spike_up', 'bullish', 7))
            elif price_change_pct < -1.0:
                patterns.append(('volume_spike_down', 'bearish', 7))
        
        # 5. Support/Resistance bounce
        if len(highs) >= 30:
            support, resistance = self._calc_support_resistance(highs[-30:], lows[-30:])
            if support is not None and resistance is not None:
                range_size = resistance - support
                if range_size > 0:
                    # Near support (within 2% of support)
                    if current <= support * 1.02 and closes[-1] > closes[-2]:
                        patterns.append(('support_bounce', 'bullish', 6))
                    # Near resistance (within 2% of resistance)
                    elif current >= resistance * 0.98 and closes[-1] < closes[-2]:
                        patterns.append(('resistance_rejection', 'bearish', 6))
        
        if not patterns:
            return (None, None)
        
        # Return highest priority pattern
        patterns.sort(key=lambda x: x[2], reverse=True)
        return (patterns[0][0], patterns[0][1])
    
    def _check_rsi_divergence(self, closes: List[float], period: int = 14) -> Optional[str]:
        """
        Check for RSI divergence over last 20 bars.
        Bullish: price lower low, RSI higher low.
        Bearish: price higher high, RSI lower high.
        """
        if len(closes) < period + 10:
            return None
        
        # Calculate RSI at two points: ~10 bars ago and now
        rsi_now = self._calc_rsi(closes, period)
        rsi_prev = self._calc_rsi(closes[:-10], period)
        
        if rsi_now is None or rsi_prev is None:
            return None
        
        price_now = closes[-1]
        price_prev = min(closes[-15:-5])  # Low around 10 bars ago
        price_recent_low = min(closes[-5:])
        price_prev_high = max(closes[-15:-5])
        price_recent_high = max(closes[-5:])
        
        # Bullish divergence: price lower low but RSI higher low
        if price_recent_low < price_prev and rsi_now > rsi_prev and rsi_now < 45:
            return 'bullish'
        
        # Bearish divergence: price higher high but RSI lower high
        if price_recent_high > price_prev_high and rsi_now < rsi_prev and rsi_now > 55:
            return 'bearish'
        
        return None
    
    def _calc_support_resistance(self, highs: List[float], lows: List[float]) -> tuple:
        """
        Simple support/resistance from 30-day price data.
        Support = area where lows cluster, Resistance = area where highs cluster.
        Uses percentile approach for robustness.
        """
        if len(lows) < 10 or len(highs) < 10:
            return (None, None)
        
        # Support: 10th percentile of lows
        sorted_lows = sorted(lows)
        support_idx = max(0, len(sorted_lows) // 10)
        support = sorted_lows[support_idx]
        
        # Resistance: 90th percentile of highs
        sorted_highs = sorted(highs)
        resistance_idx = min(len(sorted_highs) - 1, len(sorted_highs) * 9 // 10)
        resistance = sorted_highs[resistance_idx]
        
        return (support, resistance)
    
    def _calc_rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        """Calculate RSI."""
        if len(closes) < period + 1:
            return None
        
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        recent = deltas[-(period):]
        
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _calc_momentum(self, closes: List[float], rsi: Optional[float],
                       sma20: Optional[float], sma50: Optional[float],
                       volume_ratio: float) -> float:
        """Calculate momentum score from -100 to +100."""
        score = 0
        components = 0
        
        # RSI component: 30-70 neutral, outside = signal
        if rsi is not None:
            if rsi > 50:
                score += min(30, (rsi - 50) * 1.5)
            else:
                score -= min(30, (50 - rsi) * 1.5)
            components += 1
        
        # Price vs SMA20: above = bullish
        current = closes[-1]
        if sma20 is not None and sma20 > 0:
            pct_above = ((current - sma20) / sma20) * 100
            score += max(-30, min(30, pct_above * 5))
            components += 1
        
        # SMA20 vs SMA50: golden/death cross signal
        if sma20 is not None and sma50 is not None and sma50 > 0:
            cross_pct = ((sma20 - sma50) / sma50) * 100
            score += max(-20, min(20, cross_pct * 5))
            components += 1
        
        # Volume: high volume confirms trend
        if volume_ratio > 1.5:
            # Amplify current direction
            direction = 1 if score > 0 else -1
            score += direction * min(20, (volume_ratio - 1) * 10)
            components += 1
        
        return max(-100, min(100, score))
    
    def get_technical_signals(self, ticker: str) -> Optional[Dict]:
        """Get latest technical signals for a ticker."""
        rows = self.db.query("""
            SELECT * FROM technical_signals
            WHERE ticker = :ticker
            ORDER BY date DESC LIMIT 1
        """, {'ticker': ticker})
        return rows[0] if rows else None
    
    def get_rsi_alerts(self) -> List[Dict]:
        """Get current RSI alerts for portfolio positions."""
        return self.db.query("""
            SELECT ts.ticker, ts.date, ts.rsi, ts.momentum_score,
                   p.shares, p.avg_price
            FROM technical_signals ts
            JOIN portfolio p ON p.ticker = ts.ticker
            WHERE ts.date = (SELECT MAX(date) FROM technical_signals WHERE ticker = ts.ticker)
            AND (ts.rsi > 70 OR ts.rsi < 30)
            AND p.shares > 0
        """)
    
    def get_macro_impact_report(self, macro_symbol: str) -> List[Dict]:
        """Show which companies are affected by a specific macro factor."""
        try:
            results = self.db.query("""
                SELECT d.ticker, c.name, c.sector, d.input_name,
                       d.impact_direction, d.impact_strength, d.description
                FROM input_dependencies d
                JOIN companies c ON c.ticker = d.ticker
                WHERE d.macro_symbol = :symbol
                ORDER BY d.impact_strength DESC
            """, {'symbol': macro_symbol})
            return results
        except Exception as e:
            logger.error(f"Error getting macro impact report: {e}")
            return []
