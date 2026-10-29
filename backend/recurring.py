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
              'status': 'active', 'rule_revision': 1, 'created_by': user, 'created_at': d.now()}
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
    for task in d.rows(c, 'task', fid):
        if task.get('series_id') and not task.get('series_stopped') and task['assignee'] == user and (task['status'] in ('todo', 'in_progress', 'pending') or task.get('series_locked') == 'paused'):
            lock_task(c, fid, task, 'assignee_inactive')


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
                             'series_revision': series.get('rule_revision', 1),
                             'pregnancy_id': None, 'stage': None, 'private_health_anchor': False,
                             'created_at': d.now()}, fid)
            created += 1
        index += 1
    return created


def sync(c, fid):
    import domain as d
    for series in d.rows(c, 'task_series', fid):
        generate(c, fid, series)


CONFIG = ('title', 'note', 'assignee', 'frequency', 'every', 'start_date', 'until_date')


def lock_task(c, fid, task, reason):
    """Cancel execution and confirmations, retaining the record for review."""
    import domain as d
    for approval in d.rows(c, 'approval', fid):
        if approval['resource'] == 'task' and approval['target'] == task['id']:
            d.cancel_notifications(c, fid, approval['id'])
    d.invalidate_approvals(c, fid, 'task', task['id'])
    d.reset_reminder(c, fid, task)
    if reason == 'paused':
        task['series_resume_status'] = 'in_progress' if task['status'] == 'pending' else task['status']
    else:
        task.pop('series_resume_status', None)
    task.update(status='cancelled', series_locked=reason)
    return d.put(c, 'task', task, fid)


def matches(series, day):
    import domain as d
    start = d.date(series['start_date'])
    until = d.date(series['until_date']) if series.get('until_date') else None
    step = series['every'] * (7 if series['frequency'] == 'weekly' else 1)
    return day >= start and (not until or day <= until) and (day - start).days % step == 0


def update(c, fid, series, actor, data):
    import domain as d
    if series['status'] == 'stopped':
        raise d.Problem(409, '已停止的安排不能修改，请新建重复安排')
    current = d.today(d.get(c, 'family', fid).get('timezone', 'Asia/Shanghai'))
    frequency, every = data.get('repeat', series['frequency']), data.get('repeat_every', series['every'])
    if frequency not in ('daily', 'weekly') or type(every) is not int or not 1 <= every <= 30:
        raise d.Problem(400, '请选择按天或按周重复，间隔为 1—30')
    start = d.date(data.get('due_date', series['start_date']))
    if str(start) != series['start_date'] and start < current:
        raise d.Problem(400, '修改首次日期时需选择家庭时区的今天或以后')
    until_value = data.get('repeat_until', series.get('until_date'))
    until = d.date(until_value) if until_value else None
    if until and until < start:
        raise d.Problem(400, '重复结束日期不能早于首次日期')
    assignee = data.get('assignee', series['assignee'])
    if not isinstance(assignee, str) or not assignee:
        raise d.Problem(400, '请选择有效负责人')
    d.member(c, fid, assignee)
    note = data.get('note', series['note'])
    if not isinstance(note, str) or len(note) > 500:
        raise d.Problem(400, '任务说明最多 500 字')
    new = dict(series, title=d.text(data.get('title', series['title']), 100), note=note,
               assignee=assignee, frequency=frequency, every=every,
               start_date=str(start), until_date=str(until) if until else None)
    result = {'id': series['id'], 'updated_occurrences': 0, 'cancelled_occurrences': 0,
              'created_occurrences': 0, 'preserved_occurrences': 0}
    if all(new.get(key) == series.get(key) for key in CONFIG):
        return result
    if series['status'] == 'finished' and until and until < current:
        raise d.Problem(409, '安排已到结束日期，请先延长或清除结束日期')
    new.update(rule_revision=series.get('rule_revision', 1) + 1, updated_at=d.now(), updated_by=actor)
    if new['status'] == 'finished':
        new['status'] = 'active'
        new.pop('finished_at', None)
    for task in d.rows(c, 'task', fid):
        if task.get('series_id') != series['id']:
            continue
        paused = task.get('series_locked') == 'paused'
        rule_cancelled = task.get('series_locked') == 'rule_changed'
        original_status = task.get('series_resume_status') if paused else task['status']
        overridden = task.get('user_override') or task.get('series_override') or (
            not rule_cancelled and any(task.get(key) != series[key] for key in ('title', 'note', 'assignee')))
        eligible = (original_status == 'todo' or rule_cancelled) and not overridden and not task.get('series_stopped')
        eligible = eligible and d.date(task['series_date']) >= current and task.get('due_date') and d.date(task['due_date']) >= current
        if not eligible:
            result['preserved_occurrences'] += 1
            continue
        if not matches(new, d.date(task['series_date'])):
            if not rule_cancelled:
                lock_task(c, fid, task, 'rule_changed')
                result['cancelled_occurrences'] += 1
            continue
        # Reuse the stable original-date ID; completed/manual-cancelled slots never reappear.
        d.reset_reminder(c, fid, task)
        task.update(title=new['title'], note=new['note'], assignee=new['assignee'],
                    series_revision=new['rule_revision'])
        if new['status'] == 'paused':
            task.update(status='cancelled', series_locked='paused', series_resume_status='todo')
        else:
            task['status'] = 'todo'
            task.pop('series_locked', None)
            task.pop('series_resume_status', None)
        d.put(c, 'task', task, fid)
        result['updated_occurrences'] += 1
    d.put(c, 'task_series', new, fid)
    result['created_occurrences'] = generate(c, fid, new)
    d.cancel_notifications(c, fid, series['id'])
    if new['status'] == 'active' and new['assignee'] != actor:
        d.notify(c, fid, new['assignee'], '有一项重复家务安排已更新', new['id'])
    d.audit(c, fid, actor, 'task_series.updated', {'id': new['id'], 'revision': new['rule_revision'],
            'before': {key: series.get(key) for key in CONFIG}, 'after': {key: new.get(key) for key in CONFIG},
            'counts': {key: value for key, value in result.items() if key != 'id'}})
    return result


def pause(c, fid, series, actor):
    import domain as d
    if series['status'] == 'paused':
        return {'id': series['id'], 'paused_occurrences': 0}
    if series['status'] != 'active':
        raise d.Problem(409, '只有进行中的重复安排可以暂停')
    count = 0
    for task in d.rows(c, 'task', fid):
        if task.get('series_id') == series['id'] and task['status'] in ('todo', 'in_progress', 'pending'):
            lock_task(c, fid, task, 'paused')
            count += 1
    series.update(status='paused', paused_at=d.now(), paused_by=actor)
    d.put(c, 'task_series', series, fid)
    d.cancel_notifications(c, fid, series['id'])
    d.audit(c, fid, actor, 'task_series.paused', {'id': series['id'], 'count': count})
    return {'id': series['id'], 'paused_occurrences': count}


def resume(c, fid, series, actor):
    import domain as d
    if series['status'] == 'active':
        return {'id': series['id'], 'resumed_occurrences': 0, 'created_occurrences': 0}
    if series['status'] != 'paused':
        raise d.Problem(409, '只有已暂停的安排可以恢复，已停止安排需新建')
    current = d.today(d.get(c, 'family', fid).get('timezone', 'Asia/Shanghai'))
    if series.get('until_date') and d.date(series['until_date']) < current:
        raise d.Problem(409, '已过结束日期，请先修改规则延长日期再恢复')
    d.member(c, fid, series['assignee'])
    count = 0
    for task in d.rows(c, 'task', fid):
        if task.get('series_id') != series['id'] or task.get('series_locked') != 'paused':
            continue
        active = c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1', (fid, task['assignee'])).fetchone()
        if not active or (task.get('due_date') and d.date(task['due_date']) < current):
            lock_task(c, fid, task, 'pause_elapsed' if active else 'assignee_inactive')
            continue
        status = task.pop('series_resume_status', 'todo')
        task.pop('series_locked', None)
        task['status'] = status if status in ('todo', 'in_progress') else 'todo'
        d.reset_reminder(c, fid, task)
        d.put(c, 'task', task, fid)
        count += 1
    series.update(status='active', resumed_at=d.now(), resumed_by=actor)
    d.put(c, 'task_series', series, fid)
    created = generate(c, fid, series)
    if series['assignee'] != actor:
        d.notify(c, fid, series['assignee'], '有一项重复家务安排已恢复', series['id'])
    d.audit(c, fid, actor, 'task_series.resumed', {'id': series['id'], 'count': count})
    return {'id': series['id'], 'resumed_occurrences': count, 'created_occurrences': created}
