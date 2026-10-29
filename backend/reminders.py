"""Station reminders with per-user quiet hours, daily limits, merging and reauthorization."""
import datetime as dt
import re
from zoneinfo import ZoneInfo

DEFAULTS={'external_enabled':False,'enabled':True,'appointment_enabled':True,'approval_enabled':True,'weekly_enabled':False,
          'quiet_enabled':False,'quiet_start':'22:00','quiet_end':'08:00','daily_limit':20,
          'merge_enabled':False,'merge_limit':5,'appointment_days':[3],'task_days':[1]}


def preferences(c,fid,user):
    import domain as d
    raw=next((r for r in d.rows(c,'notification_preferences',fid) if r['user_id']==user),{})
    return dict(DEFAULTS,**raw)


def enabled(p,kind):return p[{'task':'enabled','appointment':'appointment_enabled','approval':'approval_enabled','weekly':'weekly_enabled'}[kind]]


def save(c,fid,user,data):
    import domain as d
    p=preferences(c,fid,user)
    for k in ('external_enabled','enabled','appointment_enabled','approval_enabled','weekly_enabled','quiet_enabled','merge_enabled'):
        if k in data:
            if type(data[k]) is not bool:raise d.Problem(400,'提醒开关需为是或否')
            p[k]=data[k]
    for k in ('quiet_start','quiet_end'):
        if k in data:
            if not isinstance(data[k],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',data[k]):raise d.Problem(400,'静默时间请使用 HH:MM')
            p[k]=data[k]
    if p['quiet_enabled'] and p['quiet_start']==p['quiet_end']:raise d.Problem(400,'静默开始和结束时间不能相同')
    for k,maximum in (('daily_limit',50),('merge_limit',20)):
        if k in data:
            if type(data[k]) is not int or not 1<=data[k]<=maximum:raise d.Problem(400,'提醒数量范围不正确')
            p[k]=data[k]
    for k,maximum in (('appointment_days',14),('task_days',7)):
        if k in data:
            values=data[k]
            if not isinstance(values,list) or not 1<=len(values)<=4 or any(type(v) is not int or not 0<=v<=maximum for v in values) or len(set(values))!=len(values):raise d.Problem(400,'提醒提前天数需为不重复的整数列表')
            p[k]=sorted(values,reverse=True)
    p.update(id=fid+'-'+user,user_id=user)
    d.put(c,'notification_preferences',p,fid)
    if data.get('external_enabled') is False:
        import push_notifications
        push_notifications.invalidate_family(c,fid,user)
    for q in d.rows(c,'reminder',fid):
        if q['recipient']==user and not enabled(p,q['type']) and q['status'] in ('scheduled','sent'):cancel(c,fid,q)
    # Also honor old installations whose task notices predate the queue.
    if not p['enabled']:
        ids={t['id'] for t in d.rows(c,'task',fid)}
        for n in d.rows(c,'notification',fid):
            if n['recipient']==user and n.get('ref') in ids and not n['read']:
                n['cancelled']=True;d.put(c,'notification',n,fid)
    return {'ok':True}


def enqueue(c,fid,id,user,kind,source,ref,title,expires_date=None,**extra):
    import domain as d
    if c.execute("SELECT 1 FROM records WHERE kind IN ('reminder','notification') AND id=?",(id,)).fetchone():return
    if not enabled(preferences(c,fid,user),kind):return
    q={'id':id,'recipient':user,'type':kind,'source':source,'ref':ref,'title':title,
       'status':'scheduled','created_at':d.now(),'expires_date':expires_date,'attempts':0,**extra}
    d.put(c,'reminder',q,fid)


def cancel(c,fid,q):
    import domain as d
    if q['status']!='cancelled':q['status']='cancelled';d.put(c,'reminder',q,fid)
    id=q.get('notification_id',q['id'])
    row=c.execute("SELECT body FROM records WHERE kind='notification' AND id=?",(id,)).fetchone()
    if row:
        import json
        n=json.loads(row['body'])
        if not n['read'] and not n.get('cancelled'):n['cancelled']=True;d.put(c,'notification',n,fid)


def cancel_ref(c,fid,ref):
    import domain as d
    for q in d.rows(c,'reminder',fid):
        if q['ref']==ref and q['status'] in ('scheduled','sent'):cancel(c,fid,q)


def valid(c,fid,q):
    import domain as d
    if not c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,q['recipient'])).fetchone():return False
    try:
        obj=d.require_active(c,fid,q['source'],q['ref'])
        if q['source']=='task':
            return obj['assignee']==q['recipient'] and obj['status'] in ('todo','in_progress') and obj.get('due_date')==q.get('date') and obj.get('reminder_revision',0)==q.get('revision',0) and d.can_view_task(c,fid,q['recipient'],obj) and not paused(c,fid,obj)
        if q['source']=='appointment':
            p=d.get(c,'pregnancy',obj['pregnancy_id']) if obj.get('pregnancy_id') else None
            return obj['owner']==q['recipient'] and obj['status']=='scheduled' and obj['date']==q.get('date') and obj.get('reminder_revision',0)==q.get('revision',0) and (not p or d.can_health(c,fid,q['recipient'],p)) and not paused(c,fid,obj)
        if q['source']=='approval':return obj['status']==q.get('expected_status','pending') and obj['expires_at']>d.now() and d.can_view_approval(c,fid,q['recipient'],obj)
        if q['source']=='briefing':
            import briefings
            return obj['recipient']==q['recipient'] and obj['revision']==q.get('briefing_revision') and briefings.authorized(c,fid,q['recipient'],obj)
    except d.Problem:return False
    return False


def paused(c,fid,obj):
    import domain as d
    if not obj.get('pregnancy_id'):return False
    p=d.get(c,'pregnancy',obj['pregnancy_id'])
    return p.get('paused') or p.get('archived') or p['state']=='ended'


def schedule(c,fid):
    import domain as d
    f=d.get(c,'family',fid);today=d.today(f.get('timezone','Asia/Shanghai'))
    pending={}
    for q in d.rows(c,'reminder',fid):
        if q['status']=='scheduled':pending.setdefault((q['source'],q['ref']),[]).append(q)
    cached_preferences={}
    for source in ('task','appointment'):
        for obj in d.rows(c,source,fid):
            due=obj.get('due_date') if source=='task' else obj['date']
            if not due or obj['status'] not in (('todo','in_progress') if source=='task' else ('scheduled',)):continue
            day=d.date(due)
            if day<today:continue
            user=obj['assignee'] if source=='task' else obj['owner']
            if user not in cached_preferences:cached_preferences[user]=preferences(c,fid,user)
            p=cached_preferences[user]
            leads=[lead for lead in p['task_days' if source=='task' else 'appointment_days'] if (day-today).days<=lead]
            for lead in leads:
                if lead!=min(leads):continue
                revision=obj.get('reminder_revision',0)
                id=('task-'+obj['id']+'-'+due if source=='task' else 'appointment-'+obj['id'])+('-r'+str(revision) if revision else '')
                default=1 if source=='task' else 3
                if lead!=default:id+='-d'+str(lead)
                for old in pending.get((source,obj['id']),[]):
                    if old['id']!=id:cancel(c,fid,old)
                enqueue(c,fid,id,user,source,source,obj['id'],'有一项家庭安排临近，请查看待办' if source=='task' else '有一项预约即将到来，请查看安排',due,date=due,revision=revision,pregnancy_id=obj.get('pregnancy_id'),health_sensitive=bool(obj.get('private_health_anchor',source=='appointment')))
            if source=='appointment' and not obj.get('reminded') and (day-today).days<=max(p['appointment_days']):obj['reminded']=True;d.put(c,'appointment',obj,fid)


def deliver(c,fid,user=None):
    import domain as d
    f=d.get(c,'family',fid);zone=ZoneInfo(f.get('timezone','Asia/Shanghai'));local=dt.datetime.fromtimestamp(d.now(),dt.timezone.utc).astimezone(zone)
    today=str(d.today(zone.key));minute=local.strftime('%H:%M');pending={}
    for q in d.rows(c,'reminder',fid):
        if q['status']!='scheduled' or (user and q['recipient']!=user):continue
        if not valid(c,fid,q):cancel(c,fid,q);continue
        if q.get('expires_date') and today>q['expires_date']:q['status']='expired';d.put(c,'reminder',q,fid);continue
        p=preferences(c,fid,q['recipient'])
        if not enabled(p,q['type']):cancel(c,fid,q);continue
        quiet=p['quiet_enabled'] and ((p['quiet_start']<=minute<p['quiet_end']) if p['quiet_start']<p['quiet_end'] else (minute>=p['quiet_start'] or minute<p['quiet_end']))
        if quiet:continue
        pending.setdefault(q['recipient'],[]).append(q)
    for recipient,queue in pending.items():
        p=preferences(c,fid,recipient)
        delivered=sum(n['recipient']==recipient and n.get('managed_reminder',False) and dt.datetime.fromtimestamp(n['created_at'],dt.timezone.utc).astimezone(zone).date().isoformat()==today for n in d.rows(c,'notification',fid))
        queue.sort(key=lambda q:(q['created_at'],q['id']))
        while queue and delivered<p['daily_limit']:
            first=queue.pop(0);batch=[first]
            if p['merge_enabled']:
                others=[q for q in queue if q['type']==first['type']][:p['merge_limit']-1]
                batch+=others;ids={q['id'] for q in others};queue=[q for q in queue if q['id'] not in ids]
            id=first['id']
            n={'id':id,'recipient':recipient,'title':first['title'] if len(batch)==1 else '你有 '+str(len(batch))+' 项待查看提醒',
               'ref':first['ref'] if len(batch)==1 else None,'read':False,'created_at':d.now(),'managed_reminder':True,'reminder_type':first['type'],'delivery_refs':[q['id'] for q in batch]}
            if len(batch)==1:n.update(pregnancy_id=first.get('pregnancy_id'),health_sensitive=first.get('health_sensitive',True))
            d.put(c,'notification',n,fid)
            for q in batch:q.update(status='sent',notification_id=id,sent_at=d.now(),attempts=q['attempts']+1);d.put(c,'reminder',q,fid)
            delivered+=1
