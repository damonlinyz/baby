"""Private Q&A history and opt-in, non-clinical explanations for static weekly snapshots."""
import hashlib
import unicodedata

RETENTION_DAYS=180


def usage(c,fid,user):
    import os,domain as d
    d.member(c,fid,user);rows=d.rows(c,'ai_usage',fid)
    active=lambda r:r['status']=='committed' or (r['status']=='reserved' and r.get('reserved_until',r['created_at']+120)>d.now())
    mine=[r for r in rows if r['user_id']==user];daily=[r for r in mine if r['created_at']>d.now()-86400 and active(r)]
    cycles=[]
    for grant in d.rows(c,'entitlement',fid):
        if grant['benefit'] not in ('ai','ai_gift') or (grant.get('owner')!=user and grant.get('bound_family')!=fid):continue
        counts={}
        for purpose,quota in (('qa',grant.get('quota',300)),('briefing',grant.get('briefing_quota',5))):
            successful=sum(r.get('entitlement_id')==grant['id'] and r.get('purpose','qa')==purpose and r['status']=='committed' for r in rows)
            pending=sum(r.get('entitlement_id')==grant['id'] and r.get('purpose','qa')==purpose and r['status']=='reserved' and active(r) for r in rows)
            counts[purpose]={'quota':quota,'successful':successful,'reserved':pending,'remaining':max(0,quota-successful-pending)}
        state=grant['status']
        if state=='active':state='scheduled' if (grant.get('starts_at') or 0)>d.now() else 'expired' if (grant.get('ends_at') or 0)<=d.now() else 'active'
        cycles.append({'state':state,'can_activate':grant.get('owner')==user and grant['benefit']=='ai_gift' and grant['status']=='unactivated','id':grant['id'],'benefit':grant['benefit'],'status':grant['status'],'starts_at':grant.get('starts_at'),'ends_at':grant.get('ends_at'),'counts':counts})
    return {'mode':os.environ.get('SERVICE_MODE','self_hosted'),'daily':{'limit':int(os.environ.get('AI_DAILY_LIMIT','30')),'used':len(daily)},
            'my_successful':sum(r['status']=='committed' for r in mine),'my_reserved':sum(r['status']=='reserved' and active(r) for r in mine),'cycles':cycles,
            'notice':'成功次数与处理中占额分别计算，失败不计成功次数，删除历史不会返还额度。周期数为家庭共享额度，不包含他人的问题或回答。'}


def unsafe_question(question):
    text=unicodedata.normalize('NFKC',question).lower()
    # Conservative static routing, not a claim of clinical assessment or professional review.
    return any(word in text for word in ('药','出血','疼','痛','胎动','破水','诊断','报告','检查结果','剂量','发烧','呼吸','胎心','流产',
       '自杀','轻生','急救','喂奶','奶粉','疫苗','黄疸','不想活','伤害自己','伤害宝宝','打孩子','胸闷','昏迷','抽搐','高热','窒息',
       '抑郁','焦虑症','过敏','血压','血糖','化验','用量','mg/kg','bleeding','suicide','dosage','diagnos','medicin','overdose'))


def unsafe_answer(answer):
    text=unicodedata.normalize('NFKC',answer).lower()
    return any(word in text for word in ('服用','毫克','mg/kg','诊断为','肯定健康','一定安全','保证健康','不用就医','不必就医','https://','http://'))


def history(c,fid,user):
    import domain as d
    d.member(c,fid,user);items=[]
    for row in d.rows(c,'ai_usage',fid):
        if row['user_id']!=user or row['status']!='committed' or row.get('purpose','qa')!='qa' or row['created_at']<=d.now()-RETENTION_DAYS*86400:continue
        if not row.get('question') or not row.get('result'):continue
        valid=d.citations_current(c,row['result'],fid,user)
        items.append({'id':row['id'],'question':row['question'],'created_at':row['created_at'],
                      'result':row['result'] if valid else None,'status':'available' if valid else 'source_changed'})
    return {'items':sorted(items,key=lambda r:(r['created_at'],r['id']),reverse=True)[:100],'retention_days':RETENTION_DAYS}


def delete_history(c,fid,user,data):
    import domain as d
    d.member(c,fid,user);row=d.require_active(c,fid,'ai_usage',data.get('id'))
    if row['user_id']!=user:raise d.Problem(403,'只能删除自己的问答')
    row.pop('question',None);row.pop('result',None);row['history_deleted']=True;d.put(c,'ai_usage',row,fid)
    return {'ok':True}


def clean(c):
    import domain as d,json
    for raw in c.execute("SELECT family_id,body FROM records WHERE kind='ai_usage'").fetchall():
        row=json.loads(raw['body'])
        if row['created_at']<=d.now()-RETENTION_DAYS*86400 and (row.get('question') or row.get('result')):
            row.pop('question',None);row.pop('result',None);row['history_deleted']=True;d.put(c,'ai_usage',row,raw['family_id'])


def brief_prompt(payload,snapshot):
    # Deterministic, allowlisted subject only. Raw task/appointment/item fields never leave the server.
    entries=payload['briefing']['entries']
    ordinary={t['id'] for t in snapshot['tasks'] if not t.get('pregnancy_id') and not t.get('private_health_anchor')}
    tasks=sum(r['kind']=='task' and r['id'] in ordinary for r in entries);items=sum(r['kind']=='item' for r in entries)
    return f'本周静态重点中有 {tasks} 项任务、{items} 项物品整理。请按家庭协作、倾听沟通、物品整理三个方向，各给一条简短的一般生活执行建议。不得新增日期、预约、医疗安排或评价家庭成员。'


def enhanced(c,fid,user,id):
    import domain as d,briefings
    row=d.require_active(c,fid,'briefing',id)
    if not briefings.authorized(c,fid,user,row):return None
    for usage in d.rows(c,'ai_usage',fid):
        if usage['user_id']==user and usage.get('purpose')=='briefing' and usage.get('briefing_id')==id and usage.get('briefing_revision')==row['revision'] and usage['status']=='committed' and usage.get('result') and usage['created_at']>d.now()-RETENTION_DAYS*86400 and d.citations_current(c,usage['result'],fid,user):return usage['result']
    return None


def enhance(user,fid,data,automatic=False):
    import domain as d,briefings,server
    if data.get('confirmed') is not True:raise d.Problem(400,'请确认请求一般生活增强解释；不发送私人事项原文')
    with d.transaction() as c:
        d.member(c,fid,user,('dad',));row=d.require_active(c,fid,'briefing',data.get('id'))
        if not briefings.authorized(c,fid,user,row):raise d.Problem(409,'简报已变化，请重新整理')
        revision=row['revision'];snapshot=d.snapshot(c,user,fid)
        key='brief-enhance-'+hashlib.sha256(d.dumps([fid,user,row['id'],revision]).encode()).hexdigest()[:32]
    # Separate quota category; HTTP remains outside the database transaction.
    result=server.ask_ai(user,fid,brief_prompt(row['payload'],snapshot),key,purpose='briefing',binding={'id':row['id'],'revision':revision,'automatic':automatic})
    return {'result':result,'notice':'AI 一般生活建议，不替代原始静态事项。没有发送姓名、日期或私人事项原文。'}


def preferences(c,fid,user):
    import domain as d
    id=hashlib.sha256((fid+':'+user).encode()).hexdigest()
    return next((r for r in d.rows(c,'ai_preferences',fid) if r['id']==id),{'id':id,'user_id':user,'auto_briefing':False})


def save(c,fid,user,data):
    import domain as d
    d.member(c,fid,user,('dad',))
    if type(data.get('enabled')) is not bool:raise d.Problem(400,'请选择是否自动增强')
    if data['enabled'] and data.get('confirmed') is not True:raise d.Problem(400,'请确认模型只接收重点中的任务和物品数量，不发送私人原文')
    row=preferences(c,fid,user);row['auto_briefing']=data['enabled'];d.put(c,'ai_preferences',row,fid)
    return {'ok':True}


def dispatch():
    import os,domain as d,briefings
    if not all(os.environ.get(k) for k in ('AI_BASE_URL','AI_API_KEY','AI_MODEL')):return
    candidate=None
    with d.transaction() as c:
        for family in d.rows(c,'family'):
            fid=family['id'];user=family['coordinator'];pref=preferences(c,fid,user)
            if not pref.get('auto_briefing') or pref.get('last_attempt',0)>d.now()-3600:continue
            # Pick only the current week's authorized static snapshot, never old history.
            s=d.snapshot(c,user,fid)
            for row in d.rows(c,'briefing',fid):
                if row['recipient']==user and row['week_start']==s['weekly_report']['week_start'] and briefings.authorized(c,fid,user,row,s) and not enhanced(c,fid,user,row['id']):
                    pref['last_attempt']=d.now();d.put(c,'ai_preferences',pref,fid);candidate=(user,fid,{'id':row['id'],'confirmed':True});break
            if candidate:break
    if candidate:
        try:enhance(*candidate,automatic=True)
        except d.Problem:pass  # Preserve the complete static snapshot; retry no more than hourly.
