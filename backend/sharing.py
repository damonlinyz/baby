"""Generic public templates and expiring, revocable, field-minimized checklist snapshots."""
import hashlib
import secrets


def roster(c,fid):
    import domain as d
    return hashlib.sha256(d.dumps([tuple(r) for r in c.execute('SELECT user_id,role FROM memberships WHERE family_id=? AND active=1 ORDER BY user_id',(fid,))]).encode()).hexdigest()


def public_template():
    import domain as d
    return {'title':'通用待产准备模板','items':[{'name':name,'pack':pack,'note':note} for name,pack,note in d.ITEMS],
            'notice':'通用生活整理模板，实际物品与办理要求请向机构核对。不包含任何家庭资料，也不会自动加入家庭。'}


def create(c,fid,user,data):
    import domain as d
    d.member(c,fid,user,('dad',))
    if data.get('confirmed') is not True:raise d.Problem(400,'请确认所选物品名称可能涉及私密信息，以及链接保存范围')
    ids=data.get('item_ids');hours=data.get('hours',24)
    if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(not isinstance(id,str) for id in ids) or len(set(ids))!=len(ids):raise d.Problem(400,'请选择 1—50 个不重复物品')
    if type(hours) is not int or not 1<=hours<=168:raise d.Problem(400,'分享有效期需为 1—168 小时')
    items=[];versions={}
    for id in ids:
        item=d.require_active(c,fid,'item',id)
        if item.get('deleted'):raise d.Problem(409,'有物品已删除，请刷新后重新选择')
        items.append({key:item[key] for key in ('name','pack','quantity','level','status')});versions[id]=item['version']
    token=secrets.token_urlsafe(32);id=hashlib.sha256(token.encode()).hexdigest();f=d.get(c,'family',fid)
    row={'id':id,'user_id':user,'created_at':d.now(),'expires_at':d.now()+hours*3600,'revoked':False,
         'roster':roster(c,fid),'family_version':f['version'],'versions':versions,'items':items}
    d.put(c,'share',row,fid);d.audit(c,fid,user,'share.created',{'id':id,'count':len(ids),'hours':hours})
    return {'id':id,'token':token,'path':'/#share='+token,'expires_at':row['expires_at']}


def revoke(c,fid,user,data):
    import domain as d
    me=d.member(c,fid,user);row=d.require_active(c,fid,'share',data.get('id'))
    if row['user_id']!=user and me['role']!='dad':raise d.Problem(403,'只能撤销自己的分享')
    if not row['revoked']:row.update(revoked=True,items=[]);d.put(c,'share',row,fid)
    return {'ok':True}


def listing(c,fid,user):
    import domain as d
    me=d.member(c,fid,user)
    return [{'id':r['id'],'created_at':r['created_at'],'expires_at':r['expires_at'],'revoked':r['revoked'],
             'count':len(r['versions']),'active':active(c,fid,r)} for r in d.rows(c,'share',fid) if r['user_id']==user or me['role']=='dad']


def view(c,token):
    import domain as d,json
    if not isinstance(token,str) or not 20<=len(token)<=100:raise d.Problem(410,'分享已失效，请向分享者获取新链接')
    id=hashlib.sha256(token.encode()).hexdigest()
    row=c.execute("SELECT family_id,body FROM records WHERE kind='share' AND id=?",(id,)).fetchone()
    if not row:raise d.Problem(410,'分享已失效，请向分享者获取新链接')
    fid=row['family_id'];s=json.loads(row['body']);valid=active(c,fid,s)
    if not valid:raise d.Problem(410,'分享已失效，请向分享者获取新链接')
    return {'title':'受限分享的准备清单','items':s['items'],'expires_at':s['expires_at'],
            'notice':'仅包含分享时选定的名称、分类、数量、优先级与状态。不提供家庭入口；截图和自行保存的副本无法撤回。'}


def active(c,fid,s):
    import domain as d,json
    valid=not s['revoked'] and s['expires_at']>d.now()
    valid=valid and bool(c.execute("SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND role='dad' AND active=1",(fid,s['user_id'])).fetchone())
    valid=valid and roster(c,fid)==s['roster'] and d.get(c,'family',fid)['version']==s['family_version']
    if valid:
        for id,version in s['versions'].items():
            raw=c.execute("SELECT body FROM records WHERE kind='item' AND id=? AND family_id=?",(id,fid)).fetchone()
            if not raw:valid=False;break
            obj=json.loads(raw['body'])
            if obj.get('deleted') or obj['version']!=version:valid=False;break
    return bool(valid)


def expire(c,fid):
    import domain as d
    for row in d.rows(c,'share',fid):
        if not row['revoked'] and row['expires_at']<=d.now():
            row.update(revoked=True,items=[]);d.put(c,'share',row,fid)


def invalidate_family(c,fid):
    import domain as d
    for row in d.rows(c,'share',fid):
        if not row['revoked']:row.update(revoked=True,items=[]);d.put(c,'share',row,fid)
