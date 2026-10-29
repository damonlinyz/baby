"""Optional WeChat one-shot subscription transport; disabled until explicitly configured.
Only generic text leaves the service. Ambiguous send results are never auto-retried.
"""
import datetime as dt
import os
import json
import urllib.request
import threading
from zoneinfo import ZoneInfo

_cache={}
_token_lock=threading.Lock()


def configured():return os.environ.get('WX_NOTIFY_ENABLED')=='1' and all(os.environ.get(k) for k in ('WX_APP_ID','WX_APP_SECRET','WX_REMINDER_TEMPLATE_ID'))


def options():return {'configured':bool(configured()),'template_id':os.environ.get('WX_REMINDER_TEMPLATE_ID') if configured() else None}


def subscribe(c,fid,user,data):
    import domain as d
    if not configured():raise d.Problem(503,'微信订阅提醒尚未配置，站内提醒照常可用')
    d.member(c,fid,user)
    if data.get('accepted') is not True or data.get('template_id')!=os.environ['WX_REMINDER_TEMPLATE_ID']:raise d.Problem(400,'需要本次模板的明确订阅授权')
    if not any(x['id']==data.get('identity_id') and x['user_id']==user and x['appid']==os.environ['WX_APP_ID'] for x in d.rows(c,'wechat_identity')):raise d.Problem(409,'请先使用本应用的微信身份登录或绑定')
    d.put(c,'subscription',{'id':d.uid(),'user_id':user,'appid':os.environ['WX_APP_ID'],'template_id':data['template_id'],'identity_id':data['identity_id'],
          'status':'available','created_at':d.now(),'expires_at':d.now()+604800},fid)
    return {'ok':True,'uses':1}


def invalidate_family(c,fid,user=None):
    import domain as d
    for row in d.rows(c,'subscription',fid):
        if row['status']=='available' and (user is None or row['user_id']==user):row['status']='revoked';d.put(c,'subscription',row,fid)
    for row in d.rows(c,'external_delivery',fid):
        if row['status']=='processing' and (user is None or row['user_id']==user):row['status']='cancelled';d.put(c,'external_delivery',row,fid)


def allowed(c,fid,n):
    import domain as d,reminders
    if n.get('cancelled') or n.get('read') or not n.get('managed_reminder'):return False
    if not c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,n['recipient'])).fetchone():return False
    p=reminders.preferences(c,fid,n['recipient'])
    if not p.get('external_enabled') or not reminders.enabled(p,n['reminder_type']):return False
    local=dt.datetime.fromtimestamp(d.now(),dt.timezone.utc).astimezone(ZoneInfo(d.get(c,'family',fid).get('timezone','Asia/Shanghai')))
    t=local.strftime('%H:%M')
    attempts=sum(row['user_id']==n['recipient'] and row.get('reason')!='token_unavailable' and dt.datetime.fromtimestamp(row['created_at'],dt.timezone.utc).astimezone(local.tzinfo).date()==local.date() for row in d.rows(c,'external_delivery',fid))
    if attempts>=p['daily_limit']:return False
    if p['quiet_enabled'] and ((p['quiet_start']<=t<p['quiet_end']) if p['quiet_start']<p['quiet_end'] else (t>=p['quiet_start'] or t<p['quiet_end'])):return False
    for id in n.get('delivery_refs',[]):
        row=c.execute("SELECT body FROM records WHERE kind='reminder' AND id=?",(id,)).fetchone()
        if not row:return False
        q=json.loads(row['body'])
        if q.get('expires_date') and str(d.today(local.tzinfo.key))>q['expires_date']:return False
        if q['status']!='sent' or not reminders.valid(c,fid,q):return False
    return bool(n.get('delivery_refs'))


def get_token():
    with _token_lock:return _get_token()


def _get_token():
    import domain as d
    appid=os.environ['WX_APP_ID']
    # Never log token request URLs or credential-bearing failures.
    if _cache.get('appid')==appid and _cache.get('until',0)>d.now():return _cache['token']
    import urllib.parse
    query=urllib.parse.urlencode({'grant_type':'client_credential','appid':appid,'secret':os.environ['WX_APP_SECRET']})
    with urllib.request.urlopen('https://api.weixin.qq.com/cgi-bin/token?'+query,timeout=8) as response:r=json.loads(response.read(50000))
    if not isinstance(r.get('access_token'),str) or not r['access_token']:raise ValueError('token unavailable')
    _cache.update(appid=appid,token=r['access_token'],until=d.now()+max(0,min(int(r.get('expires_in',7200)),7200)-120))
    return _cache['token']


def send(token,openid):
    import urllib.parse
    payload={'touser':openid,'template_id':os.environ['WX_REMINDER_TEMPLATE_ID'],'page':'pages/main/main',
             'data':{'thing1':{'value':'你有待查看事项'}}}
    req=urllib.request.Request('https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token='+urllib.parse.quote(token,safe=''),data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=8) as response:return json.loads(response.read(50000))


def dispatch(limit=5):
    import domain as d
    if not configured():return
    # Sources and recipient are selected in one transaction; HTTP is always outside the DB lock.
    with d.transaction() as c:
        for row in d.rows(c,'external_delivery'):
            if row['status']=='processing' and row['created_at']+120<d.now():
                row['status']='unknown';fid=c.execute("SELECT family_id FROM records WHERE kind='external_delivery' AND id=?",(row['id'],)).fetchone()[0];d.put(c,'external_delivery',row,fid)
        candidates=[]
        for raw in c.execute("SELECT family_id,body FROM records WHERE kind='notification' ORDER BY id").fetchall():
            n=json.loads(raw['body']);fid=raw['family_id']
            if c.execute("SELECT 1 FROM records WHERE kind='external_delivery' AND id=?",(n['id'],)).fetchone() or not allowed(c,fid,n):continue
            receipt=next((s for s in d.rows(c,'subscription',fid) if s['user_id']==n['recipient'] and s['status']=='available' and s['expires_at']>d.now() and s['appid']==os.environ['WX_APP_ID'] and s['template_id']==os.environ['WX_REMINDER_TEMPLATE_ID']),None)
            if receipt:candidates.append((fid,n['id'],receipt['id']))
            if len(candidates)>=limit:break
    for fid,id,receipt_id in candidates:
        try:token=get_token()
        except Exception:
            with d.transaction() as c:
                n=c.execute("SELECT body FROM records WHERE kind='notification' AND id=?",(id,)).fetchone()
                if n and allowed(c,fid,json.loads(n['body'])) and not c.execute("SELECT 1 FROM records WHERE kind='external_delivery' AND id=?",(id,)).fetchone():
                    d.put(c,'external_delivery',{'id':id,'user_id':json.loads(n['body'])['recipient'],'status':'failed','reason':'token_unavailable','attempts':0,'created_at':d.now()},fid)
            continue
        with d.transaction() as c:
            nrow=c.execute("SELECT body FROM records WHERE kind='notification' AND id=?",(id,)).fetchone()
            if not nrow or c.execute("SELECT 1 FROM records WHERE kind='external_delivery' AND id=?",(id,)).fetchone():continue
            n=json.loads(nrow['body']);receipt=d.get(c,'subscription',receipt_id)
            identity=next((r for r in d.rows(c,'wechat_identity') if r['id']==receipt.get('identity_id') and r['user_id']==n['recipient'] and r['appid']==os.environ['WX_APP_ID']),None)
            if not allowed(c,fid,n) or receipt['status']!='available' or receipt['expires_at']<=d.now() or not identity:continue
            receipt['status']='used';d.put(c,'subscription',receipt,fid)
            d.put(c,'external_delivery',{'id':id,'user_id':n['recipient'],'status':'processing','attempts':1,'created_at':d.now(),'subscription_id':receipt_id},fid)
        platform_id=None
        try:
            result=send(token,identity['openid']);code=result.get('errcode')
            if type(code) is not int:raise ValueError('ambiguous platform response')
            raw_id=result.get('msgid')
            if type(raw_id) in (str,int) and len(str(raw_id))<=100:platform_id=str(raw_id)
            status='sent' if code==0 else 'failed'
            if code in (40001,40014,42001):
                with _token_lock:_cache.clear()
        except Exception:status='unknown';code=None
        with d.transaction() as c:
            row=c.execute("SELECT body FROM records WHERE kind='external_delivery' AND id=?",(id,)).fetchone()
            if row:
                delivery=json.loads(row['body'])
                if delivery['status']=='processing':delivery.update(status=status,platform_code=code,platform_message_id=platform_id,finished_at=d.now());d.put(c,'external_delivery',delivery,fid)
