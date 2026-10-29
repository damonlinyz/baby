"""Private, explicitly shared issue tickets; never auto-attach health data or AI text."""
import os
import hashlib

CATEGORIES=('usage','account','content','ai','other')


def require_admin(c,user):
    import domain as d
    if user!=os.environ.get('ADMIN_USER_ID') or not c.execute('SELECT 1 FROM users WHERE id=?',(user,)).fetchone():raise d.Problem(403,'需要系统管理员权限')


def own(c,user):
    import domain as d
    clean(c)
    return {'tickets':[{k:v for k,v in r.items() if k!='input_hash'} for r in sorted(d.rows(c,'support_ticket'),key=lambda r:(r['updated_at'],r['id']),reverse=True) if r['user_id']==user][:100]}


def create(c,user,data):
    import domain as d
    if data.get('confirmed') is not True:raise d.Problem(400,'请确认仅将本人填写的问题交给系统管理员处理')
    key=d.text(data.get('request_id'),80);id=hashlib.sha256(d.dumps([user,key,'support']).encode()).hexdigest()
    category=data.get('category','usage')
    if category not in CATEGORIES:raise d.Problem(400,'问题分类无效')
    title=d.text(data.get('title'),100);body=d.text(data.get('body'),1000)
    digest=hashlib.sha256(d.dumps([category,title,body]).encode()).hexdigest()
    old=next((r for r in d.rows(c,'support_ticket') if r['id']==id),None)
    if old:
        if old['input_hash']!=digest:raise d.Problem(409,'请求编号已用于不同问题，请核对原工单')
        if old.get('withdrawn'):raise d.Problem(409,'此工单已撤回，请查看我的问题')
        return {'id':id}
    d.rate(c,'support:'+user,5,3600)
    row={'id':id,'user_id':user,'category':category,'title':title,'body':body,'input_hash':digest,'status':'open',
         'reply':'','created_at':d.now(),'updated_at':d.now(),'withdrawn':False}
    d.put(c,'support_ticket',row)
    return {'id':id}


def withdraw(c,user,data):
    import domain as d
    row=d.get(c,'support_ticket',data.get('id'))
    if row['user_id']!=user:raise d.Problem(403,'只能撤回自己的问题')
    if not row['withdrawn']:
        d.version(row,data);row.update(withdrawn=True,title='',body='',reply='',status='withdrawn',updated_at=d.now());d.put(c,'support_ticket',row)
    return {'ok':True}


def admin_list(c,user):
    import domain as d
    require_admin(c,user);clean(c)
    return {'tickets':[{k:v for k,v in r.items() if k not in ('input_hash',)} for r in sorted(d.rows(c,'support_ticket'),key=lambda r:(r['updated_at'],r['id']),reverse=True) if not r.get('withdrawn')][:100]}


def reply(c,user,data):
    import domain as d
    require_admin(c,user);clean(c);row=d.get(c,'support_ticket',data.get('id'))
    if row.get('withdrawn'):raise d.Problem(409,'问题已撤回，不再回复')
    d.version(row,data);status=data.get('status')
    if status not in ('open','in_progress','resolved'):raise d.Problem(400,'工单状态无效')
    text=d.text(data.get('reply'),1000)
    row.update(status=status,reply=text,updated_at=d.now());d.put(c,'support_ticket',row)
    d.audit(c,None,user,'support.replied',{'id':row['id'],'status':status})
    return {'ok':True}


def clean(c):
    import domain as d
    for row in d.rows(c,'support_ticket'):
        if not row.get('withdrawn') and row['updated_at']<=d.now()-180*86400:
            row.update(withdrawn=True,title='',body='',reply='',status='expired');d.put(c,'support_ticket',row)
