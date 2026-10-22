"""Calendar-only family timeline calculations; event timestamps stay in UTC."""
import calendar
import datetime as dt
import re
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = 'Asia/Shanghai'
SOURCES = ('lmp_estimate', 'clinician_entered', 'user_entered')


def parse(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期必须使用 YYYY-MM-DD 格式')
    return dt.date.fromisoformat(value)


def local_today(timezone=DEFAULT_TIMEZONE, instant=None):
    instant = instant or dt.datetime.now(dt.timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return instant.astimezone(ZoneInfo(timezone)).date()


def anniversary(birth, years=1):
    year = birth.year + years
    # Feb 29 anniversaries fall on the last day of February in a common year.
    return birth.replace(year=year, day=min(birth.day, calendar.monthrange(year, birth.month)[1]))


def estimate_edd(lmp):
    return parse(lmp) + dt.timedelta(days=280)


def age(birth, on_date):
    if birth > on_date:
        raise ValueError('出生日期不能在未来')
    months = (on_date.year - birth.year) * 12 + on_date.month - birth.month
    month_day = min(birth.day, calendar.monthrange(on_date.year, on_date.month)[1])
    if on_date.day < month_day:
        months -= 1
    return {'days': (on_date - birth).days, 'months': months,
            'first_birthday': anniversary(birth).isoformat(),
            'reached_first_birthday': on_date >= anniversary(birth)}


def timeline(p, timezone=DEFAULT_TIMEZONE, on_date=None):
    current = on_date or local_today(timezone)
    result = {'as_of': current.isoformat(), 'timezone': timezone,
              'title': '日期待确定', 'description': '可以先从不依赖孕周的准备开始。',
              'phase': 'dating', 'progress': 0}
    if p['state'] == 'pregnant' and p.get('edd'):
        remaining = (parse(p['edd']) - current).days
        days = 280 - remaining
        if days < 0:
            raise ValueError('预产期对应的孕龄不能为负数')
        weeks, extra = divmod(days, 7)
        source = '用户转录医生确认日期' if p.get('dating_source') == 'clinician_entered' else '末次月经估算日期' if p.get('dating_source') == 'lmp_estimate' else '用户录入日期'
        result.update(gestational_days=days, weeks=weeks, days=extra,
                      edd_days_remaining=remaining, phase='early' if days < 98 else 'middle' if days < 196 else 'late',
                      title=f'{weeks} 周 + {extra} 天', progress=min(100, days / 280 * 100),
                      description=f'距预产期 {remaining} 天 · {source}' if remaining > 0 else '已到预产期，请按医生安排；系统不会自动切换为已出生')
    elif p['state'] == 'postpartum' and p.get('birth_date'):
        baby_age = age(parse(p['birth_date']), current)
        days = baby_age['days']
        result.update(postpartum_days=days, baby_age=baby_age, phase='postpartum_early' if days <= 42 else 'postpartum_later',
                      title=f'宝宝出生 {days} 天', progress=100,
                      description='一起照顾妈妈，也慢慢适应新的生活。' if days <= 42 else '妈妈的恢复与宝宝的成长，同样值得被看见。')
    elif p['state'] == 'ended':
        result.update(phase='ended', title='给彼此，一点时间', description='相关促活已停止。你可以安静地保存、归档这些记录。')
    return result
