"""
AI Brain - Autonomous Trading Decision Engine

Uses a governed LLM backend to analyze market data and make trading decisions.
All decisions are logged to ai_decisions table for audit trail.
"""

import os
import json
import hashlib
import logging
import math
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from datetime import date, datetime, timedelta, timezone
from time import sleep
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from .notifier import TelegramNotifier
from .risk import (
    DecisionValidationError,
    validate_decision,
    validate_decision_response,
)
from .strategy import (
    ActiveStrategy,
    DEFAULT_TRADING_OBJECTIVE,
    render_objective_progress,
    render_system_prompt,
)
from ..model_config import (
    validate_hermes_model,
    validate_hermes_provider,
    validate_hermes_url,
    validate_reasoning_effort,
)
from ..runtime_secrets import read_runtime_secret
from ..data.market_data import (
    FreshnessPolicy,
    MarketDataError,
    MarketSession,
    QuoteRecord,
    assert_fresh_quote,
)

logger = logging.getLogger(__name__)


def trade_idempotency_key(
    *,
    cycle_key: str,
    action: str,
    ticker: str,
) -> str:
    """Derive a stable, non-sensitive order key for one scheduled cycle."""
    if not isinstance(cycle_key, str) or not cycle_key.strip():
        raise ValueError("cycle_key is required for trade idempotency")
    key_payload = json.dumps(
        {
            "cycle_key": cycle_key,
            "action": action,
            "ticker": ticker,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "brain:" + hashlib.sha256(key_payload).hexdigest()[:48]


# Try OpenAI client first (works with Ollama), fall back to Anthropic
HAS_OPENAI = False
HAS_ANTHROPIC = False

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    pass

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    pass

if not HAS_OPENAI and not HAS_ANTHROPIC:
    logger.warning("Neither openai nor anthropic package installed - brain disabled")


from .candidates import rank_candidate_signals, render_candidate_context

class TradingBrain:
    """AI-powered trading decision engine with explicit backend selection."""

    # Ollama model (free, local) - 14B for speed, 32B too slow with large context
    OLLAMA_MODEL = "qwen2.5-coder:14b"
    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def __init__(self, db):
        self.db = db
        self.backend = None
        self.model_provider = None
        self.reasoning_effort = None
        self.last_response_id = None
        self.last_response_model = None
        self.notifier = TelegramNotifier()

        backend = os.getenv("LLM_BACKEND", "openai-compatible")
        if backend == "openai-compatible":
            if not HAS_OPENAI:
                raise RuntimeError("OpenAI-compatible client is not installed")
            base_url = os.getenv(
                "OLLAMA_URL",
                "http://host.docker.internal:11434",
            ).rstrip("/")
            parsed = urlparse(base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise RuntimeError(
                    "OLLAMA_URL must be an absolute URL without credentials"
                )
            if (
                "OPENAI_COMPATIBLE_API_KEY" in os.environ
                or "OPENAI_COMPATIBLE_API_KEY_FILE" in os.environ
            ):
                api_key = read_runtime_secret(
                    "OPENAI_COMPATIBLE_API_KEY",
                    required=True,
                )
            else:
                api_key = "local-no-auth"
            self.client = OpenAI(
                base_url=f"{base_url}/v1",
                api_key=api_key,
            )
            self.backend = backend
            self.model = os.getenv("LLM_MODEL", self.OLLAMA_MODEL)
            self.model_provider = "openai-compatible"
        elif backend == "hermes":
            if not HAS_OPENAI:
                raise RuntimeError("OpenAI client is required for Hermes")
            try:
                base_url = validate_hermes_url(
                    os.getenv("HERMES_URL", "")
                )
                model = validate_hermes_model(
                    os.getenv("LLM_MODEL", "")
                )
                provider = validate_hermes_provider(
                    os.getenv("HERMES_PROVIDER", "openai-codex")
                )
                reasoning_effort = validate_reasoning_effort(
                    os.getenv("LLM_REASONING_EFFORT", "medium")
                )
            except ValueError as error:
                raise RuntimeError(str(error)) from error
            api_key = read_runtime_secret(
                "HERMES_API_KEY",
                required=True,
            )
            self.client = OpenAI(
                base_url=f"{base_url}/v1",
                api_key=api_key,
            )
            self.backend = backend
            self.model = model
            self.model_provider = provider
            self.reasoning_effort = reasoning_effort
        elif backend == "anthropic":
            if not HAS_ANTHROPIC:
                raise RuntimeError("Anthropic client is not installed")
            api_key = read_runtime_secret(
                "ANTHROPIC_API_KEY",
                required=True,
            )
            model = os.getenv("LLM_MODEL")
            if not model:
                raise RuntimeError(
                    "LLM_MODEL is required for the Anthropic backend"
                )
            self.client = anthropic.Anthropic(api_key=api_key)
            self.backend = backend
            self.model = model
            self.model_provider = "anthropic"
        else:
            raise RuntimeError("LLM_BACKEND is not supported")
        logger.info(
            "AI backend configured: backend=%s model=%s",
            self.backend,
            self.model,
        )

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
                    quote = self.db.get_latest_authorized_market_quote(
                        p['ticker'],
                    )
                    if (
                        quote is None
                        or (
                            quote.quote_id is None
                            and quote.book_state_id is None
                        )
                    ):
                        lines.append(
                            f"  {p['ticker']}: {shares:.1f} st @ {avg:.2f}, "
                            "färsk verifierad kurs saknas"
                        )
                        continue
                    cur = float(quote.last_price)
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

    def _get_objective_context(self) -> str:
        """Live progress toward the operator-owned paper-trading goal."""
        try:
            balance = self.db.get_balance()
            return render_objective_progress(
                DEFAULT_TRADING_OBJECTIVE,
                current_equity_sek=balance["total_value"],
            )
        except Exception as exc:
            logger.error(f"Objective context error: {exc}")
            return (
                f"Målversion: {DEFAULT_TRADING_OBJECTIVE.version}\n"
                "Aktuell målprogression är inte tillgänglig; föreslå ingen "
                "affär utan verifierbart portföljvärde."
            )

    def _get_macro_context(self) -> str:
        """Current market context from governed provider evidence."""
        now = self._now_utc()
        operational_reader = getattr(
            self.db,
            "require_operational_market_data",
            None,
        )
        session_closed = False
        if callable(operational_reader):
            try:
                operational = operational_reader(now)
                if (
                    operational.get("data_type")
                    == "delayed-pre-trade-equity"
                ):
                    eligible = int(
                        operational.get(
                            "eligible_instrument_count",
                            0,
                        )
                    )
                    provider = operational.get("provider") or "okänd källa"
                    return (
                        "XSTO public pre-trade: "
                        f"{eligible} färska tvåsidiga orderböcker "
                        f"({provider}).\n"
                        "Separat OMXSGI krävs inte i detta "
                        "interna papertradingläge."
                    )
            except MarketDataError as exc:
                session_closed = "XSTO session is not open" in str(exc)
            except (TypeError, ValueError):
                pass

        mode_reader = getattr(
            self.db,
            "get_authorized_market_data_mode",
            None,
        )
        if session_closed and callable(mode_reader):
            try:
                mode = mode_reader(now)
                if (
                    mode.get("data_type") == "delayed-pre-trade-equity"
                    and mode.get("usage_scope")
                    == "INTERNAL_ANALYSIS_AND_PAPER"
                ):
                    provider = mode.get("provider") or "okänd källa"
                    return (
                        "XSTO public pre-trade väntar på en öppen "
                        f"XSTO-session ({provider}).\n"
                        "Separat OMXSGI krävs inte i detta interna "
                        "papertradingläge."
                    )
            except (MarketDataError, TypeError, ValueError):
                pass

        try:
            signal = self.db.get_current_authorized_index_change(
                now,
            )
            level = float(signal["current_level"])
            change = float(signal["change_pct"])
            provider = signal["provider"]
            event_time = signal["event_time"].isoformat()
            return (
                f"OMXSGI: {level:.2f} ({change:+.1f}%)\n"
                f"Källa: {provider}, händelsetid: {event_time}"
            )
        except Exception as e:
            logger.error(f"Authorized market context error: {e}")
            return "Verifierad OMXSGI-data ej tillgänglig."

    def _get_market_session(self, now: datetime) -> Optional[MarketSession]:
        session_date = now.astimezone(
            ZoneInfo("Europe/Stockholm")
        ).date()
        rows = self.db.query("""
            SELECT
                mic,
                session_date,
                opens_at,
                closes_at,
                timezone_name,
                source,
                status
            FROM market_sessions
            WHERE mic = 'XSTO' AND session_date = :session_date
            LIMIT 1
        """, {'session_date': session_date})
        if not rows or rows[0].get('status') not in {'OPEN', 'HALF_DAY'}:
            return None
        row = rows[0]
        return MarketSession(
            mic=row['mic'],
            session_date=row['session_date'],
            opens_at=row['opens_at'],
            closes_at=row['closes_at'],
            timezone_name=row['timezone_name'],
            source=row['source'],
        )

    def _get_fresh_market_quote(
        self,
        ticker: str,
        now: datetime,
        *,
        action: str,
    ) -> Optional[QuoteRecord]:
        execution_reader = getattr(
            self.db,
            "get_latest_authorized_execution_quote",
            None,
        )
        quote_checked_at = now
        if callable(execution_reader):
            quote = None
            for attempt in range(4):
                quote = execution_reader(
                    ticker,
                    action=action,
                    now=quote_checked_at,
                )
                if quote is not None:
                    break
                if attempt < 3:
                    logger.info(
                        "Execution quote for %s is unavailable; "
                        "retrying in 5 seconds (%d/3)",
                        ticker,
                        attempt + 1,
                    )
                    sleep(5)
                    quote_checked_at = self._now_utc()
        else:
            # Compatibility for older adapters; the production Database always
            # supplies the action-specific execution reader.
            quote = self.db.get_latest_authorized_market_quote(ticker)
        if quote is None:
            return None
        if (quote.quote_id is None) == (quote.book_state_id is None):
            raise MarketDataError(
                "execution quote must carry exactly one evidence id"
            )
        try:
            max_delay_minutes = int(
                os.getenv("MARKET_DATA_MAX_DELAY_MINUTES", "15")
            )
            tolerance_minutes = int(
                os.getenv("MARKET_DATA_TOLERANCE_MINUTES", "2")
            )
        except ValueError as exc:
            raise MarketDataError(
                "market data freshness configuration is invalid"
            ) from exc
        assert_fresh_quote(
            quote,
            now=quote_checked_at,
            policy=FreshnessPolicy(
                max_delay=timedelta(minutes=max_delay_minutes),
                tolerance=timedelta(minutes=tolerance_minutes),
            ),
        )
        return quote

    def _get_news_context(self) -> str:
        """Exclude legacy news rows until their provenance is governed."""
        return (
            "Nyhetsdata används inte utan auktoriserad källa och proveniens."
        )

    def _get_technical_context(self) -> str:
        """Latest technical signals for all companies."""
        intraday_reader = getattr(
            self.db,
            "get_current_authorized_intraday_signals",
            None,
        )
        if callable(intraday_reader):
            try:
                intraday_rows = intraday_reader(
                    now=self._now_utc(),
                    window=20,
                    limit=50,
                )
                if intraday_rows:
                    return "\n".join(
                        (
                            f"{row['ticker']}: "
                            if not row.get("name")
                            else (
                                f"{row['ticker']} ({row['name']}, "
                                f"{row.get('sector') or 'Unclassified'}): "
                            )
                        )
                        + (
                            f"{float(row['latest_price']):.2f} SEK, "
                            f"SMA20={float(row['sma20']):.2f}, "
                            f"{float(row['momentum_pct']):+.2f}% mot SMA20 "
                            f"({int(row['window'])} minuter, "
                            f"{row['source']})"
                        )
                        for row in intraday_rows
                    )
            except Exception as exc:
                logger.error(
                    "Authorized intraday technical context error: %s",
                    exc,
                )
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


    def _get_candidate_universe(self, *, now: datetime) -> List[Dict[str, Any]]:
        """Load and rank the complete bounded XSTO shadow universe."""
        reader = getattr(
            self.db,
            "get_current_authorized_candidate_signals",
            None,
        )
        if not callable(reader):
            return []
        rows = reader(now=now, limit=1_000)
        policy_reader = getattr(
            self.db,
            "get_active_candidate_policy",
            None,
        )
        policy = policy_reader() if callable(policy_reader) else None
        return rank_candidate_signals(
            rows,
            limit=1_000,
            policy=policy,
        )

    @staticmethod
    def _model_candidate_snapshot(
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Bound model context while retaining all candidates for evaluation."""
        return [
            candidate
            for candidate in candidates
            if candidate.get("eligible") is True
        ][:20]

    def _get_learning_context(self, *, now: datetime) -> str:
        """Render only complete, sufficiently sized forward outcome evidence."""
        reader = getattr(self.db, "get_continuous_learning_status", None)
        if not callable(reader):
            return "Ingen verifierad utfallsserie är tillgänglig ännu."
        try:
            status = reader(now=now)
        except Exception as exc:
            logger.warning("Continuous learning evidence unavailable: %s", exc)
            return "Verifierade utfall kunde inte läsas; använd dem inte i beslutet."

        labelled = int(status.get("labelled_outcomes") or 0)
        matured = int(status.get("matured_outcomes") or 0)
        overdue = int(status.get("overdue_outcomes") or max(0, matured - labelled))
        coverage = float(status.get("coverage_pct") or 0.0)
        if labelled < 100 or overdue:
            return (
                f"Underlaget är otillräckligt: {labelled} av {matured} mogna "
                f"utfall är mätta ({coverage:.1f}% täckning, {overdue} saknas). "
                "Ändra inte urval, konfidens eller strategi utifrån dessa data."
            )

        lines = [
            (
                f"Verifierad framåtmätning: {labelled} av {matured} mogna "
                f"utfall ({coverage:.1f}% täckning)."
            )
        ]
        for metric in status.get("action_metrics") or []:
            action = str(metric.get("action") or "").upper()
            horizon = int(metric.get("horizon_minutes") or 0)
            outcomes = int(metric.get("outcomes") or 0)
            mean_return = float(metric.get("mean_return_bps") or 0.0)
            positive_rate = float(metric.get("positive_rate_pct") or 0.0)
            if action not in {"BUY", "SELL", "HOLD", "ABSTAIN"}:
                continue
            if horizon not in {30, 60, 120} or outcomes < 30:
                continue
            lines.append(
                f"{action} {horizon}m: {outcomes} utfall, "
                f"{mean_return:+.1f} bp marknadsavkastning i snitt, "
                f"kursen steg i {positive_rate:.1f}%."
            )
        lines.append(
            "Använd detta som kalibreringsstöd men ändrar aldrig riskregler, "
            "strategiversion eller handelsgränser."
        )
        return "\n".join(lines)


    def _record_candidate_snapshot(
        self,
        *,
        decision_id: int,
        candidates: List[Dict[str, Any]],
        model_decisions: List[Dict[str, Any]],
    ) -> List[int]:
        """Persist the exact model candidate set before any execution."""
        if not candidates:
            return []
        recorder = getattr(self.db, "record_candidate_predictions", None)
        if not callable(recorder):
            raise RuntimeError("candidate prediction journal is unavailable")
        prediction_ids = recorder(
            ai_decision_id=decision_id,
            candidates=candidates,
            model_decisions=model_decisions,
        )
        logger.info(
            "candidate_predictions_recorded policy=%s candidates=%d records=%d",
            candidates[0]["policy_version"],
            len(candidates),
            len(prediction_ids),
        )
        return prediction_ids

    def _get_prospects_context(self) -> str:
        """Current prospects with confidence."""
        try:
            rows = self.db.query("""
                SELECT ticker, name, thesis, confidence, entry_trigger, current_price
                FROM prospects
                WHERE status = 'watching' AND is_current = TRUE
                ORDER BY priority, confidence DESC LIMIT 10
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
        """Exclude legacy report rows until their provenance is governed."""
        return (
            "Rapportkalender används inte utan auktoriserad källa och "
            "proveniens."
        )

    def _get_prices_context(self) -> str:
        """Current prices from the authorized XSTO provider."""
        try:
            rows = self.db.get_latest_authorized_prices()
            if not rows:
                return "Prisdata ej tillgänglig."
            lines = []
            for r in rows:
                lines.append(
                    f"{r['ticker']}: {float(r['close']):.2f} SEK "
                    f"({r['source']}, {r['event_time'].isoformat()})"
                )
            return "\n".join(lines)
        except Exception:
            return "Prisdata ej tillgänglig."

    # ------------------------------------------------------------------
    # Core decision making
    # ------------------------------------------------------------------

    def build_context(
        self,
        deep: bool = False,
        *,
        candidate_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build the exact bounded context used for one model decision."""
        technical_context = (
            render_candidate_context(candidate_snapshot)
            if candidate_snapshot is not None
            else self._get_technical_context()
        )
        sections = [
            ("UPPDRAG OCH MÅL", self._get_objective_context()),
            ("PORTFÖLJ", self._get_portfolio_context()),
            ("MAKRO", self._get_macro_context()),
            ("NYHETER (24h)", self._get_news_context()),
            ("FLERHORISONT-KANDIDATER", technical_context),
            ("PROSPECTS", self._get_prospects_context()),
            ("RAPPORTER (5 dagar)", self._get_reports_context()),
        ]
        if deep and candidate_snapshot is None:
            sections.append(("ALLA PRISER", self._get_prices_context()))

        parts = []
        for title, content in sections:
            parts.append(f"## {title}\n{content}")
        return "\n\n".join(parts)

    def make_decisions(
        self,
        deep: bool = False,
        *,
        strategy: Optional[ActiveStrategy] = None,
    ) -> Dict[str, Any]:
        """Call the configured model and journal the exact candidate set."""
        active_strategy = strategy or self.db.get_active_strategy()
        now = self._now_utc()
        has_candidate_reader = callable(
            getattr(
                self.db,
                "get_current_authorized_candidate_signals",
                None,
            )
        )
        candidate_universe = (
            self._get_candidate_universe(now=now)
            if has_candidate_reader
            else []
        )
        candidate_snapshot = self._model_candidate_snapshot(
            candidate_universe
        )
        context = (
            self.build_context(
                deep=deep,
                candidate_snapshot=candidate_snapshot,
            )
            if has_candidate_reader
            else self.build_context(deep=deep)
        )
        learning_context = self._get_learning_context(now=now)
        context = (
            f"{context}\n\n"
            f"## UPPMÄTTA UTFALL\n{learning_context}"
        )
        user_msg = (
            f"Datum: {now.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
            f"{context}\n\n"
            "Analysera all data och ge dina trading-beslut som JSON."
        )

        logger.info(
            f"🧠 Calling {self.backend} ({self.model}) "
            f"({'deep' if deep else 'standard'} analysis)..."
        )

        try:
            raw_text, prompt_tokens, response_tokens = self._call_llm(
                system=render_system_prompt(active_strategy),
                user_msg=user_msg,
                max_tokens=2000,
            )

            logger.info(
                f"🧠 LLM response: {prompt_tokens} in / "
                f"{response_tokens} out tokens"
            )

            json_str = raw_text.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                json_str = "\n".join(
                    line
                    for line in lines
                    if not line.strip().startswith("```")
                )

            decisions = validate_decision_response(json.loads(json_str))
            decision_id = self._log_decision(
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                decisions_json=decisions,
                market_context=context,
                raw_response=raw_text,
                strategy=active_strategy,
                timestamp=now,
            )
            self._record_candidate_snapshot(
                decision_id=decision_id,
                candidates=candidate_universe,
                model_decisions=decisions.get("decisions", []),
            )
            decisions["_audit_id"] = decision_id
            return decisions

        except (json.JSONDecodeError, DecisionValidationError) as e:
            logger.error(f"Failed to parse model response as JSON: {e}")
            logger.error(f"Raw response: {raw_text[:500]}")
            decision_id = self._log_decision(
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                decisions_json={"error": str(e), "raw": raw_text[:1000]},
                market_context=context,
                raw_response=raw_text,
                strategy=active_strategy,
                timestamp=now,
            )
            self._record_candidate_snapshot(
                decision_id=decision_id,
                candidates=candidate_universe,
                model_decisions=[],
            )
            return {
                "decisions": [],
                "market_outlook": "neutral",
                "analysis_summary": f"Parse error: {e}",
                "_audit_id": decision_id,
            }

        except Exception as e:
            logger.error(f"Model API error: {e}", exc_info=True)
            return {
                "decisions": [],
                "market_outlook": "neutral",
                "analysis_summary": f"API error: {e}",
            }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_decisions(
        self,
        decisions: Dict[str, Any],
        *,
        strategy: Optional[ActiveStrategy] = None,
    ) -> List[Dict]:
        """
        Double-check decisions against hard rules.
        Returns list of validated, executable decisions.
        """
        active_strategy = strategy or self.db.get_active_strategy()
        config = active_strategy.config
        validated = []
        if not isinstance(decisions, dict):
            logger.error("Decision response must be a JSON object")
            return validated

        raw = decisions.get("decisions", [])

        if not raw:
            logger.info("🧠 No decisions from Claude.")
            return validated
        if not isinstance(raw, list):
            logger.error("Decision response field 'decisions' must be a list")
            return validated

        # Get current state
        try:
            balance = self.db.get_balance()
            portfolio = self.db.get_portfolio()
            cash = balance['cash']
            total_value = balance['total_value']
            company_rows = self.db.query(
                "SELECT ticker, sector FROM companies"
            )
        except Exception as e:
            logger.error(f"Cannot validate - DB error: {e}")
            return validated

        if not math.isfinite(float(cash)) or cash < 0:
            logger.error("Cannot validate - invalid cash balance")
            return validated
        if (
            total_value is None
            or not math.isfinite(float(total_value))
            or total_value <= 0
        ):
            logger.error("Cannot validate - invalid total portfolio value")
            return validated

        companies = {
            str(row['ticker']).upper(): row.get('sector')
            for row in company_rows
            if row.get('ticker')
        }
        allowed_tickers = set(companies)

        current_tickers = set()
        current_shares = {}
        if not portfolio.empty:
            for _, p in portfolio.iterrows():
                shares = float(p.get('shares', 0))
                if shares > 0:
                    ticker = str(p['ticker']).upper()
                    current_tickers.add(ticker)
                    current_shares[ticker] = shares

        num_positions = len(current_tickers)
        started_at_capacity = num_positions >= config.max_positions
        rotation_requested = started_at_capacity and any(
            isinstance(candidate, dict)
            and str(candidate.get("action", "")).strip().upper() == "BUY"
            for candidate in raw
        )
        projected_cash = float(cash)
        sold_tickers = set()
        rotation_sell_accepted = False
        now = self._now_utc()
        try:
            operational_status = self.db.require_operational_market_data(now)
            market_session = self._get_market_session(now)
        except (MarketDataError, ValueError) as exc:
            logger.error(
                f"Cannot validate - operational market data is not ready: "
                f"{exc}"
            )
            return validated
        public_pretrade = (
            operational_status.get("data_type")
            == "delayed-pre-trade-equity"
        )
        market_index_change = 0.0 if public_pretrade else None
        if public_pretrade:
            logger.info(
                "Public pre-trade mode: licensed OMXSGI risk signal is "
                "unavailable; neutral index input is used and all other "
                "position, freshness and loss limits remain active"
            )

        ordered_raw = list(enumerate(raw))
        if rotation_requested:
            ordered_raw.sort(
                key=lambda item: (
                    0
                    if isinstance(item[1], dict)
                    and str(item[1].get("action", "")).strip().upper()
                    == "SELL"
                    else 1,
                    item[0],
                )
            )

        for _, candidate in ordered_raw:
            try:
                d = validate_decision(
                    candidate,
                    allowed_tickers=allowed_tickers,
                    max_position_pct=config.max_position_pct,
                )
            except DecisionValidationError as exc:
                logger.info(f"Decision rejected: {exc}")
                continue

            action = d["action"]
            ticker = d["ticker"]
            confidence = d["confidence"]
            size_pct = d.get("position_size_pct")

            if action == "HOLD":
                continue

            if market_session is None or not market_session.is_open(now):
                logger.info(
                    f"🚫 {ticker} rejected: XSTO is closed or calendar is missing"
                )
                continue

            try:
                quote = self._get_fresh_market_quote(
                    ticker,
                    now,
                    action=action,
                )
            except MarketDataError as exc:
                logger.info(f"🚫 {ticker} rejected: {exc}")
                continue
            if quote is None:
                logger.info(
                    f"🚫 {ticker} rejected: fresh intraday quote is missing"
                )
                continue

            d['execution_price'] = float(quote.last_price)
            d['source_quote_id'] = quote.quote_id
            d['source_book_state_id'] = quote.book_state_id
            d['price_event_time'] = quote.event_time
            d['price_source'] = quote.source
            if quote.book_state_id is not None:
                if quote.volume is None or quote.volume <= 0:
                    logger.info(
                        f"🚫 {ticker} rejected: executable book quantity "
                        "is missing"
                    )
                    continue
                d['executable_quantity'] = float(quote.volume)

            if confidence <= config.min_confidence:
                logger.info(
                    f"🚫 {ticker} rejected: confidence {confidence}% <= "
                    f"{config.min_confidence:g}%"
                )
                continue

            if action == "BUY":
                if market_index_change is None:
                    try:
                        index_signal = (
                            self.db.get_current_authorized_index_change(now)
                        )
                        market_index_change = float(
                            index_signal["change_pct"]
                        )
                        if (
                            index_signal.get("symbol") != "OMXSGI"
                            or not math.isfinite(market_index_change)
                        ):
                            raise MarketDataError(
                                "authorized OMXSGI signal is invalid"
                            )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        MarketDataError,
                    ) as exc:
                        logger.info(
                            f"🚫 {ticker} rejected: current authorized "
                            f"OMXSGI signal is unavailable: {exc}"
                        )
                        continue

                if ticker in sold_tickers:
                    logger.info(
                        f"🚫 {ticker} rejected: same-cycle SELL/BUY round trip"
                    )
                    continue

                if num_positions >= config.max_positions:
                    logger.info(
                        f"🚫 {ticker} rejected: max "
                        f"{config.max_positions} positions reached"
                    )
                    continue

                if market_index_change < config.omxs30_risk_off_pct:
                    logger.info(
                        f"🚫 {ticker} rejected: OMXSGI "
                        f"{market_index_change:.1f}% (risk-off)"
                    )
                    continue

                # Rule: don't buy what we already own
                if ticker in current_tickers:
                    logger.info(f"🚫 {ticker} rejected: already in portfolio")
                    continue

                position_value = total_value * size_pct / 100

                # Check cash
                if position_value > projected_cash:
                    position_value = projected_cash * 0.9
                    if position_value < 500:
                        logger.info(f"🚫 {ticker} rejected: insufficient cash")
                        continue

                if config.require_price_above_sma20:
                    if public_pretrade:
                        tech = self.db.get_authorized_intraday_signal(
                            ticker,
                            now=now,
                            window=20,
                        )
                        if (
                            tech is None
                            or tech.get("book_state_id")
                                != quote.book_state_id
                        ):
                            logger.info(
                                f"🚫 {ticker} rejected: continuous 20-minute "
                                "pre-trade warm-up is incomplete"
                            )
                            continue
                        signal_date = tech.get("session_date")
                        rsi = None
                    else:
                        rows = self.db.query("""
                            SELECT date, rsi, sma20
                            FROM technical_signals
                            WHERE ticker = :ticker
                            ORDER BY date DESC
                            LIMIT 1
                        """, {"ticker": ticker})
                        tech = rows[0] if rows else None
                        signal_date = tech.get("date") if tech else None
                        rsi = tech.get("rsi") if tech else None

                    if tech is None or tech.get("sma20") is None:
                        logger.info(
                            f"🚫 {ticker} rejected: SMA20 or price data is missing"
                        )
                        continue

                    if isinstance(signal_date, str):
                        try:
                            signal_date = date.fromisoformat(signal_date)
                        except ValueError:
                            signal_date = None
                    if signal_date != market_session.session_date:
                        logger.info(
                            f"🚫 {ticker} rejected: SMA20 signal is not "
                            "from the current XSTO session"
                        )
                        continue

                    if rsi is not None:
                        rsi = float(rsi)
                        if rsi > 65:
                            logger.info(
                                f"⚠️ {ticker} warning: RSI {rsi:.0f} > 65 "
                                "(overköpt men tillåtet)"
                            )

                    sma20 = float(tech["sma20"])
                    price = float(quote.last_price)
                    if not math.isfinite(sma20) or not math.isfinite(price):
                        logger.info(
                            f"🚫 {ticker} rejected: non-finite price or SMA20"
                        )
                        continue
                    if price <= sma20:
                        logger.info(
                            f"🚫 {ticker} rejected: price {price:.2f} "
                            f"<= SMA20 {sma20:.2f}"
                        )
                        continue

                d['position_value'] = position_value
                d['strategy_version'] = active_strategy.version
                validated.append(d)
                num_positions += 1
                current_tickers.add(ticker)
                projected_cash -= position_value

            elif action == "SELL":
                if ticker not in current_tickers:
                    logger.info(f"🚫 {ticker} SELL rejected: not in portfolio")
                    continue

                # Rule: minimum holding period - don't sell same day as buy
                buy_trade = self.db.query("""
                    SELECT executed_at FROM trades
                    WHERE ticker = :ticker
                      AND action = 'BUY'
                      AND closed_at IS NULL
                    ORDER BY executed_at DESC LIMIT 1
                """, {'ticker': ticker})
                if buy_trade:
                    buy_time = buy_trade[0]['executed_at']
                    if isinstance(buy_time, str):
                        buy_time = datetime.fromisoformat(
                            buy_time.replace('Z', '+00:00')
                        )
                    if buy_time.tzinfo is None:
                        buy_time = buy_time.replace(tzinfo=timezone.utc)
                    else:
                        buy_time = buy_time.astimezone(timezone.utc)
                    hours_held = (
                        now.astimezone(timezone.utc) - buy_time
                    ).total_seconds() / 3600
                    if hours_held < config.min_holding_hours:
                        logger.info(
                            f"🚫 {ticker} SELL rejected: only held "
                            f"{hours_held:.1f}h (min "
                            f"{config.min_holding_hours:g}h)"
                        )
                        continue

                # Rule: only sell on bearish outlook or stop-loss, not just neutral
                outlook = decisions.get("market_outlook", "neutral")
                if (
                    outlook != "bearish"
                    and confidence < config.sell_confidence
                ):
                    logger.info(
                        f"🚫 {ticker} SELL rejected: outlook={outlook}, "
                        f"confidence={confidence}% (need bearish or conf≥"
                        f"{config.sell_confidence:g}%)"
                    )
                    continue

                shares = current_shares[ticker]
                executable_quantity = d.get('executable_quantity')
                shares_to_sell = (
                    min(shares, float(executable_quantity))
                    if executable_quantity is not None
                    else shares
                )
                full_exit = shares_to_sell >= shares
                if rotation_requested and not full_exit:
                    logger.info(
                        f"🚫 {ticker} SELL rejected: rotation requires a "
                        "complete exit before replacement"
                    )
                    continue
                if rotation_requested and rotation_sell_accepted:
                    logger.info(
                        f"🚫 {ticker} SELL rejected: at most one rotation "
                        "is allowed per cycle"
                    )
                    continue

                d['strategy_version'] = active_strategy.version
                validated.append(d)
                projected_cash += shares_to_sell * d['execution_price']
                if full_exit:
                    num_positions -= 1
                    current_tickers.remove(ticker)
                    sold_tickers.add(ticker)
                    if rotation_requested:
                        rotation_sell_accepted = True

        if rotation_requested and not any(
            item["action"] == "BUY" for item in validated
        ):
            validated = [
                item for item in validated if item["action"] != "SELL"
            ]
            logger.info(
                "Rotation cancelled: no replacement BUY passed validation"
            )
        elif rotation_requested:
            for item in validated:
                item["_rotation_pair"] = True

        # Check cash ratio - warn if too much cash sitting idle
        cash_ratio = (cash / total_value) * 100 if total_value > 0 else 0
        if cash_ratio > config.cash_warning_pct:
            logger.warning(
                f"⚠️ Cash ratio {cash_ratio:.0f}% exceeds strategy warning "
                f"{config.cash_warning_pct:g}%"
            )

        logger.info(f"🧠 Validated {len(validated)}/{len(raw)} decisions")
        return validated

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_decisions(
        self,
        decisions: List[Dict],
        trader,
        *,
        cycle_key: str,
        strategy: ActiveStrategy,
        decision_id: int,
    ) -> List[Dict]:
        """Execute validated decisions via PaperTrader."""
        if (
            decisions
            and (
                isinstance(decision_id, bool)
                or not isinstance(decision_id, int)
                or decision_id <= 0
            )
        ):
            raise ValueError(
                "decision_id is required for AI trade execution"
            )
        executed = []
        rotation_sell_executed = False

        for d in decisions:
            action = d["action"].upper()
            ticker = d["ticker"]
            if (
                action == "BUY"
                and d.get("_rotation_pair") is True
                and not rotation_sell_executed
            ):
                logger.info(
                    f"🚫 {ticker} BUY skipped: paired rotation SELL did "
                    "not execute"
                )
                continue
            idempotency_key = trade_idempotency_key(
                cycle_key=cycle_key,
                action=action,
                ticker=ticker,
            )

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
                        'execution_price': d.get('execution_price'),
                        'source_quote_id': d.get('source_quote_id'),
                        'source_book_state_id': d.get(
                            'source_book_state_id'
                        ),
                        'executable_quantity': d.get(
                            'executable_quantity'
                        ),
                        'idempotency_key': idempotency_key,
                        'decision_id': decision_id,
                        'decision_origin': 'AI_DECISION',
                        'strategy_version': strategy.version,
                        'target_pct': strategy.config.take_profit_pct,
                        'stop_loss_pct': strategy.config.stop_loss_pct,
                    }
                    if trader.execute_trade(opp):
                        executed.append(d)
                        logger.info(f"✅ BUY {ticker} executed ({d.get('confidence')}%)")
                        self.notifier.notify_trade(opp)

                elif action == "SELL":
                    # Get current position
                    portfolio = self.db.get_portfolio()
                    pos = portfolio[portfolio['ticker'] == ticker]
                    if pos.empty:
                        continue
                    shares = float(pos.iloc[0]['shares'])
                    price = d.get('execution_price')
                    if price is None:
                        continue
                    price = float(price)
                    if d.get('source_book_state_id') is not None:
                        try:
                            executable_quantity = Decimal(
                                str(d.get('executable_quantity'))
                            )
                        except (InvalidOperation, TypeError, ValueError):
                            logger.info(
                                f"🚫 {ticker} SELL rejected: executable "
                                "book quantity is invalid"
                            )
                            continue
                        if (
                            not executable_quantity.is_finite()
                            or executable_quantity <= 0
                        ):
                            logger.info(
                                f"🚫 {ticker} SELL rejected: executable "
                                "book quantity is invalid"
                            )
                            continue
                        executable_quantity = executable_quantity.quantize(
                            Decimal('0.0001'),
                            rounding=ROUND_DOWN,
                        )
                        if executable_quantity <= 0:
                            logger.info(
                                f"🚫 {ticker} SELL rejected: executable "
                                "book quantity is below trade precision"
                            )
                            continue
                        original_shares = shares
                        shares = float(min(
                            Decimal(str(shares)),
                            executable_quantity,
                        ).quantize(
                            Decimal('0.0001'),
                            rounding=ROUND_DOWN,
                        ))
                        if shares < original_shares:
                            logger.info(
                                f"Partial SELL {ticker}: capped from "
                                f"{original_shares:.4f} to {shares:.4f} "
                                "shares by executable book quantity"
                            )

                    trade = {
                        'ticker': ticker,
                        'action': 'SELL',
                        'shares': shares,
                        'price': price,
                        'total_value': float((
                            Decimal(str(shares)) * Decimal(str(price))
                        ).quantize(Decimal('0.01'))),
                        'reasoning': d.get('reason', 'AI sell decision'),
                        'confidence': d.get('confidence', 70),
                        'hypothesis': f"AI exit: {d.get('reason', '')}",
                        'macro_context': {},
                        'target_price': None,
                        'stop_loss': None,
                        'target_pct': 0,
                        'stop_loss_pct': 0,
                        'idempotency_key': idempotency_key,
                        'strategy_version': strategy.version,
                        'source_quote_id': d.get('source_quote_id'),
                        'source_book_state_id': d.get(
                            'source_book_state_id'
                        ),
                        'decision_id': decision_id,
                        'decision_origin': 'AI_DECISION',
                    }
                    result = self.db.log_trade_result(trade)
                    if result.inserted:
                        if d.get("_rotation_pair") is True:
                            rotation_sell_executed = True
                        executed.append(d)
                        logger.info(
                            f"✅ SELL {ticker} executed "
                            f"({d.get('confidence')}%)"
                        )
                        self.notifier.notify_trade(trade)
                    else:
                        logger.info(
                            f"Duplicate SELL {ticker} ignored "
                            f"(trade {result.trade_id})"
                        )

            except Exception as e:
                logger.error(f"Error executing {action} {ticker}: {e}", exc_info=True)

        return executed

    # ------------------------------------------------------------------
    # Full cycle
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        trader,
        deep: bool = False,
        *,
        cycle_key: str,
    ) -> Dict[str, Any]:
        """
        Full brain cycle: gather data → Claude analysis → validate → execute.
        Returns summary dict.
        """
        logger.info(f"🧠 Starting brain cycle ({'deep' if deep else 'standard'})...")

        outcomes_labelled = 0
        outcome_recorder = getattr(
            self.db,
            "record_candidate_prediction_outcomes",
            None,
        )
        if callable(outcome_recorder):
            outcomes_labelled = outcome_recorder(
                evaluated_at=self._now_utc(),
            )
            logger.info(
                "candidate_outcomes_labelled count=%d",
                outcomes_labelled,
            )

        strategy = self.db.get_active_strategy()
        decisions = self.make_decisions(deep=deep, strategy=strategy)
        outlook = decisions.get("market_outlook", "neutral")
        summary = decisions.get("analysis_summary", "")

        logger.info(f"🧠 Market outlook: {outlook}")
        logger.info(f"🧠 Summary: {summary}")

        validated = self.validate_decisions(decisions, strategy=strategy)
        executed = self.execute_decisions(
            validated,
            trader,
            cycle_key=cycle_key,
            strategy=strategy,
            decision_id=decisions.get("_audit_id"),
        )

        result = {
            "outlook": outlook,
            "summary": summary,
            "decisions_raw": len(decisions.get("decisions", [])),
            "decisions_validated": len(validated),
            "decisions_executed": len(executed),
            "outcomes_labelled": outcomes_labelled,
            "executed": executed,
            "strategy_version": strategy.version,
            "strategy_config_hash": strategy.config_hash,
        }

        logger.info(
            f"🧠 Brain cycle complete: {result['decisions_raw']} raw → "
            f"{result['decisions_validated']} validated → "
            f"{result['decisions_executed']} executed"
        )

        # Send morning briefing on deep cycle
        if deep:
            try:
                pv = trader.get_portfolio_value()
                self.notifier.notify_morning_briefing(
                    outlook, validated, pv['total_value'], pv['pnl']
                )
            except Exception as e:
                logger.error(f"Morning briefing notification error: {e}")

        return result

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def generate_daily_summary(self) -> str:
        """Generate end-of-day summary using Claude."""
        now = self._now_utc()
        stockholm = ZoneInfo("Europe/Stockholm")
        local_date = now.astimezone(stockholm).date()
        day_start = datetime.combine(
            local_date,
            datetime.min.time(),
            stockholm,
        ).astimezone(timezone.utc)
        day_end = datetime.combine(
            local_date + timedelta(days=1),
            datetime.min.time(),
            stockholm,
        ).astimezone(timezone.utc)
        context = self.build_context(deep=True)
        
        # Get today's AI decisions
        today_decisions = self.db.query("""
            SELECT decisions_json, timestamp FROM ai_decisions
            WHERE timestamp >= :day_start
              AND timestamp < :day_end
            ORDER BY timestamp
        """, {
            'day_start': day_start,
            'day_end': day_end,
        })
        
        decisions_text = "Inga AI-beslut idag."
        if today_decisions:
            parts = []
            for d in today_decisions:
                parts.append(f"{d['timestamp']}: {d['decisions_json']}")
            decisions_text = "\n".join(parts)

        user_msg = (
            f"Datum: {now.strftime('%Y-%m-%d')} UTC\n\n"
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
        Call an OpenAI-compatible or Anthropic LLM.

        Returns (text, prompt_tokens, response_tokens).
        """
        if self.backend in {'ollama', 'openai-compatible'}:
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
            self.last_response_id = getattr(response, "id", None)
            self.last_response_model = getattr(response, "model", None)
            prompt_tokens = getattr(response.usage, 'prompt_tokens', 0) or 0
            response_tokens = getattr(response.usage, 'completion_tokens', 0) or 0
            return (raw_text, prompt_tokens, response_tokens)

        elif self.backend == "hermes":
            reasoning = (
                {"enabled": False}
                if self.reasoning_effort == "none"
                else {
                    "enabled": True,
                    "effort": self.reasoning_effort,
                }
            )
            response = self.client.responses.create(
                model=self.model,
                instructions=system,
                input=user_msg,
                max_output_tokens=max_tokens,
                store=False,
                extra_body={
                    "provider": self.model_provider,
                    "model_options": {"reasoning": reasoning},
                },
            )
            raw_text = response.output_text
            if not isinstance(raw_text, str):
                raise RuntimeError("Hermes response did not contain text")
            self.last_response_id = getattr(response, "id", None)
            self.last_response_model = getattr(response, "model", None)
            prompt_tokens = getattr(response.usage, "input_tokens", 0) or 0
            response_tokens = (
                getattr(response.usage, "output_tokens", 0) or 0
            )
            return (raw_text, prompt_tokens, response_tokens)

        elif self.backend == 'anthropic':
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw_text = response.content[0].text
            self.last_response_id = getattr(response, "id", None)
            self.last_response_model = getattr(response, "model", None)
            prompt_tokens = response.usage.input_tokens
            response_tokens = response.usage.output_tokens
            return (raw_text, prompt_tokens, response_tokens)
        
        else:
            raise RuntimeError(f"Unknown backend: {self.backend}")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_decision(
        self,
        prompt_tokens: int,
        response_tokens: int,
        decisions_json: Any,
        market_context: str,
        raw_response: str = "",
        strategy: Optional[ActiveStrategy] = None,
        timestamp: Optional[datetime] = None,
    ) -> int:
        """Log one AI decision with the clock used for its evidence."""
        if strategy is None:
            raise ValueError("strategy is required for AI decision audit")
        decision_at = timestamp or self._now_utc()
        if (
            not isinstance(decision_at, datetime)
            or decision_at.tzinfo is None
            or decision_at.utcoffset() is None
        ):
            raise ValueError("AI decision timestamp must be timezone-aware")
        try:
            return self.db.log_ai_decision(
                timestamp=decision_at.astimezone(timezone.utc),
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                decisions_json=decisions_json,
                market_data_json=market_context[:10000],
                raw_response=raw_response[:5000],
                strategy_version=strategy.version,
                strategy_config_hash=strategy.config_hash,
                model_backend=str(
                    getattr(self, "backend", None) or "legacy"
                ),
                model_name=str(
                    getattr(self, "model", None) or "unknown"
                ),
                model_provider=getattr(self, "model_provider", None),
                reasoning_effort=getattr(
                    self,
                    "reasoning_effort",
                    None,
                ),
                response_model=getattr(
                    self,
                    "last_response_model",
                    None,
                ),
                response_id=getattr(self, "last_response_id", None),
            )
        except Exception as e:
            logger.error(f"Error logging AI decision: {e}")
            raise
