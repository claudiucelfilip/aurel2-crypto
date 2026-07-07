"""Scheduling + state-restore tests for the live daemon.

Covers the missed-Monday catch-up fix: a rebalance must run on Mondays and,
if a scheduled Monday was missed (daemon down), catch up rather than wait a
full week — and the timer must survive restarts.
"""

from datetime import date, datetime, timedelta

from aurel2_crypto.live.daemon import CryptoDaemon


class _Sched:
    """Minimal stand-in exposing only the schedule method under test.

    Avoids constructing a full daemon (which needs a live broker); the method
    depends solely on `now` and `_last_momentum_check`.
    """

    def __init__(self, last_momentum_check):
        self._last_momentum_check = last_momentum_check

    _should_check_momentum = CryptoDaemon._should_check_momentum


def _at(d: date, hour: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, hour)


# 2026-07-06 is a Monday; 07-07 Tue ... 07-13 next Monday.
MON = date(2026, 7, 6)
TUE = date(2026, 7, 7)
NEXT_MON = date(2026, 7, 13)


def test_runs_on_monday_when_not_yet_checked():
    assert _Sched(last_momentum_check=None)._should_check_momentum(_at(MON)) is True


def test_skips_monday_once_already_checked_today():
    assert _Sched(last_momentum_check=MON)._should_check_momentum(_at(MON)) is False


def test_skips_normal_tuesday_after_monday_check():
    # Checked Monday, now Tuesday — no catch-up needed, wait for next Monday.
    assert _Sched(last_momentum_check=MON)._should_check_momentum(_at(TUE)) is False


def test_catches_up_missed_monday_on_tuesday():
    # Last check was the PREVIOUS Monday (7 days ago); this Monday was missed.
    # Back up on Tuesday -> catch up now instead of waiting to 07-13.
    stale = TUE - timedelta(days=7)  # 2026-06-30 (a Tuesday), 7 days stale
    assert _Sched(last_momentum_check=stale)._should_check_momentum(_at(TUE)) is True


def test_no_catchup_when_check_is_recent_midweek():
    # 3 days stale, midweek, not a Monday -> hold.
    recent = TUE - timedelta(days=3)
    assert _Sched(last_momentum_check=recent)._should_check_momentum(_at(TUE)) is False


def test_first_ever_run_triggers_immediately_any_day():
    # Fresh daemon, no prior check -> evaluate now rather than wait for Monday.
    assert _Sched(last_momentum_check=None)._should_check_momentum(_at(TUE)) is True


def test_next_monday_runs_even_if_last_check_six_days_ago():
    # Checked last Tuesday (6 days, <7), but it's Monday again -> normal cadence.
    last = NEXT_MON - timedelta(days=6)  # 2026-07-07 Tue
    assert _Sched(last_momentum_check=last)._should_check_momentum(_at(NEXT_MON)) is True
