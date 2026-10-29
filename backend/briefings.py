"""Per-recipient weekly snapshots; frozen facts, live authorization and live references."""
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo


def digest(value):
    import domain as d
    return hashlib.sha256(d.dumps(value).encode()).hexdigest()


def context(snapshot):
    p=snapshot.get('pregnancy')
    return {'roster':digest(sorted((m['id'],m['name'],m['role']) for m in snapshot['members'])),
            'timezone':snapshot['family'].get('timezone','Asia/Shanghai'),'role':snapshot['role'],
            'pregnancy':None if not p or p.get('restricted') else [p['id'],p.get('revision')],
            'babies':[list(x) for x in sorted((b['id'],b['status'],b.get('birth_date')) for b in snapshot.get('babies',[]))]}


def authorized(c,fid,user,row,snapshot=None):
    import domain as d
    if row['recipient']!=user or row['status']!='active' or not row.get('payload'):return False
    try:
        s=snapshot or d.snapshot(c,user,fid)
        if context(s)!=row['context']:return False
        resources={kind:{obj['id']:obj for obj in s[key]} for kind,key in (('task','tasks'),('item','items'),('appointment','appointments'))}
        if any(ref['id'] not in resources[ref['kind']] for ref in row['refs']):return False
        if not d.citations_current(c,{'citations':row['citations']},fid,user):return False
        return True
    except d.Problem:return False


def invalidate(c,fid,row):
    import domain as d
    if row['status']=='active':
        row.update(status='invalid',payload=None,refs=[],citations=[])
        d.put(c,'briefing',row,fid);d.cancel_notifications(c,fid,row['id'])


def metadata(c,fid,user):
    import domain as d
    return [{'id':r['id'],'week_start':r['week_start'],'created_at':r['created_at'],'status':r['status'],'revision':r['revision']} for r in sorted(d.rows(c,'briefing',fid),key=lambda r:r['week_start'],reverse=True) if r['recipient']==user]


def listing(c,fid,user):
    import domain as d
    s=d.snapshot(c,user,fid)
    for row in d.rows(c,'briefing',fid):
        if row['recipient']==user and not authorized(c,fid,user,row,s):invalidate(c,fid,row)
    return {'briefings':metadata(c,fid,user)}


def read(c,fid,user,id):
    import domain as d
    d.member(c,fid,user);row=d.require_active(c,fid,'briefing',id)
    if row['recipient']!=user:raise d.Problem(403,'这份简报仅对原接收人开放')
    if not authorized(c,fid,user,row):
        invalidate(c,fid,row)
        # A read that reports invalidation must commit the payload removal.
        return {'id':id,'status':'invalid','payload':None}
    import ai_features
    return {'id':id,'status':'active','payload':row['payload'],'created_at':row['created_at'],'revision':row['revision'],'enhancement':ai_features.enhanced(c,fid,user,id)}


def generate(c,fid,user,force=False):
    import domain as d,reminders
    s=d.snapshot(c,user,fid);r=s['weekly_report'];today=d.date(s['calendar_date'])
    local=dt.datetime.fromtimestamp(d.now(),dt.timezone.utc).astimezone(ZoneInfo(r['timezone']))
    if not force and today.weekday()==0 and local.hour<8:return None
    id='brief-'+digest([fid,user,r['week_start']])[:32]
    old=next((x for x in d.rows(c,'briefing',fid) if x['id']==id),None)
    if old and authorized(c,fid,user,old,s) and not force:return id
    if old:invalidate(c,fid,old)
    if not r['briefing']['enabled']:return None
    refs=[]
    for key,kind in (('tasks','task'),('items','item')):
        refs.extend({'id':e['id'],'kind':kind} for e in r['completed'][key])
    refs.extend({'id':e['id'],'kind':e['kind']} for e in r['briefing']['entries'])
    refs=[dict(pair) for pair in {tuple(sorted(ref.items())) for ref in refs}]
    revision=(old['revision'] if old else 0)+1
    row={'id':id,'recipient':user,'week_start':r['week_start'],'week_end':r['week_end'],
         'revision':revision,'created_at':d.now(),'status':'active','context':context(s),'refs':refs,
         'citations':[{'id':k['id'],'version':k['content_version']} for k in r['briefing']['knowledge']],
         'payload':r}
    d.put(c,'briefing',row,fid)
    reminders.enqueue(c,fid,id+'-r'+str(revision),user,'weekly','briefing',id,'本周家庭简报已整理，请查看',r['week_end'],briefing_revision=revision)
    d.audit(c,fid,user,'briefing.generated',{'id':id,'week':r['week_start'],'revision':revision})
    return id


def schedule(c,fid):
    import domain as d
    for member in c.execute('SELECT user_id FROM memberships WHERE family_id=? AND active=1',(fid,)).fetchall():
        user=member['user_id']
        listing(c,fid,user)
        generate(c,fid,user)


def invalidate_family(c,fid,user=None):
    import domain as d
    for row in d.rows(c,'briefing',fid):
        if user is None or row['recipient']==user:invalidate(c,fid,row)
