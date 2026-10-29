"""Private, correction-aware achievements backed by explicit completion/check-in evidence."""
import datetime as dt
import hashlib

CATALOG = [
 ('first-action','认真在场','认真开始',1,'tasks','完成第 1 件任务','芽'),
 ('five-actions','认真在场','稳稳接住',5,'tasks','完成 5 件不同任务','叶'),
 ('twenty-actions','认真在场','持续分担',20,'tasks','完成 20 件不同任务','树'),
 ('fifty-actions','认真在场','日常同行',50,'tasks','完成 50 件不同任务','林'),
 ('first-ready','准备有序','多一份准备',1,'items','备齐第 1 种物品','袋'),
 ('five-ready','准备有序','有序准备',5,'items','备齐 5 种不同物品','箱'),
 ('documents','准备有序','资料有着落',3,'documents','完成 3 件资料任务或证件物品','册'),
 ('housework','日常分担','搭把手',5,'housework','完成 5 件家务或家庭协作任务','手'),
 ('listening','日常分担','认真倾听',3,'communication','完成 3 件陪伴沟通任务','心'),
 ('support','日常分担','安心同行',1,'support','为已完成预约明确记录一次本人陪同','伴'),
 ('checkin-three','认真在场','留下足迹',3,'days','主动打卡 3 个不同日期','迹'),
 ('checkin-seven','认真在场','一周在场',7,'longest','连续主动打卡 7 天','周'),
 ('checkin-thirty','认真在场','三十日同行',30,'longest','连续主动打卡 30 天','月'),
 ('checkin-hundred','认真在场','百日小事',100,'days','累计主动打卡 100 天','日'),
 ('new-journey','共同纪念','新旅程同行',1,'milestone','在独立宝宝授权下保存已确认的成长纪念','纪'),
]


def key(*values):return hashlib.sha256(':'.join(values).encode()).hexdigest()[:32]


def record(c, fid, kind, obj):
    import domain as d
    if kind in ('task','item') and obj.get('status') == 'done' and obj.get('completed_at') is not None:
        d.put(c,'completion_fact',{'id':key(fid,kind,obj['id']), 'user_id':obj['assignee'],
              'resource':kind,'target':obj['id'],'completed_at':obj['completed_at']},fid)


def quiet(snapshot):
    p=snapshot.get('pregnancy')
    return bool((p and not p.get('restricted') and (p.get('paused') or p.get('archived') or p.get('state')=='ended')) or
                (p and any(b.get('pregnancy_id')==p['id'] and b['status'] in ('unconfirmed','hospitalized','lost') for b in snapshot.get('babies',[]))))


def mutate(c,fid,user,action,data):
    import domain as d
    d.member(c,fid,user)
    if action=='engagement.preferences':
        if type(data.get('celebrations')) is not bool:raise d.Problem(400,'请选择是否展示纪念与庆祝')
        d.put(c,'engagement_preferences',{'id':key(fid,user),'user_id':user,'celebrations':data['celebrations']},fid)
        return {'ok':True}
    if action=='engagement.checkin':
        day=str(d.today(d.get(c,'family',fid).get('timezone','Asia/Shanghai')))
        if data.get('date',day)!=day or data.get('confirmed') is not True:raise d.Problem(400,'请确认今天参与了家庭小事；不支持补签')
        id=key(fid,user,day)
        if not c.execute("SELECT 1 FROM records WHERE kind='checkin' AND id=?",(id,)).fetchone():
            d.put(c,'checkin',{'id':id,'user_id':user,'date':day,'created_at':d.now()},fid)
        return {'date':day}
    if action=='engagement.support':
        if data.get('confirmed') is not True:raise d.Problem(400,'请本人确认实际参与了陪同')
        snapshot=d.snapshot(c,user,fid)
        appointment=next((a for a in snapshot['appointments'] if a['id']==data.get('id')),None)
        if not appointment:raise d.Problem(403,'无权读取此预约')
        d.version(appointment,data)
        if appointment['status']!='done' or d.date(appointment['date'])>d.today(snapshot['family'].get('timezone','Asia/Shanghai')):raise d.Problem(409,'请先核对预约已完成，再记录本人陪同')
        id=key(fid,user,appointment['id'])
        prior=next((r for r in d.rows(c,'support_fact',fid) if r['id']==id),None)
        if not prior or prior.get('appointment_version')!=appointment['version']:
            d.put(c,'support_fact',{'id':id,'user_id':user,'target':appointment['id'],'appointment_version':appointment['version'],'created_at':d.now()},fid)
        return {'id':id}
    raise d.Problem(400,'未知成长记录操作')


def summary(c,fid,user,snapshot):
    import domain as d
    tasks={t['id']:t for t in snapshot['tasks']};items={i['id']:i for i in snapshot['items']}
    counts={name:0 for name in ('tasks','items','documents','housework','communication','support','days','longest','milestone')}
    known=set()
    for fact in d.rows(c,'completion_fact',fid):
        obj=(tasks if fact['resource']=='task' else items).get(fact['target'])
        if not obj or obj['status']!='done' or obj.get('completed_at')!=fact['completed_at']:continue
        known.add((fact['resource'],fact['target']))
        if fact['user_id']!=user:continue
        if fact['resource']=='item':
            counts['items']+=1;counts['documents']+=obj.get('pack')=='证件资料包'
        else:
            counts['tasks']+=1
            counts['documents']+=obj.get('type')=='资料准备'
            counts['housework']+=obj.get('type') in ('家庭协作','重复家务','协作')
            counts['communication']+=obj.get('type') in ('陪伴','陪伴与沟通')
    appointments={a['id']:a for a in snapshot['appointments'] if a['status']=='done' and a['date']<=snapshot['calendar_date']}
    counts['support']=sum(f['user_id']==user and f['target'] in appointments and f.get('appointment_version')==appointments[f['target']]['version'] for f in d.rows(c,'support_fact',fid))
    days=sorted({d.date(r['date']) for r in d.rows(c,'checkin',fid) if r['user_id']==user and r['date']<=snapshot['calendar_date']})
    streak=best=0;previous=None
    for day in days:
        streak=streak+1 if previous is not None and (day-previous).days==1 else 1
        best=max(best,streak);previous=day
    today=d.date(snapshot['calendar_date']);current=streak if days and (today-days[-1]).days<=1 else 0
    counts.update(days=len(days),longest=best)
    p=snapshot.get('pregnancy');is_quiet=quiet(snapshot)
    confirmed_babies={e['baby_id'] for e in d.rows(c,'baby_event',fid) if e.get('guardian_notice_version') and e.get('after',{}).get('status')=='growing'}
    counts['milestone']=int(bool(p and any(b['id'] in confirmed_babies and b.get('pregnancy_id')==p['id'] and b['status']=='growing' for b in snapshot.get('babies',[])) and not is_quiet))
    prefs=next((r for r in d.rows(c,'engagement_preferences',fid) if r['user_id']==user),{'celebrations':True})
    quiet_mode=is_quiet or not prefs['celebrations']
    badges=[{'id':id,'group':group,'name':name,'target':target,'progress':min(target,counts[metric]),
             'earned':counts[metric]>=target,'rule':rule,'symbol':symbol,'commemorative':metric=='milestone'}
            for id,group,name,target,metric,rule,symbol in CATALOG]
    legacy=sum(t['status']=='done' and t['assignee']==user and ('task',t['id']) not in known for t in tasks.values())
    legacy+=sum(i['status']=='done' and i['assignee']==user and ('item',i['id']) not in known for i in items.values())
    return {'catalog_version':1,'badges':badges,'earned_count':sum(b['earned'] for b in badges),'quiet':quiet_mode,
            'celebrations':prefs['celebrations'],'current_streak':current,'longest_streak':best,'checkin_days':len(days),
            'checked_today':today in days,'legacy_unattributed':legacy}
