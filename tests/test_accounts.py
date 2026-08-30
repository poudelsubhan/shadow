from datetime import date, datetime

import accounts


def test_verify_accepts_matching_factors():
    assert accounts.verify("Jordan Avery", "4417", {"year": 1988, "month": 3, "day": 2})
    assert accounts.verify("Jordan Avery", "4417", "1988-03-02")
    assert accounts.verify("jordan avery", "xx4417", date(1988, 3, 2))


def test_verify_rejects_any_mismatch():
    assert not accounts.verify("Jordan Avery", "4418", "1988-03-02")
    assert not accounts.verify("Jordan Avery", "4417", "1988-03-03")
    assert not accounts.verify("Nobody At All", "4417", "1988-03-02")
    assert not accounts.verify(None, "4417", "1988-03-02")
    assert not accounts.verify("Jordan Avery", None, None)


def test_slot_search_returns_three_future_weekday_slots():
    now = datetime(2026, 8, 29, 19, 0)  # a Saturday
    slots = accounts.next_slots("", now=now)
    assert len(slots) == 3
    for s in slots:
        d = datetime.fromisoformat(s)
        assert d > now and d.weekday() < 5


def test_slot_search_honours_keywords():
    now = datetime(2026, 8, 31, 9, 0)  # Monday
    assert all(datetime.fromisoformat(s).hour == 10 for s in accounts.next_slots("morning", now=now))
    assert all(datetime.fromisoformat(s).hour in (14, 16) for s in accounts.next_slots("afternoon", now=now))
    assert all(datetime.fromisoformat(s).weekday() == 2 for s in accounts.next_slots("wednesday", now=now))
