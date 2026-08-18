# Diagnosis: SCHEDULED_ROUTINE_MISSED Latching Issue

## Problem

On 2026-08-10, the opening routine missed its 20-minute grace window at ~9:25 AM Stockholm time.
This created a `SCHEDULED_ROUTINE_MISSED` PAGE alert with alert_key `scheduled:2026-08-10:open`.

The alert stayed OPEN for the rest of the day, blocking trading even after all other services
became healthy. The issue is that once a trading routine (open/midday) misses its window:

1. It cannot be recovered (intentionally - to avoid stale trade replay)
2. The alert stays OPEN all day on that session date
3. Trading stays BLOCKED even though services are otherwise healthy

## Current Behavior

- `missed_routines()` returns routines where `current_time - scheduled_time > grace_period`
- For "open" routine: scheduled 9:00, grace 20 min, so missed after 9:20
- Once missed at 9:25, the alert is created with key `scheduled:2026-08-10:open`
- The alert stays in the missed list ALL DAY (because current time - 9:00 is always > 20 min)
- Therefore `sync_operational_alerts()` keeps it in the normalized dict all day
- It only auto-RESOLVES the NEXT DAY when `missed_routines()` checks 2026-08-11's routines

## Expected Behavior

A missed trading routine (open/midday) should:
1. Create a PAGE alert when it first misses its grace window (evidence)
2. Auto-RESOLVE after a reasonable recovery window has passed
3. Not permanently block trading for the entire day

Non-trading routines (morning/close/evening) have recovery windows and should behave differently.

## Root Cause

The logic in `missed_routines()` only checks if a routine is missed, not whether it should
continue to block operations. Once a routine misses its grace period, it stays in the missed
list until the next day.

## Solution

Modify the operational monitor to auto-RESOLVE trading routine alerts (open/midday) after
a bounded recovery window, while keeping the append-only evidence that the routine missed.

The alert should exist for evidence but not block trading after the recovery window passes.
