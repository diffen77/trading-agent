"""
AI Brain - Autonomous Trading Decision Engine

Uses LLM (Ollama/OpenAI-compatible or Anthropic) to analyze market data and make trading decisions.
All decisions are logged to ai_decisions table for audit trail.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Try OpenAI client first (works with Ollama), fall back to Anthropic
HAS_OPENAI = False
HAS_ANTHROPIC = False

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    pass

if not HAS_OPENAI:
    try:
        import anthropic
        HAS_ANTHROPIC = True
    except ImportError:
        pass

if not HAS_OPENAI and not HAS_ANTHROPIC:
    logger.warning("Neither openai nor anthropic package installed - brain disabled")


SYSTEM_PROMPT = """Du är en autonom trading-agent för svenska aktier på Stockholmsbörsen.
Du förvaltar ett paper trading-konto med mål att dubbla 20 000 kr på 6 månader.

## AGGRESSIV STRATEGI: Momentum + Rapport Swing Trading

### ENTRY-REGLER (alla måste uppfyllas för KÖP):
- Pris > SMA20 (upptrend)
- Positiv sentiment ELLER rapport-katalysator ELLER stark makro-medvind
- Confidence > 55%
- **TVINGANDE REGEL: Om cash > 20% av total value → MÅSTE ge BUY-beslut**

### EXIT-REGLER:
- Stop-loss: -5% från entry → SÄLJ
- Take-profit: +10% → SÄLJ
- Trailing stop: vid +5% flytta mental SL till +2%
- Tidsstopp: efter 10 dagar, om under +3% → SÄLJ

### RISKHANTERING:
- Max 5 samtida positioner
- Max 25% av portföljen i en position
- INGA KÖP om OMXS30 är ner mer än -2.5% idag (risk-off)
- Sprid över sektorer — max 2 positioner i samma sektor

### AGGRESSIV MENTALITET:
Du FÖRVALTAR aktivt. Cash = dåligt. Varje dag pengar sitter stilla förlorar vi mot målet.
Bättre att vara i marknaden än att missa momentum. RSI-restriktioner har tagits bort.

### OUTPUT-FORMAT (STRIKT JSON):
Svara ENBART med valid JSON, inget annat:
{
  "decisions": [
    {
      "action": "BUY" eller "SELL" eller "HOLD",
      "ticker": "TICKER", 
      "reason": "Kort motivering",
      "confidence": 0-100,
      "position_size_pct": 5-25
    }
  ],
  "market_outlook": "bullish" eller "neutral" eller "bearish",
  "analysis_summary": "2-3 meningar om marknadens tillstånd"
}

Om inget ska göras, returnera tom decisions-lista.
Var AGGRESSIV men DISCIPLINERAD. Bättre att vara i marknaden än att sitta passiv."""


class TradingBrain:
    """AI-powered trading decision engine. Supports Ollama (free) or Anthropic."""

    # Ollama model (free, local) - 14B for speed, 32B too slow with large context
    OLLAMA_MODEL = "qwen2.5:14b-instruct-q4_K_M"
    ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

    def __init__(self, db):
        self.db = db
        self.backend = None  # 'ollama' or 'anthropic'
        
        # Try Ollama first (free, local)
        ollama_url = os.getenv("OLLAMA_URL", "http://192.168.99.176:11434")
        if HAS_OPENAI:
            try:
                self.client = OpenAI(
                    base_url=f"{ollama_url}/v1",
                    api_key="ollama",  # Ollama doesn't need a real key
                )
                self.backend = 'ollama'
                self.model = self.OLLAMA_MODEL
                logger.info(f"🧠 Brain using Ollama ({self.model}) at {ollama_url}")
            except Exception as e:
                logger.warning(f"Ollama init failed: {e}")
        
        # Fall back to Anthropic
        if not self.backend and HAS_ANTHROPIC:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                self.client = anthropic.Anthropic(api_key=api_key)
                self.backend = 'anthropic'
                self.model = self.ANTHROPIC_MODEL
                logger.info(f"🧠 Brain using Anthropic ({self.model})")
            else:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
        
        if not self.backend:
            raise RuntimeError("No LLM backend available (install openai or anthropic package)")

    # ------------------------------------------------------------------
    # Context gathering
    # ------------------------------------------------------------------

    def _get_portfolio_context(self) -> str:
        """Current portfolio state."""
        try:
            balance = self.db.get_balance()
            portfolio = self.db.get_portfolio()
            lines = [
                f"Cash: {balance['cash']:.0f} SEK",
                f"Total value: {balance['total_value']:.0f} SEK",
                f"P&L vs 20k start: {balance['total_value'] - 20000:+.0f} SEK",
            ]
            if not portfolio.empty:
                lines.append("\nÖppna positioner:")
                for _, p in portfolio.iterrows():
                    shares = float(p.get('shares', 0))
                    if shares <= 0:
                        continue
                    avg = float(p.get('avg_price', 0))
                    # get current price
                    prices = self.db.get_latest_prices([p['ticker']])
                    cur = float(prices.iloc[0]['close']) if not prices.empty else avg
                    pnl_pct = ((cur / avg) - 1) * 100 if avg else 0
                    lines.append(
                        f"  {p['ticker']}: {shares:.1f} st @ {avg:.2f}, "
                        f"nu {cur:.2f} ({pnl_pct:+.1f}%)"
                    )
            else:
                lines.append("Inga öppna positioner.")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Portfolio context error: {e}")
            return "Portföljdata ej tillgänglig."

    def _get_macro_context(self) -> str:
        """Latest macro data."""
        try:
            rows = self.db.query("""
                SELECT DISTINCT ON (symbol) symbol, value, change_pct, date
                FROM macro ORDER BY symbol, date DESC
            """)
            if not rows:
                return "Makrodata ej tillgänglig."
            names = {
                '^OMX': 'OMXS30', 'EURSEK=X': 'EUR/SEK', 'USDSEK=X': 'USD/SEK',
                'BZ=F': 'Brent Olja', 'GC=F': 'Guld', 'HG=F': 'Koppar',
                'CL=F': 'WTI Olja', 'SI=F': 'Silver', 'NG=F': 'Naturgas',
                '^OMXSPI': 'OMXS All', '^GSPC': 'S&P 500', 'EURUSD=X': 'EUR/USD',
            }
            lines = []
            for r in rows:
                name = names.get(r['symbol'], r['symbol'])
                val = float(r.get('value', 0))
                chg = float(r.get('change_pct', 0) or 0)
                lines.append(f"{name}: {val:.2f} ({chg:+.1f}%)")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Macro context error: {e}")
            return "Makrodata ej tillgänglig."

    def _get_omxs30_change(self) -> float:
        """Get today's OMXS30 change percent."""
        try:
            rows = self.db.query("""
                SELECT change_pct FROM macro
                WHERE symbol = '^OMX' ORDER BY date DESC LIMIT 1
            """)
            return float(rows[0]['change_pct']) if rows else 0
        except:
            return 0

    def _get_news_context(self) -> str:
        """Recent news (24h)."""
        try:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            rows = self.db.query("""
                SELECT ticker, headline, sentiment, sentiment_score
                FROM news WHERE published_at >= :cutoff
                ORDER BY published_at DESC LIMIT 20
            """, {'cutoff': cutoff})
            if not rows:
                return "Inga nyheter senaste 24h."
            lines = []
            for r in rows:
                ticker = f"[{r['ticker']}] " if r.get('ticker') else ""
                sent = r.get('sentiment', 'neutral')
                lines.append(f"{ticker}{r['headline'][:80]} ({sent})")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"News context error: {e}")
            return "Nyhetsdata ej tillgänglig."

    def _get_technical_context(self) -> str:
        """Latest technical signals for all companies."""
        try:
            rows = self.db.query("""
                SELECT DISTINCT ON (ticker) ticker, date, rsi, sma20, sma50,
                       volume_ratio, momentum_score, pattern, pattern_signal
                FROM technical_signals
                ORDER BY ticker, date DESC
            """)
            if not rows:
                return "Tekniska signaler ej tillgängliga."
            lines = []
            for r in rows:
                rsi = f"RSI={float(r['rsi']):.0f}" if r.get('rsi') else "RSI=?"
                sma20 = f"SMA20={float(r['sma20']):.1f}" if r.get('sma20') else ""
                sma50 = f"SMA50={float(r['sma50']):.1f}" if r.get('sma50') else ""
                mom = f"Mom={float(r['momentum_score']):.0f}" if r.get('momentum_score') else ""
                pat = f"Mönster={r['pattern']}({r['pattern_signal']})" if r.get('pattern') else ""
                parts = [x for x in [rsi, sma20, sma50, mom, pat] if x]
                lines.append(f"{r['ticker']}: {', '.join(parts)}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Technical context error: {e}")
            return "Tekniska signaler ej tillgängliga."

    def _get_prospects_context(self) -> str:
        """Current prospects with confidence."""
        try:
            rows = self.db.query("""
                SELECT ticker, name, thesis, confidence, entry_trigger, current_price
                FROM prospects WHERE status = 'active'
                ORDER BY confidence DESC LIMIT 10
            """)
            if not rows:
                return "Inga aktiva prospects."
            lines = []
            for r in rows:
                lines.append(
                    f"{r['ticker']} ({r.get('name', '')}): "
                    f"{float(r.get('confidence', 0)):.0f}% - {r.get('thesis', '')[:80]}"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Prospects context error: {e}")
            return "Prospects ej tillgängliga."

    def _get_reports_context(self) -> str:
        """Upcoming reports within 5 days."""
        try:
            from datetime import date
            cutoff = date.today() + timedelta(days=5)
            rows = self.db.query("""
                SELECT rc.ticker, c.name, rc.report_date, rc.report_type
                FROM report_calendar rc
                LEFT JOIN companies c ON c.ticker = rc.ticker
                WHERE rc.report_date BETWEEN CURRENT_DATE AND :cutoff
                ORDER BY rc.report_date
            """, {'cutoff': cutoff})
            if not rows:
                return "Inga rapporter inom 5 dagar."
            lines = []
            for r in rows:
                lines.append(
                    f"{r['ticker']} ({r.get('name', '')}): "
                    f"{r['report_type']} den {r['report_date']}"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Reports context error: {e}")
            return "Rapportkalender ej tillgänglig."

    def _get_prices_context(self) -> str:
        """Current prices for all stocks (latest)."""
        try:
            rows = self.db.query("""
                SELECT DISTINCT ON (p.ticker) p.ticker, p.close, p.date,
                       c.name, c.sector
                FROM prices p
                LEFT JOIN companies c ON c.ticker = p.ticker
                ORDER BY p.ticker, p.date DESC
            """)
            if not rows:
                return "Prisdata ej tillgänglig."
            lines = []
            for r in rows:
                name = r.get('name', '')
                sector = f"({r.get('sector', '')})" if r.get('sector') else ""
                lines.append(f"{r['ticker']}: {float(r['close']):.2f} SEK {name} {sector}")
            return "\n".join(lines)
        except Exception as e:
            return "Prisdata ej tillgänglig."

    # ------------------------------------------------------------------
    # Core decision making
    # ------------------------------------------------------------------

    def build_context(self, deep: bool = False) -> str:
        """Build full context string for Claude."""
        sections = [
            ("PORTFÖLJ", self._get_portfolio_context()),
            ("MAKRO", self._get_macro_context()),
            ("NYHETER (24h)", self._get_news_context()),
            ("TEKNISKA SIGNALER", self._get_technical_context()),
            ("PROSPECTS", self._get_prospects_context()),
            ("RAPPORTER (5 dagar)", self._get_reports_context()),
        ]
        if deep:
            sections.append(("ALLA PRISER", self._get_prices_context()))

        parts = []
        for title, content in sections:
            parts.append(f"## {title}\n{content}")
        return "\n\n".join(parts)

    def make_decisions(self, deep: bool = False) -> Dict[str, Any]:
        """
        Call Claude Sonnet with full market context and get trading decisions.
        Returns parsed JSON response.
        """
        context = self.build_context(deep=deep)
        now = datetime.utcnow()
        user_msg = (
            f"Datum: {now.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
            f"{context}\n\n"
            "Analysera all data och ge dina trading-beslut som JSON."
        )

        logger.info(f"🧠 Calling {self.backend} ({self.model}) ({'deep' if deep else 'standard'} analysis)...")

        try:
            raw_text, prompt_tokens, response_tokens = self._call_llm(
                system=SYSTEM_PROMPT,
                user_msg=user_msg,
                max_tokens=2000,
            )

            logger.info(
                f"🧠 LLM response: {prompt_tokens} in / {response_tokens} out tokens"
            )

            # Parse JSON from response (handle markdown code blocks)
            json_str = raw_text.strip()
            if json_str.startswith("```"):
                # Remove ```json ... ```
                lines = json_str.split("\n")
                json_str = "\n".join(
                    l for l in lines if not l.strip().startswith("```")
                )

            decisions = json.loads(json_str)

            # Log to ai_decisions table
            self._log_decision(
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                decisions_json=decisions,
                market_context=context,
                raw_response=raw_text,
            )

            return decisions

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.error(f"Raw response: {raw_text[:500]}")
            self._log_decision(
                prompt_tokens=getattr(response, 'usage', None) and response.usage.input_tokens or 0,
                response_tokens=getattr(response, 'usage', None) and response.usage.output_tokens or 0,
                decisions_json={"error": str(e), "raw": raw_text[:1000]},
                market_context=context,
                raw_response=raw_text,
            )
            return {"decisions": [], "market_outlook": "neutral", "analysis_summary": f"Parse error: {e}"}

        except Exception as e:
            logger.error(f"Claude API error: {e}", exc_info=True)
            return {"decisions": [], "market_outlook": "neutral", "analysis_summary": f"API error: {e}"}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_decisions(self, decisions: Dict[str, Any]) -> List[Dict]:
        """
        Double-check decisions against hard rules.
        Returns list of validated, executable decisions.
        """
        validated = []
        raw = decisions.get("decisions", [])

        if not raw:
            logger.info("🧠 No decisions from Claude.")
            return validated

        # Get current state
        try:
            balance = self.db.get_balance()
            portfolio = self.db.get_portfolio()
            cash = balance['cash']
            total_value = balance['total_value']
        except Exception as e:
            logger.error(f"Cannot validate - DB error: {e}")
            return validated

        current_tickers = set()
        current_sectors = {}
        if not portfolio.empty:
            for _, p in portfolio.iterrows():
                if float(p.get('shares', 0)) > 0:
                    current_tickers.add(p['ticker'])

        num_positions = len(current_tickers)
        omxs30_change = self._get_omxs30_change()

        for d in raw:
            action = d.get("action", "").upper()
            ticker = d.get("ticker", "")
            confidence = d.get("confidence", 0)
            size_pct = d.get("position_size_pct", 15)

            if action == "HOLD":
                continue

            # Rule: confidence > 55%
            if confidence < 55:
                logger.info(f"🚫 {ticker} rejected: confidence {confidence}% < 55%")
                continue

            if action == "BUY":
                # Rule: max 5 positions
                if num_positions >= 5:
                    logger.info(f"🚫 {ticker} rejected: max 5 positions reached")
                    continue

                # Rule: no buys if OMXS30 down > 2.5%
                if omxs30_change < -2.5:
                    logger.info(f"🚫 {ticker} rejected: OMXS30 {omxs30_change:.1f}% (risk-off)")
                    continue

                # Rule: don't buy what we already own
                if ticker in current_tickers:
                    logger.info(f"🚫 {ticker} rejected: already in portfolio")
                    continue

                # Rule: max 25% position size
                size_pct = min(size_pct, 25)
                position_value = total_value * size_pct / 100

                # Check cash
                if position_value > cash:
                    position_value = cash * 0.9  # Use 90% of remaining cash
                    if position_value < 500:
                        logger.info(f"🚫 {ticker} rejected: insufficient cash")
                        continue

                # Validate technical signals (price > SMA20, RSI warning only)
                tech = self.db.query("""
                    SELECT rsi, sma20 FROM technical_signals
                    WHERE ticker = :ticker ORDER BY date DESC LIMIT 1
                """, {'ticker': ticker})

                price_rows = self.db.query("""
                    SELECT close FROM prices
                    WHERE ticker = :ticker ORDER BY date DESC LIMIT 1
                """, {'ticker': ticker})

                if tech and tech[0].get('rsi'):
                    rsi = float(tech[0]['rsi'])
                    if rsi > 65:
                        logger.info(f"⚠️ {ticker} warning: RSI {rsi:.0f} > 65 (overköpt men tillåtet)")
                        # Don't reject - just warn

                if tech and tech[0].get('sma20') and price_rows:
                    sma20 = float(tech[0]['sma20'])
                    price = float(price_rows[0]['close'])
                    if price < sma20:
                        logger.info(f"⚠️ {ticker} warning: price {price:.2f} < SMA20 {sma20:.2f}")
                        # Don't reject, just warn - Claude may have good reason

                d['position_value'] = position_value
                d['position_size_pct'] = size_pct
                validated.append(d)
                num_positions += 1

            elif action == "SELL":
                if ticker not in current_tickers:
                    logger.info(f"🚫 {ticker} SELL rejected: not in portfolio")
                    continue
                validated.append(d)

        # Check cash ratio - warn if too much cash sitting idle
        cash_ratio = (cash / total_value) * 100 if total_value > 0 else 0
        if cash_ratio > 20:
            logger.warning(f"⚠️ FOR MYCKET CASH ({cash_ratio:.0f}%) - brain måste deploya mer aggressivt")

        logger.info(f"🧠 Validated {len(validated)}/{len(raw)} decisions")
        return validated

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_decisions(self, decisions: List[Dict], trader) -> List[Dict]:
        """Execute validated decisions via PaperTrader."""
        executed = []

        for d in decisions:
            action = d["action"].upper()
            ticker = d["ticker"]

            try:
                if action == "BUY":
                    opp = {
                        'ticker': ticker,
                        'action': 'BUY',
                        'confidence': d.get('confidence', 70),
                        'reasoning': d.get('reason', 'AI decision'),
                        'thesis': d.get('reason', 'AI decision'),
                        'hypothesis': f"AI: {d.get('reason', '')}. Target +8%, SL -5%.",
                        'position_size': d.get('position_value', 3000),
                    }
                    if trader.execute_trade(opp):
                        executed.append(d)
                        logger.info(f"✅ BUY {ticker} executed ({d.get('confidence')}%)")

                elif action == "SELL":
                    # Get current position
                    portfolio = self.db.get_portfolio()
                    pos = portfolio[portfolio['ticker'] == ticker]
                    if pos.empty:
                        continue
                    shares = float(pos.iloc[0]['shares'])
                    prices = self.db.get_latest_prices([ticker])
                    if prices.empty:
                        continue
                    price = float(prices.iloc[0]['close'])

                    trade = {
                        'ticker': ticker,
                        'action': 'SELL',
                        'shares': shares,
                        'price': price,
                        'total_value': shares * price,
                        'reasoning': d.get('reason', 'AI sell decision'),
                        'confidence': d.get('confidence', 70),
                        'hypothesis': f"AI exit: {d.get('reason', '')}",
                        'macro_context': {},
                        'target_price': None,
                        'stop_loss': None,
                        'target_pct': 0,
                        'stop_loss_pct': 0,
                    }
                    self.db.log_trade(trade)
                    executed.append(d)
                    logger.info(f"✅ SELL {ticker} executed ({d.get('confidence')}%)")

            except Exception as e:
                logger.error(f"Error executing {action} {ticker}: {e}", exc_info=True)

        return executed

    # ------------------------------------------------------------------
    # Full cycle
    # ------------------------------------------------------------------

    def run_cycle(self, trader, deep: bool = False) -> Dict[str, Any]:
        """
        Full brain cycle: gather data → Claude analysis → validate → execute.
        Returns summary dict.
        """
        logger.info(f"🧠 Starting brain cycle ({'deep' if deep else 'standard'})...")

        decisions = self.make_decisions(deep=deep)
        outlook = decisions.get("market_outlook", "neutral")
        summary = decisions.get("analysis_summary", "")

        logger.info(f"🧠 Market outlook: {outlook}")
        logger.info(f"🧠 Summary: {summary}")

        validated = self.validate_decisions(decisions)
        executed = self.execute_decisions(validated, trader)

        result = {
            "outlook": outlook,
            "summary": summary,
            "decisions_raw": len(decisions.get("decisions", [])),
            "decisions_validated": len(validated),
            "decisions_executed": len(executed),
            "executed": executed,
        }

        logger.info(
            f"🧠 Brain cycle complete: {result['decisions_raw']} raw → "
            f"{result['decisions_validated']} validated → "
            f"{result['decisions_executed']} executed"
        )

        return result

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def generate_daily_summary(self) -> str:
        """Generate end-of-day summary using Claude."""
        context = self.build_context(deep=True)
        
        # Get today's AI decisions
        today_decisions = self.db.query("""
            SELECT decisions_json, timestamp FROM ai_decisions
            WHERE timestamp::date = CURRENT_DATE
            ORDER BY timestamp
        """)
        
        decisions_text = "Inga AI-beslut idag."
        if today_decisions:
            parts = []
            for d in today_decisions:
                parts.append(f"{d['timestamp']}: {d['decisions_json']}")
            decisions_text = "\n".join(parts)

        user_msg = (
            f"Datum: {datetime.utcnow().strftime('%Y-%m-%d')} UTC\n\n"
            f"{context}\n\n"
            f"## DAGENS AI-BESLUT\n{decisions_text}\n\n"
            "Ge en kort daglig sammanfattning (3-5 meningar). Vad gick bra/dåligt? "
            "Vad bör vi fokusera på imorgon? Svara på svenska, plain text."
        )

        try:
            summary, _, _ = self._call_llm(
                system="Du är en trading-assistent. Ge koncisa dagliga sammanfattningar på svenska.",
                user_msg=user_msg,
                max_tokens=500,
            )
            logger.info(f"🧠 Daily summary: {summary}")
            return summary
        except Exception as e:
            logger.error(f"Daily summary error: {e}")
            return f"Kunde inte generera sammanfattning: {e}"

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    # LLM abstraction
    # ------------------------------------------------------------------

    def _call_llm(self, system: str, user_msg: str, max_tokens: int = 2000) -> tuple:
        """
        Call LLM (Ollama or Anthropic). Returns (text, prompt_tokens, response_tokens).
        """
        if self.backend == 'ollama':
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
            )
            raw_text = response.choices[0].message.content
            prompt_tokens = getattr(response.usage, 'prompt_tokens', 0) or 0
            response_tokens = getattr(response.usage, 'completion_tokens', 0) or 0
            return (raw_text, prompt_tokens, response_tokens)
        
        elif self.backend == 'anthropic':
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw_text = response.content[0].text
            prompt_tokens = response.usage.input_tokens
            response_tokens = response.usage.output_tokens
            return (raw_text, prompt_tokens, response_tokens)
        
        else:
            raise RuntimeError(f"Unknown backend: {self.backend}")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_decision(self, prompt_tokens: int, response_tokens: int,
                      decisions_json: Any, market_context: str,
                      raw_response: str = ""):
        """Log AI decision to database."""
        try:
            self.db.execute("""
                INSERT INTO ai_decisions 
                    (timestamp, prompt_tokens, response_tokens, decisions_json, market_data_json, raw_response)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                datetime.utcnow(),
                prompt_tokens,
                response_tokens,
                json.dumps(decisions_json, ensure_ascii=False),
                market_context[:10000],  # Truncate to avoid huge storage
                raw_response[:5000],
            ))
        except Exception as e:
            logger.error(f"Error logging AI decision: {e}")
