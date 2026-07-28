from datetime import datetime
from zoneinfo import ZoneInfo

from ambientwill.gates import in_quiet_hours
from ambientwill.models import QuietWindow

SGT = ZoneInfo("Asia/Singapore")


def test_quiet_window_is_left_closed_right_open() -> None:
    windows = (QuietWindow("09:00", "10:00"),)

    assert in_quiet_hours(datetime(2026, 1, 1, 9, 0, tzinfo=SGT), windows)
    assert not in_quiet_hours(datetime(2026, 1, 1, 10, 0, tzinfo=SGT), windows)


def test_cross_midnight_quiet_window() -> None:
    windows = (QuietWindow("23:00", "07:00"),)

    assert in_quiet_hours(datetime(2026, 1, 1, 23, 0, tzinfo=SGT), windows)
    assert in_quiet_hours(datetime(2026, 1, 2, 6, 59, tzinfo=SGT), windows)
    assert not in_quiet_hours(datetime(2026, 1, 2, 7, 0, tzinfo=SGT), windows)
    assert not in_quiet_hours(datetime(2026, 1, 1, 12, 0, tzinfo=SGT), windows)


def test_non_utc_timezone_is_used() -> None:
    windows = (QuietWindow("23:00", "07:00"),)
    utc = ZoneInfo("UTC")

    assert in_quiet_hours(datetime(2026, 1, 1, 16, 0, tzinfo=utc), windows, SGT)
