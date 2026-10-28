"""Calendar-based household task series; every occurrence is an independent task."""
import datetime
import hashlib

HORIZON_DAYS = 14


def create(c, fid, user, data):
    import domain as d
    d.member(c, fid, user, ('dad',))
    frequency = data.get('repeat')
    every = data.get('repeat_every', 1)
    if frequency not in ('daily', 'weekly') or type(every) is not int or not 1 <= every <= 30:
        raise d.Problem(400, '请选择按天或按周重复，间隔为 1—30')
    current = d.today(d.get(c, 'family', fid).get('timezone', 'Asia/Shanghai'))
    start = d.date(data.get('due_date'))
    if start < current:
        raise d.Problem(400, '重复任务的首次日期需为家庭时区的今天或以后')
    until = d.date(data['repeat_until']) if data.get('repeat_until') else None
    if until and until < start:
        raise d.Problem(400, '重复结束日期不能早于首次日期')
    assignee = data.get('assignee') or user
    d.member(c, fid, assignee)
    note = data.get('note', '')
    if not isinstance(note, str) or len(note) > 500:
        raise d.Problem(400, '任务说明最多 500 字')
    series = {'id': d.uid(), 'title': d.text(data.get('title'), 100), 'note': note,
              'assignee': assignee, 'frequency': frequency, 'every': every,
              'start_date': str(start), 'until_date': str(until) if until else None,
              'status': 'active', 'created_by': user, 'created_at': d.now()}
    d.put(c, 'task_series', series, fid)
    created = generate(c, fid, series)
    if assignee != user:
        d.notify(c, fid, assignee, '你收到一项重复家务安排', series['id'])
    d.audit(c, fid, user, 'task_series.created', {'id': series['id']})
    return {'id': series['id'], 'series_id': series['id'], 'created_occurrences': created}


def stop(c, fid, series, actor, reason='user_stopped'):
    import domain as d
    if series['status'] == 'stopped':
        return {'id': series['id'], 'cancelled_occurrences': 0}
    cancelled = 0
    for task in d.rows(c, 'task', fid):
        if task.get('series_id') != series['id'] or task['status'] in ('done', 'skipped'):
            continue
        if task['status'] == 'cancelled':
            if not task.get('series_stopped'):
                task['series_stopped'] = True
                d.put(c, 'task', task, fid)
            continue
        for approval in d.rows(c, 'approval', fid):
            if approval['resource'] == 'task' and approval['target'] == task['id'] and approval['status'] in ('pending', 'cancelled', 'expired'):
                d.cancel_notifications(c, fid, approval['id'])
        d.invalidate_approvals(c, fid, 'task', task['id'])
        d.reset_reminder(c, fid, task)
        task.update(status='cancelled', series_stopped=True)
        d.put(c, 'task', task, fid)
        cancelled += 1
    series.update(status='stopped', stopped_at=d.now(), stopped_by=actor, stopped_reason=reason)
    d.put(c, 'task_series', series, fid)
    d.cancel_notifications(c, fid, series['id'])
    d.audit(c, fid, actor, 'task_series.stopped', {'id': series['id'], 'reason': reason})
    return {'id': series['id'], 'cancelled_occurrences': cancelled}


def stop_for_member(c, fid, user, actor, reason):
    import domain as d
    for series in d.rows(c, 'task_series', fid):
        if series['assignee'] == user and series['status'] != 'stopped':
            stop(c, fid, series, actor, reason)


def generate(c, fid, series):
    import domain as d
    if series['status'] != 'active':
        return 0
    active = c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',
                       (fid, series['assignee'])).fetchone()
    if not active:
        stop(c, fid, series, 'system', 'assignee_inactive')
        return 0
    current = d.today(d.get(c, 'family', fid).get('timezone', 'Asia/Shanghai'))
    start = d.date(series['start_date'])
    until = d.date(series['until_date']) if series.get('until_date') else None
    if until and current > until:
        series.update(status='finished', finished_at=d.now())
        d.put(c, 'task_series', series, fid)
        return 0
    step = series['every'] * (7 if series['frequency'] == 'weekly' else 1)
    elapsed = max(0, (current - start).days)
    index = (elapsed + step - 1) // step
    last = current + datetime.timedelta(days=HORIZON_DAYS)
    if until:
        last = min(last, until)
    created = 0
    # Bound the loop by the local calendar horizon; never backfill an outage.
    while start <= last and index * step <= (last - start).days:
        scheduled = start + datetime.timedelta(days=index * step)
        key = series['id'] + ':' + str(scheduled)
        task_id = 'repeat-' + hashlib.sha256(key.encode()).hexdigest()[:24]
        exists = c.execute("SELECT 1 FROM records WHERE kind='task' AND id=?", (task_id,)).fetchone()
        if not exists:
            d.put(c, 'task', {'id': task_id, 'title': series['title'], 'note': series['note'],
                             'type': '重复家务', 'assignee': series['assignee'], 'status': 'todo',
                             'due_date': str(scheduled), 'series_id': series['id'], 'series_date': str(scheduled),
                             'pregnancy_id': None, 'stage': None, 'private_health_anchor': False,
                             'created_at': d.now()}, fid)
            created += 1
        index += 1
    return created


def sync(c, fid):
    import domain as d
    for series in d.rows(c, 'task_series', fid):
        generate(c, fid, series)
