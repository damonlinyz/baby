"""System operations projections and safe station-outbox recovery, never external re-send."""
import collections
import hashlib
import json
import support

KINDS=('outbox','reminder','external_delivery','ai_usage')


def overview(c,user):
    import domain as d
    support.require_admin(c,user);counts={};queues=[]
    for kind in KINDS:
        rows=c.execute('SELECT family_id,body FROM records WHERE kind=?',(kind,)).fetchall()
        statuses=collections.Counter();ages=[]
        for raw in rows:
            item=json.loads(raw['body']);state=item.get('status','unknown');statuses[state]+=1
            created=item.get('created_at',d.now())
            if state in ('pending','scheduled','processing','reserved'):ages.append(max(0,d.now()-created))
            if state in ('failed','unknown','processing','pending','reserved'):
                queues.append({'kind':kind,'id':item['id'],'family_id':raw['family_id'],'status':state,'version':item['version'],
                               'created_at':created,'attempts':item.get('attempts',0),'manual_retries':item.get('manual_retries',0),
                               'retryable':kind=='outbox' and state=='failed' and item.get('manual_retries',0)<3})
        counts[kind]={'statuses':dict(statuses),'oldest_pending_seconds':max(ages,default=0)}
    audit=[dict(r) for r in c.execute("SELECT id,type,created FROM events WHERE type IN ('operations.retry','support.replied','knowledge.published','knowledge.draft','knowledge.retired') ORDER BY created DESC LIMIT 50")]
    return {'counts':counts,'queues':sorted(queues,key=lambda r:(r['created_at'],r['id']),reverse=True)[:100],
            'audit':audit,'generated_at':d.now(),'notice':'仅提供运行状态，不包含家庭名称、成员、健康日期、事项或问答正文。外部发送结果未知时不可重发。'}


def retry(c,user,data):
    import domain as d
    support.require_admin(c,user)
    if data.get('kind')!='outbox':raise d.Problem(400,'只允许重新排队站内通知，外部消息和 AI 请求不可在这里重试')
    key=d.text(data.get('request_id'),80);id=hashlib.sha256(d.dumps([user,key,'operations']).encode()).hexdigest()
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    old=next((r for r in d.rows(c,'admin_receipt') if r['id']==id),None)
    if old:
        if old['digest']!=digest:raise d.Problem(409,'请求号与原操作不一致，请刷新')
        return old['result']
    raw=c.execute("SELECT family_id,body FROM records WHERE kind='outbox' AND id=?",(data.get('id'),)).fetchone()
    if not raw:raise d.Problem(404,'队列记录不存在')
    fid=raw['family_id'];row=json.loads(raw['body']);d.version(row,data)
    if row['status']!='failed' or row.get('manual_retries',0)>=3:raise d.Problem(409,'当前记录不可重试，请刷新查看状态')
    if data.get('confirmed') is not True:raise d.Problem(400,'请确认核对过失败原因后再重新排队')
    active=False
    try:
        p=d.require_active(c,fid,'pregnancy',row['pregnancy_id'])
        active=p['revision']==row['pregnancy_revision'] and not p.get('paused') and not p.get('archived') and p['state']!='ended' and any(d.can_health(c,fid,u,p) for u in row['recipients'])
    except d.Problem:pass
    if active:row.update(status='pending',attempts=0,available_at=d.now(),manual_retries=row.get('manual_retries',0)+1)
    else:row.update(status='cancelled',processed_at=d.now())
    d.put(c,'outbox',row,fid);result={'status':row['status']}
    d.put(c,'admin_receipt',{'id':id,'user_id':user,'digest':digest,'result':result})
    d.audit(c,None,user,'operations.retry',{'id':row['id'],'status':row['status']})
    return result
