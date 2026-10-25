"""Application-only outcome metrics; no provider calls or inferred status from notes."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

# None means closed without a recorded outcome; never imply a rejection or silence.
APPLICATION_STATUS_TO_FUNNEL_BUCKET = {
    'Applied': 'No response',
    'Rejected': 'Rejected',
    'Interviewing': 'Interview',
    'Offer': 'Interview',
    'Withdrawn': None,
}
APPROACHES = ('Tailored resume', 'Untailored resume', 'Attack Hard', 'Casual')


def precise_timestamp(value):
    """Only absolute timestamps with a time component; naive values are UTC."""
    if not isinstance(value, str) or len(value) <= 10 or value[10] not in ('T', ' '):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def application_stats(records, priorities=None, posted_times=None):
    priorities = priorities or {}
    posted_times = posted_times or {}
    timing = Counter()
    reasons = Counter(no_posted_time=0, date_only_posted_time=0, applied_before_posted=0)
    timing_weeks = {}
    buckets = Counter()
    groups = {name: Counter() for name in APPROACHES}
    weeks = Counter()
    unknown = Counter()
    for record in records:
        status = record.get('status', '')
        bucket = APPLICATION_STATUS_TO_FUNNEL_BUCKET.get(status)
        if status not in APPLICATION_STATUS_TO_FUNNEL_BUCKET:
            unknown[status] += 1
        buckets[bucket] += 1
        created = precise_timestamp(record.get('created_at'))
        tailored = bool(record.get('resume_revision') or record.get('resume_snapshot') or record.get('tailoring_revision'))
        approach = 'Tailored resume' if tailored else 'Untailored resume'
        snapshot = record.get('job_snapshot') or {}
        assessment = record.get('assessment_snapshot') or {}
        priority = priorities.get(record['job_id']) or snapshot.get('priority') or snapshot.get('recommendation') or assessment.get('recommendation')
        priority = {'ATTACK_HARD': 'Attack Hard', 'CASUAL': 'Casual'}.get(priority, priority)
        for name in [approach] + ([priority] if priority in ('Attack Hard', 'Casual') else []):
            groups[name]['applied'] += 1
            groups[name]['screens'] += bucket in ('Screen', 'Interview')
            groups[name]['rejected'] += bucket == 'Rejected'
        applied = date.fromisoformat(record['applied_date'])
        week = applied - timedelta(days=applied.weekday())
        weeks[week] += 1
        posted_value = posted_times.get(record['job_id']) or snapshot.get('posted_at')
        posted = precise_timestamp(posted_value)
        reason = None
        if not posted:
            try:
                date.fromisoformat(posted_value)
                reason = 'date_only_posted_time'
            except (ValueError, TypeError):
                reason = 'no_posted_time'
        elif created is None:
            reason = 'no_precise_applied_time'
        elif created < posted:
            reason = 'applied_before_posted'
        if reason:
            reasons[reason] += 1
        for counts in (timing, timing_weeks.setdefault(week, Counter())):
            counts['unknown'] += reason is not None
            counts['measurable'] += reason is None
            counts['within_6h'] += reason is None and created - posted <= timedelta(hours=6)
    total = len(records)
    percent = lambda value, denominator: round(value / denominator * 100, 1) if denominator else 0
    weekly = []
    if weeks:
        week = min(weeks)
        while week <= max(weeks):
            weekly.append({'week_start': week.isoformat(), 'count': weeks[week]})
            week += timedelta(days=7)
    timing_weekly = []
    for row in weekly:
        week = date.fromisoformat(row['week_start'])
        timing_weekly.append(dict(week_start=row['week_start'], **{
            key: timing_weeks.get(week, Counter())[key] for key in ('within_6h', 'measurable', 'unknown')}))
    return {
        'primary': {
            'applied_within_6h': dict(within_6h=timing['within_6h'], measurable=timing['measurable'],
                                     unknown=timing['unknown'], unknown_reasons=dict(reasons),
                                     rate=percent(timing['within_6h'], timing['measurable']), weekly=timing_weekly),
        },
        'funnel': [{'label': name, 'count': total if name == 'Applied' else buckets[name],
                    'percent': percent(total if name == 'Applied' else buckets[name], total)}
                   for name in ('Applied', 'Rejected', 'No response', 'Screen', 'Interview')],
        'approaches': [{'label': name, 'applied': group['applied'], 'screens': group['screens'],
                        'screen_rate': percent(group['screens'], group['applied']),
                        'rejection_rate': percent(group['rejected'], group['applied'])}
                       for name, group in groups.items()],
        'weekly': weekly,
        'withdrawn': sum(r.get('status') == 'Withdrawn' for r in records),
        'unknown_statuses': dict(unknown),
    }


class SavedPriorities:
    """Remembers each applied job's saved recommendation for Stats.

    Building a recommendation reads the whole analysis for that job, so a cold
    pass over every application takes many seconds. Known jobs are served from
    memory and refreshed in the background once stale; only jobs not seen
    before are looked up during the request.
    """

    def __init__(self, lookup, max_age=600):
        from threading import Lock
        self.lookup = lookup  # job_ids -> {job_id: priority}
        self.max_age = max_age
        self.lock = Lock()
        self.values = {}
        self.updated = 0.0
        self.refreshing = False

    def get(self, job_ids):
        import time
        job_ids = {j for j in job_ids if not j.startswith(('external:', 'opportunity:'))}
        with self.lock:
            cold = not self.values
            missing = job_ids - self.values.keys()
            stale = time.monotonic() - self.updated > self.max_age
        if missing:
            found = self.lookup(missing)
            with self.lock:
                self.values.update(found)
                if cold:
                    self.updated = time.monotonic()
        if stale and not missing:
            self.refresh(job_ids)
        with self.lock:
            return {j: self.values.get(j) for j in job_ids}

    def refresh(self, job_ids):
        """Recompute in the background; readers keep the previous values."""
        import time
        from threading import Thread
        with self.lock:
            if self.refreshing:
                return
            self.refreshing = True
        def run():
            try:
                found = self.lookup(set(job_ids))
                with self.lock:
                    self.values.update(found)
                    self.updated = time.monotonic()
            finally:
                with self.lock:
                    self.refreshing = False
        Thread(target=run, daemon=True, name='stats-priorities').start()
