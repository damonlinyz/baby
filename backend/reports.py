"""Live weekly facts and a deterministic briefing built from an authorized snapshot."""
import datetime as dt
from zoneinfo import ZoneInfo

OPEN_TASKS = ('todo', 'in_progress', 'pending')


def monday(day):
    return day - dt.timedelta(days=day.weekday())


def build(snapshot, week=None):
    import domain as d
    current = d.date(snapshot['calendar_date'])
    start = monday(d.date(week) if week is not None else current)
    this_week = monday(current)
    if start > this_week:
        raise d.Problem(400, '未来周尚未发生，请选择本周或历史周')
    end = start + dt.timedelta(days=6)
    zone = snapshot['family'].get('timezone', 'Asia/Shanghai')
    timezone = ZoneInfo(zone)
    members = {m['id']: m['name'] for m in snapshot['members']}
    missing = False
    completed = {'tasks': [], 'items': []}
    now = d.now()
    for key, title in (('tasks', 'title'), ('items', 'name')):
        for obj in snapshot[key]:
            if obj['status'] != 'done':
                continue
            timestamp = obj.get('completed_at')
            if type(timestamp) not in (int, float):
                missing = True
                continue
            try:
                finished = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).astimezone(timezone).date()
                if timestamp > now:
                    raise ValueError('future completion')
            except (ValueError, OverflowError, OSError):
                missing = True
                continue
            if start <= finished <= end:
                entry = {'id': obj['id'], 'title': obj[title], 'completed_date': str(finished),
                         'assignee_name': members.get(obj['assignee'], '已离开的家人')}
                if key == 'items':
                    entry['quantity'] = obj['quantity']
                completed[key].append(entry)
        completed[key].sort(key=lambda entry: (entry['completed_date'], entry['id']))
    candidates = []
    p = snapshot.get('pregnancy')
    # Only visible state may affect the briefing; hidden baby/health status is never consulted.
    enabled = start == this_week and not (p and not p.get('restricted') and
              (p.get('paused') or p.get('archived') or p.get('state') == 'ended'))
    if p and any(b.get('pregnancy_id') == p['id'] and b['status'] in ('unconfirmed', 'hospitalized', 'lost')
                 for b in snapshot.get('babies', [])):
        enabled = False
    if enabled:
        for obj in snapshot['tasks']:
            if obj['status'] not in OPEN_TASKS:
                continue
            if p and p.get('restricted') and obj.get('pregnancy_id'):
                continue
            due = obj.get('due_date')
            if due and start <= d.date(due) <= end:
                candidates.append((0, due, 1, obj['id'], brief_entry(obj, 'task', due, members,
                                   '本周已安排' if due >= str(current) else '本周日期已过，仍未关闭')))
            elif not due:
                candidates.append((1, '', 0, obj['id'], brief_entry(obj, 'task', None, members, '尚未定日期的家庭任务')))
        for obj in snapshot['appointments']:
            if obj['status'] == 'scheduled' and start <= d.date(obj['date']) <= end:
                candidates.append((0, obj['date'], 0, obj['id'],
                                   {'id': obj['id'], 'kind': 'appointment', 'title': obj['title'],
                                    'due_date': obj['date'], 'status': obj['status'],
                                    'assignee_name': members.get(obj['owner'], '已离开的家人'),
                                    'reason': '本周已录入预约' if obj['date'] >= str(current) else '本周预约日期已过，请核对状态'}))
        for obj in snapshot['items']:
            if obj['status'] in OPEN_TASKS:
                candidates.append((2, '', {'必备': 0, '建议': 1, '可选': 2}.get(obj['level'], 1), obj['id'],
                                   brief_entry(obj, 'item', None, members, '尚待准备的物品（未设日期）')))
    entries = [entry[-1] for entry in sorted(candidates, key=lambda entry: entry[:4])[:3]]
    # Generic, published non-clinical references only; never invent medical arrangements.
    topics = ['备产准备', '家庭协作', '陪伴与沟通', '自我照顾'] if any(e['kind'] == 'item' for e in entries) else ['家庭协作', '陪伴与沟通', '自我照顾', '备产准备']
    knowledge = sorted((k for k in snapshot['knowledge'] if not k.get('clinical')),
                       key=lambda k: (topics.index(k['topic']) if k['topic'] in topics else len(topics), k['id']))[:2] if entries else []
    return {'week_start': str(start), 'week_end': str(end), 'timezone': zone, 'as_of': str(current),
            'is_current': start == this_week, 'previous_week': str(start - dt.timedelta(days=7)) if start > dt.date.min + dt.timedelta(days=6) else None,
            'next_week': str(start + dt.timedelta(days=7)) if start < this_week else None,
            'scope': 'own' if snapshot['role'] == 'member' else 'visible_family',
            'counts': {key: len(entries) for key, entries in completed.items()}, 'completed': completed,
            'missing_completion_dates': missing,
            'briefing': {'enabled': enabled, 'entries': entries,
                         'knowledge': [{'id': k['id'], 'title': k['title'], 'topic': k['topic'],
                                        'content_version': k['content_version'], 'source': k['source']} for k in knowledge]}}


def brief_entry(obj, kind, due, members, reason):
    return {'id': obj['id'], 'kind': kind, 'title': obj['name'] if kind == 'item' else obj['title'], 'due_date': due,
            'status': obj['status'], 'assignee_name': members.get(obj['assignee'], '已离开的家人'), 'reason': reason}
