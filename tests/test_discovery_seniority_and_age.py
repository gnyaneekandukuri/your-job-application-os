from datetime import datetime, timedelta, timezone

from app.discovery_preferences import matches
from app.discovery_screening import TOO_SENIOR
from app.job_retention import age_state

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def test_titles_above_senior_pm_are_detected_without_catching_target_levels():
    for title in ('Staff Product Manager', 'Principal Product Manager, AI', 'Director of Product',
                  'Head of Product', 'VP, Product', 'Group Product Manager'):
        assert matches(title, TOO_SENIOR), title
    for title in ('Product Manager', 'Senior Product Manager', 'Product Manager, Staffing Platform',
                  'Technical Product Manager'):
        assert not matches(title, TOO_SENIOR), title


def test_undated_listings_age_from_first_discovery():
    seen = lambda days: (NOW - timedelta(days=days)).isoformat()
    assert age_state({'first_seen': seen(1)}, NOW) == 'unknown'
    assert age_state({'first_seen': seen(5)}, NOW) == 'older_than_four_days'
    assert age_state({}, NOW) == 'unknown'
    assert age_state({'posted_at': (NOW - timedelta(days=1)).isoformat(), 'first_seen': seen(9)}, NOW) == 'active'
