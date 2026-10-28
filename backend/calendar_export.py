"""Manual RFC 5545 all-day export of the actor's currently visible schedules."""
import datetime
import hashlib


def escape(value):
    text = str(value).replace('\r\n', '\n').replace('\r', '\n')
    text = ''.join(c for c in text if c == '\n' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(line):
    # RFC 5545 limits physical lines to 75 octets, including a continuation space.
    lines, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            lines.append(current)
            current = ' '
        current += char
    return '\r\n'.join(lines + [current])


def export(c, user, fid, start=None, end=None):
    import domain as d
    snapshot = d.snapshot(c, user, fid)
    first = d.date(start) if start is not None else d.date(snapshot['calendar_date'])
    try:
        last = d.date(end) if end is not None else first + datetime.timedelta(days=90)
    except OverflowError:
        raise d.Problem(400, '日期超出可导出范围')
    if last < first or (last - first).days > 365 or last == datetime.date.max:
        raise d.Problem(400, '请选择起止顺序正确且不超过 366 天的日历范围')
    entries = []
    for kind, records, field, statuses in (
        ('task', snapshot['tasks'], 'due_date', ('todo', 'in_progress', 'pending')),
        ('appointment', snapshot['appointments'], 'date', ('scheduled',)),
    ):
        for obj in records:
            if obj['status'] not in statuses or not obj.get(field):
                continue
            due = d.date(obj[field])
            if first <= due <= last:
                entries.append((due, kind, obj))
    if len(entries) > 2000:
        raise d.Problem(400, '此范围事项过多，请缩小日期范围后导出')
    stamp = datetime.datetime.fromtimestamp(d.now(), datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Baba//Family Calendar//ZH', 'CALSCALE:GREGORIAN']
    for due, kind, obj in sorted(entries, key=lambda entry: (entry[0], entry[1], entry[2]['id'])):
        opaque = hashlib.sha256((fid + ':' + kind + ':' + obj['id']).encode()).hexdigest()
        until = due + datetime.timedelta(days=1)
        label = '任务' if kind == 'task' else '预约'
        lines.extend(['BEGIN:VEVENT', 'UID:' + opaque + '@baba.local', 'DTSTAMP:' + stamp,
                      'SEQUENCE:' + str(obj['version']),
                      'DTSTART;VALUE=DATE:' + due.isoformat().replace('-', ''),
                      'DTEND;VALUE=DATE:' + until.isoformat().replace('-', ''),
                      'SUMMARY:' + escape('[' + label + '] ' + obj['title']),
                      'CLASS:PRIVATE', 'TRANSP:TRANSPARENT', 'STATUS:CONFIRMED', 'END:VEVENT'])
    lines.append('END:VCALENDAR')
    d.audit(c, fid, user, 'calendar.export', {'start': str(first), 'end': str(last), 'count': len(entries)})
    return {'filename': 'baba-calendar-' + str(first) + '-' + str(last) + '.ics',
            'content': '\r\n'.join(fold(line) for line in lines) + '\r\n',
            'event_count': len(entries), 'start': str(first), 'end': str(last)}
