"""
Trading Agent - Main Entry Point

This is the core agent that:
1. Consumes validated provider data from PostgreSQL
2. Analyzes companies and macro factors
3. Makes paper trading decisions
4. Logs everything with reasoning
5. Learns from outcomes

Schedule:
- 07:00: Pre-market analysis, macro update
- 09:00: Market open, scan for entries
- 12:00: Midday check, update prices
- 17:30: Market close, EOD analysis
- 22:00: Evening review, learning

Run modes:
- python -m agent.src.main              # Auto-detect based on time
- python -m agent.src.main morning      # Force morning routine
- python -m agent.src.main analyze      # Run full analysis
- python -m agent.src.main snapshot     # Save portfolio snapshot
"""

import sys
import time
import logging
from datetime import datetime, timedelta, timezone

from .data.database import Database
from .core.analyzer import MarketAnalyzer
from .core.trader import PaperTrader
from .core.brain import TradingBrain
from .core.student import TradingStudent
from .core.notifier import TelegramNotifier
from .core.schedule import (
    BRAIN_CYCLE_INTERVAL_MINUTES,
    brain_cycle_slot,
    due_routines,
    recoverable_routines,
    recovery_slots,
    stockholm_now,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _utc_instant(now: datetime | None = None) -> datetime:
    """Return one validated UTC instant for an entire routine."""
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return checked_at.astimezone(timezone.utc)


def main():
    """Main agent entry point."""
    logger.info("🤖 Trading Agent starting up...")
    
    # Initialize components
    db = Database()
    analyzer = MarketAnalyzer(db)
    trader = PaperTrader(db)
    
    # Initialize AI brain
    brain = None
    try:
        brain = TradingBrain(db)
        logger.info(
            "🧠 AI Brain initialized (%s / %s)",
            brain.backend,
            brain.model,
        )
    except Exception as e:
        logger.warning(f"⚠️ AI Brain not available: {e}")
    
    # Initialize study module
    student = None
    try:
        student = TradingStudent(db)
        logger.info("📚 Trading Student initialized")
    except Exception as e:
        logger.warning(f"⚠️ Trading Student not available: {e}")
    
    logger.info("✅ Components initialized")
    
    # Check for command-line mode override
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    
    if mode and mode == 'daemon':
        run_daemon(db, analyzer, trader, brain, student)
    elif mode == 'brain':
        if brain:
            manual_slot = int(datetime.now(timezone.utc).timestamp() // 60)
            result = brain.run_cycle(
                trader,
                deep=True,
                cycle_key=f"manual-brain:{manual_slot}",
            )
            logger.info(f"🧠 Brain result: {result}")
        else:
            logger.error("Brain not available")
    elif mode == 'student':
        if student:
            result = student.study_cycle()
            logger.info(f"📚 Student result: {result}")
        else:
            logger.error("Student not available")
    elif mode == 'deep_study':
        if student:
            result = student.deep_study()
            logger.info(f"📚🔬 Deep study result: {result}")
        else:
            logger.error("Student not available")
    elif mode:
        run_mode(mode, db, analyzer, trader, brain, student)
        logger.info("✅ Agent routine complete")
    else:
        run_daemon(db, analyzer, trader, brain, student)


def run_daemon(db, analyzer, trader, brain=None, student=None):
    """Run as a long-lived daemon with scheduled routines.
    
    Schedule:
    - Every 10 min during market hours: validate provider data and run TA
    - Every 15 min during the explicit XSTO market session: brain cycle
    - Every 60 min OUTSIDE market hours: student study cycle
    - Every 2 hours on weekends: deep study cycle
    - 07:00 Europe/Stockholm: morning deep analysis
    - 17:40 Europe/Stockholm: daily summary
    """
    logger.info("🔄 Running in daemon mode — market/study cycles every 10/60 minutes")
    
    completed_routines = set()
    
    while True:
        try:
            now = datetime.now(timezone.utc)
            local_now = stockholm_now(now)

            try:
                for session_date in (
                    local_now.date() - timedelta(days=1),
                    local_now.date(),
                ):
                    completed_routines.update(
                        db.get_successful_scheduled_routine_keys(session_date)
                    )
                schedule_evidence_ready = True
            except Exception:
                logger.error(
                    "Scheduled routine evidence is unavailable; "
                    "scheduled execution is paused"
                )
                schedule_evidence_ready = False

            if schedule_evidence_ready:
                current_routines = due_routines(
                    now,
                    completed=completed_routines,
                )
                current_keys = {routine.key for routine in current_routines}
                recovered_routines = tuple(
                    routine
                    for routine in recoverable_routines(
                        now,
                        completed=completed_routines,
                    )
                    if routine.key not in current_keys
                )
                routines = tuple(current_routines) + recovered_routines
            else:
                routines = ()
            for routine in routines:
                logger.info(
                    f"⏰ Scheduled run: {routine.name} "
                    f"({routine.scheduled_at.strftime('%H:%M %Z')})"
                )
                try:
                    if routine.name == 'morning':
                        run_morning_routine(db, analyzer)
                        if brain and not routine.recovery:
                            result = brain.run_cycle(
                                trader,
                                deep=True,
                                cycle_key=(
                                    "scheduled-morning:"
                                    f"{routine.scheduled_at.isoformat()}"
                                ),
                            )
                            logger.info(
                                f"🧠 Morning brain: "
                                f"{result['decisions_executed']} trades"
                            )
                    elif routine.name == 'close':
                        run_eod_routine(db, analyzer, trader)
                        if brain:
                            summary = brain.generate_daily_summary()
                            logger.info(f"🧠 Daily summary: {summary}")
                    else:
                        run_mode(
                            routine.name,
                            db,
                            analyzer,
                            trader,
                            brain,
                            student,
                            now=now,
                        )
                except Exception as e:
                    try:
                        db.record_scheduled_routine_event(
                            routine_key=routine.key,
                            routine_name=routine.name,
                            scheduled_at=routine.scheduled_at,
                            status="FAILED",
                            failure_code="ROUTINE_EXECUTION_ERROR",
                            observed_at=datetime.now(timezone.utc),
                        )
                    except Exception:
                        logger.error(
                            "Failed to persist scheduled routine failure"
                        )
                    logger.error(
                        f"Scheduled {routine.name} error: {e}",
                        exc_info=True,
                    )
                else:
                    try:
                        db.record_scheduled_routine_event(
                            routine_key=routine.key,
                            routine_name=routine.name,
                            scheduled_at=routine.scheduled_at,
                            status="SUCCEEDED",
                            failure_code=None,
                            observed_at=datetime.now(timezone.utc),
                        )
                    except Exception:
                        logger.error(
                            "Failed to persist scheduled routine success"
                        )
                    completed_routines.add(routine.key)

            retained_dates = {
                (local_now.date() - timedelta(days=1)).isoformat(),
                local_now.date().isoformat(),
            }
            completed_routines = {
                key
                for key in completed_routines
                if any(key.startswith(day) for day in retained_dates)
            }

            try:
                market_session = db.get_market_session(
                    'XSTO',
                    local_now.date(),
                )
                market_open = (
                    market_session is not None
                    and market_session.is_open(now)
                )
            except Exception as e:
                logger.error(
                    f"Market calendar unavailable: {e}",
                    exc_info=True,
                )
                market_open = False

            # ------ EVERY 10 MIN DURING AN EXPLICIT MARKET SESSION ------
            if market_open:
                logger.info(
                    f"📊 Provider-data validation + TA "
                    f"({local_now.strftime('%H:%M %Z')})"
                )
                db.require_operational_market_data(now)
                
                # Technical analysis after price update
                try:
                    ta_alerts = analyzer.run_technical_analysis()
                    if ta_alerts:
                        for alert in ta_alerts:
                            logger.warning(f"🔔 TA Alert: {alert['ticker']} - {alert['type']} (RSI={alert['rsi']:.1f})")
                except Exception as e:
                    logger.error(f"Technical analysis error: {e}")
                
                trader.check_positions(now=now)
                db.save_portfolio_snapshot()
                
                # ------ DURABLE BRAIN CYCLE SLOTS WHILE MARKET IS OPEN ------
                if brain:
                    try:
                        latest_brain_slot = (
                            db.get_latest_terminal_scheduled_job_at(
                                "brain_cycle"
                            )
                        )
                        session_opens_at = getattr(
                            market_session,
                            "opens_at",
                            None,
                        )
                        if (
                            latest_brain_slot is not None
                            and session_opens_at is not None
                            and latest_brain_slot < session_opens_at
                        ):
                            latest_brain_slot = session_opens_at - timedelta(
                                minutes=BRAIN_CYCLE_INTERVAL_MINUTES
                            )
                        brain_slots = recovery_slots(
                            now,
                            interval_minutes=BRAIN_CYCLE_INTERVAL_MINUTES,
                            last_completed_at=latest_brain_slot,
                            max_backfill_slots=40,
                        )
                    except Exception as e:
                        logger.error(
                            f"Brain schedule evidence unavailable: {e}",
                            exc_info=True,
                        )
                        brain_slots = ()

                    for slot_at in brain_slots:
                        brain_slot = brain_cycle_slot(slot_at)
                        cycle_key = f"scheduled-brain:{brain_slot}"
                        is_current = slot_at == brain_slots[-1]
                        run_kind = "CURRENT" if is_current else "STALE_SKIP"
                        claimed_at = now
                        try:
                            claimed = db.claim_scheduled_job_run(
                                job_key=cycle_key,
                                job_name="brain_cycle",
                                scheduled_at=slot_at,
                                observed_at=claimed_at,
                                run_kind=run_kind,
                            )
                        except Exception as e:
                            logger.error(
                                f"Brain slot claim failed: {e}",
                                exc_info=True,
                            )
                            continue
                        if not claimed:
                            continue

                        if not is_current:
                            db.complete_scheduled_job_run(
                                job_key=cycle_key,
                                claimed_at=claimed_at,
                                status="SKIPPED_STALE",
                                observed_at=now,
                            )
                            logger.warning(
                                "brain_cycle_skipped_stale slot=%d scheduled_at=%s",
                                brain_slot,
                                slot_at.isoformat(),
                            )
                            continue

                        logger.info(
                            "brain_cycle_started slot=%d interval_minutes=%d "
                            "local_time=%s",
                            brain_slot,
                            BRAIN_CYCLE_INTERVAL_MINUTES,
                            local_now.strftime("%H:%M %Z"),
                        )
                        try:
                            refreshed = analyzer.update_prospects(now=now)
                            logger.info(
                                "📋 Refreshed %d current prospects",
                                refreshed,
                            )
                            result = brain.run_cycle(
                                trader,
                                deep=False,
                                cycle_key=cycle_key,
                            )
                            db.complete_scheduled_job_run(
                                job_key=cycle_key,
                                claimed_at=claimed_at,
                                status="SUCCEEDED",
                                observed_at=now,
                            )
                            logger.info(
                                "brain_cycle_completed slot=%d outlook=%s "
                                "raw=%d validated=%d executed=%d",
                                brain_slot,
                                result["outlook"],
                                result["decisions_raw"],
                                result["decisions_validated"],
                                result["decisions_executed"],
                            )
                        except Exception as e:
                            try:
                                db.complete_scheduled_job_run(
                                    job_key=cycle_key,
                                    claimed_at=claimed_at,
                                    status="FAILED",
                                    failure_code="BRAIN_CYCLE_ERROR",
                                    observed_at=now,
                                )
                            except Exception:
                                logger.error(
                                    "Failed to persist brain cycle failure",
                                    exc_info=True,
                                )
                            logger.error(
                                f"Brain cycle error: {e}",
                                exc_info=True,
                            )
            else:
                # ------ DURABLE STUDENT SLOTS OUTSIDE MARKET HOURS ------
                if student:
                    try:
                        latest_study_slot = (
                            db.get_latest_terminal_scheduled_job_at(
                                "student_study"
                            )
                        )
                        study_slots = recovery_slots(
                            now,
                            interval_minutes=60,
                            last_completed_at=latest_study_slot,
                            max_backfill_slots=6,
                        )
                    except Exception as e:
                        logger.error(
                            f"Study schedule evidence unavailable: {e}",
                            exc_info=True,
                        )
                        study_slots = ()

                    for slot_at in study_slots:
                        local_slot = stockholm_now(slot_at)
                        slot_id = int(slot_at.timestamp() // 3600)
                        job_key = f"scheduled-study:{slot_id}"
                        weekend_idle = (
                            local_slot.weekday() >= 5
                            and local_slot.hour % 2 != 0
                        )
                        run_kind = (
                            "STALE_SKIP"
                            if weekend_idle
                            else (
                                "CURRENT"
                                if slot_at == study_slots[-1]
                                else "RECOVERY"
                            )
                        )
                        claimed_at = now
                        try:
                            claimed = db.claim_scheduled_job_run(
                                job_key=job_key,
                                job_name="student_study",
                                scheduled_at=slot_at,
                                observed_at=claimed_at,
                                run_kind=run_kind,
                            )
                        except Exception as e:
                            logger.error(
                                f"Study slot claim failed: {e}",
                                exc_info=True,
                            )
                            continue
                        if not claimed:
                            continue

                        if weekend_idle:
                            db.complete_scheduled_job_run(
                                job_key=job_key,
                                claimed_at=claimed_at,
                                status="SKIPPED_STALE",
                                observed_at=now,
                            )
                            continue

                        try:
                            if local_slot.weekday() >= 5:
                                logger.info(
                                    "📚🔬 Weekend deep study (%s)",
                                    local_slot.strftime("%H:%M %Z"),
                                )
                                result = student.deep_study(now=slot_at)
                            else:
                                logger.info(
                                    "📚 Study cycle (%s)",
                                    local_slot.strftime("%H:%M %Z"),
                                )
                                result = student.study_cycle(now=slot_at)
                            db.complete_scheduled_job_run(
                                job_key=job_key,
                                claimed_at=claimed_at,
                                status="SUCCEEDED",
                                observed_at=now,
                            )
                            if result.get("studies_completed"):
                                logger.info(
                                    "📚 Study: %s",
                                    result["studies_completed"],
                                )
                            if result.get("insights_generated", 0) > 0:
                                logger.info(
                                    "💡 Generated %d insights",
                                    result["insights_generated"],
                                )
                            if result.get("learnings_added", 0) > 0:
                                logger.info(
                                    "📚 Added %d learnings",
                                    result["learnings_added"],
                                )
                        except Exception as e:
                            try:
                                db.complete_scheduled_job_run(
                                    job_key=job_key,
                                    claimed_at=claimed_at,
                                    status="FAILED",
                                    failure_code="STUDY_CYCLE_ERROR",
                                    observed_at=now,
                                )
                            except Exception:
                                logger.error(
                                    "Failed to persist study cycle failure",
                                    exc_info=True,
                                )
                            logger.error(
                                f"Study cycle error: {e}",
                                exc_info=True,
                            )

                logger.debug(
                    f"💤 XSTO closed ({local_now.strftime('%H:%M %Z')})"
                )
            
            # Sleep 10 minutes between checks
            time.sleep(600)
            
        except KeyboardInterrupt:
            logger.info("🛑 Agent shutting down...")
            break
        except Exception as e:
            logger.error(f"❌ Error in daemon loop: {e}", exc_info=True)
            time.sleep(60)


def run_mode(
    mode: str,
    db,
    analyzer,
    trader,
    brain=None,
    student=None,
    *,
    now: datetime | None = None,
):
    """Run a specific mode."""
    logger.info(f"🎯 Running mode: {mode}")
    
    if mode == 'morning':
        run_morning_routine(db, analyzer)
    elif mode == 'open':
        run_market_open_routine(db, analyzer, trader, now=now)
    elif mode == 'midday':
        run_midday_routine(db, trader, now=now)
    elif mode == 'close':
        run_eod_routine(db, analyzer, trader)
    elif mode == 'evening':
        run_evening_routine(db, analyzer, trader, now=now)
    elif mode == 'analyze':
        run_full_analysis(db, analyzer)
    elif mode == 'snapshot':
        db.save_portfolio_snapshot()
    elif mode == 'prospects':
        analyzer.update_prospects()
    elif mode == 'student':
        if student:
            result = student.study_cycle(now=now)
            logger.info(f"📚 Student cycle result: {result}")
        else:
            logger.error("Student not available")
    elif mode == 'deep_study':
        if student:
            result = student.deep_study(now=now)
            logger.info(f"📚 Deep study result: {result}")
        else:
            logger.error("Student not available")
    else:
        logger.warning(f"Unknown mode: {mode}")
        logger.info("Available modes: morning, open, midday, close, evening, analyze, snapshot, prospects, student, deep_study")


def run_scheduled(hour: int, db, analyzer, trader):
    """Run routine based on current hour."""
    
    if hour == 7:
        run_morning_routine(db, analyzer)
    elif hour == 9:
        run_market_open_routine(db, analyzer, trader)
    elif hour == 12:
        run_midday_routine(db, trader)
    elif hour in [17, 18]:
        run_eod_routine(db, analyzer, trader)
    elif hour == 22:
        run_evening_routine(db, analyzer, trader)
    else:
        logger.info("🔄 Ad-hoc run - saving provider-valued snapshot...")
        db.save_portfolio_snapshot()


def run_morning_routine(db, analyzer):
    """
    Pre-market analysis routine (07:00).
    - Use already ingested research context
    - Generate morning briefing
    - Update prospects
    """
    logger.info("🌅 Morning routine starting...")
    logger.info(
        "📰 External news and report scrapers are disabled until an "
        "authorized provider with provenance is configured"
    )
    
    # Technical analysis
    logger.info("📈 Running technical analysis...")
    try:
        ta_alerts = analyzer.run_technical_analysis()
        if ta_alerts:
            for alert in ta_alerts:
                logger.warning(f"🔔 TA Alert: {alert['ticker']} - {alert['type']} (RSI={alert['rsi']:.1f})")
    except Exception as e:
        logger.warning(f"Technical analysis failed: {e}")
    
    # Generate morning briefing
    briefing = analyzer.generate_morning_briefing()
    
    # Update prospects based on new data
    analyzer.update_prospects()
    
    logger.info("✅ Morning routine complete")
    return briefing


def run_market_open_routine(
    db,
    analyzer,
    trader,
    *,
    now: datetime | None = None,
):
    """
    Market open routine (09:00).
    - Fresh price update
    - Find opportunities (NO auto-trade — Börje decides)
    - Check stop-loss/take-profit on existing positions
    """
    logger.info("📈 Market open routine starting...")
    
    checked_at = _utc_instant(now)
    db.require_operational_market_data(checked_at)
    
    # Find opportunities (for Börje to review)
    opportunities = analyzer.find_opportunities(now=checked_at)
    
    if opportunities:
        logger.info(f"📋 {len(opportunities)} opportunities found (awaiting Börje's decision)")
        for opp in opportunities[:5]:
            logger.info(f"   {opp['ticker']}: {opp['confidence']:.0f}% — {opp.get('thesis', 'N/A')}")
    
    # Auto stop-loss/take-profit on existing positions (mechanical, no brain needed)
    trader.check_positions(now=checked_at)
    
    # Update prospects from the same authorised clock.
    analyzer.update_prospects(now=checked_at)
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Market open routine complete")
    return opportunities


def run_midday_routine(db, trader, *, now: datetime | None = None):
    """
    Midday check (12:00).
    - Validate complete provider data
    - Check positions for stop-loss/take-profit
    - Save snapshot
    """
    logger.info("☀️ Midday routine starting...")
    
    checked_at = _utc_instant(now)
    db.require_operational_market_data(checked_at)
    
    # Check positions
    trader.check_positions(now=checked_at)
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Midday routine complete")


def run_eod_routine(db, analyzer, trader):
    """
    End of day routine (17:30).
    - Daily performance log
    - Day analysis
    - Update prospects
    """
    logger.info("🌆 End of day routine starting...")
    
    # Log daily performance
    trader.log_daily_performance()
    
    # Run day analysis
    day_stats = analyzer.analyze_day()
    
    # Update prospects
    analyzer.update_prospects()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ End of day routine complete")
    return day_stats


def run_evening_routine(
    db,
    analyzer,
    trader,
    *,
    now: datetime | None = None,
):
    """
    Evening routine (22:00).
    - Validate old hypotheses (learning!)
    - Weekly review (if Friday)
    - Extract learnings
    """
    logger.info("🌙 Evening routine starting...")

    checked_at = _utc_instant(now)
    local_now = stockholm_now(checked_at)
    
    # Validate hypotheses from trades 14+ days old
    validated = trader.validate_hypotheses(
        days_to_check=14,
        now=checked_at,
    )
    
    # Weekly review on Fridays
    if local_now.weekday() == 4:
        logger.info("📝 Friday - running weekly review...")
        trader.run_weekly_review(now=checked_at)
    
    # Extract learnings
    trader.extract_learnings()
    
    outcome_recorder = getattr(
        db,
        "record_candidate_prediction_outcomes",
        None,
    )
    if callable(outcome_recorder):
        labelled = outcome_recorder(evaluated_at=checked_at)
        logger.info(
            "candidate_outcomes_labelled phase=evening count=%d",
            labelled,
        )

    # Save snapshot
    db.save_portfolio_snapshot(recorded_at=checked_at)
    db.record_post_close_benchmark_observation(
        observed_at=checked_at,
    )
    
    logger.info("✅ Evening routine complete")
    return validated


def run_full_analysis(db, analyzer):
    """
    Full analysis routine (ad-hoc).
    - Use validated ingested data
    - Full market scan
    - Generate reports
    """
    logger.info("🔬 Full analysis starting...")
    
    # Morning briefing
    briefing = analyzer.generate_morning_briefing()
    print("\n" + briefing + "\n")
    
    # Find all opportunities
    opportunities = analyzer.find_opportunities()
    
    print("\n📊 Top Opportunities:")
    print("=" * 60)
    
    for i, opp in enumerate(opportunities[:10], 1):
        print(f"\n{i}. {opp['ticker']} ({opp['name']})")
        print(f"   Confidence: {opp['confidence']:.0f}%")
        print(f"   Price: {opp['current_price']:.2f} SEK")
        print(f"   Thesis: {opp['thesis']}")
        print(f"   Entry: {opp['entry_trigger']}")
    
    # Update prospects
    analyzer.update_prospects()
    
    # Save snapshot
    db.save_portfolio_snapshot()
    
    logger.info("✅ Full analysis complete")


if __name__ == "__main__":
    main()
