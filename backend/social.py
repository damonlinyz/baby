"""Private feedback on current, authorized completion facts; no public family timeline."""
import hashlib


def visible(c,fid,user,snapshot):
    import domain as d
    resources={kind:{r['id']:r for r in snapshot[key]} for kind,key in (('task','tasks'),('item','items'))}
    members={m['id']:m for m in snapshot['members']};result={}
    for fact in d.rows(c,'completion_fact',fid):
        obj=resources.get(fact['resource'],{}).get(fact['target'])
        if not obj or obj.get('status')!='done' or obj.get('completed_at')!=fact['completed_at'] or fact['user_id'] not in members:continue
        # A changed source gets a different event. Undo/recomplete cannot revive old feedback.
        event=hashlib.sha256(d.dumps([fid,fact['id'],fact['version'],obj['version']]).encode()).hexdigest()
        result[event]={'id':event,'resource':fact['resource'],'target':obj['id'],'title':obj.get('title',obj.get('name')),
                       'author_id':fact['user_id'],'author_name':members[fact['user_id']]['name'],'completed_at':fact['completed_at']}
    return result


def feed(c,fid,user,snapshot):
    import domain as d
    active={m['id']:m['name'] for m in snapshot['members']};items=visible(c,fid,user,snapshot)
    feedback=d.rows(c,'feedback',fid)
    for item in items.values():
        rows=[r for r in feedback if r['event_id']==item['id'] and r['user_id'] in active and not r.get('deleted')]
        likes=[r for r in rows if r['type']=='like' and r['enabled']]
        item['likes']=len(likes);item['liked']=any(r['user_id']==user for r in likes)
        item['comments']=[{'id':r['id'],'user_id':r['user_id'],'name':active[r['user_id']],'text':r['text'],'created_at':r['created_at'],
                           'can_delete':r['user_id']==user or snapshot['role']=='dad'} for r in sorted(rows,key=lambda r:(r['created_at'],r['id'])) if r['type']=='comment'][-30:]
    return sorted(items.values(),key=lambda r:(r['completed_at'],r['id']),reverse=True)[:50]


def mutate(c,fid,user,action,data):
    import domain as d
    # snapshot calls feed with prefiltered data; no feedback mutation recursion.
    s=d.snapshot(c,user,fid);items=visible(c,fid,user,s)
    event=data.get('event_id')
    if event not in items:raise d.Problem(403,'该完成记录已变化或当前不可见，请刷新')
    if action=='feedback.like':
        if type(data.get('enabled')) is not bool:raise d.Problem(400,'点赞状态需要为布尔值')
        id=hashlib.sha256(d.dumps([fid,user,event,'like']).encode()).hexdigest()
        d.put(c,'feedback',{'id':id,'user_id':user,'event_id':event,'type':'like','enabled':data['enabled'],'created_at':d.now()},fid)
    elif action=='feedback.comment':
        text=d.text(data.get('text'),300);d.rate(c,'feedback:'+user,20,3600)
        id=d.uid();d.put(c,'feedback',{'id':id,'user_id':user,'event_id':event,'type':'comment','text':text,'created_at':d.now(),'deleted':False},fid)
        return {'id':id}
    elif action=='feedback.delete':
        row=d.require_active(c,fid,'feedback',data.get('id'))
        if row['type']!='comment' or row['event_id']!=event or (row['user_id']!=user and s['role']!='dad'):raise d.Problem(403,'只能删除本人留言，统筹者可以管理留言')
        row.update(deleted=True,text='');d.put(c,'feedback',row,fid)
    else:raise d.Problem(404,'未知互动操作')
    return {'ok':True}


def clean(c,fid):
    """Remove stale content, including after revocation or roster changes, without copying source text."""
    import domain as d
    active={r['user_id'] for r in c.execute('SELECT user_id FROM memberships WHERE family_id=? AND active=1',(fid,))}
    allowed={}
    for user in active:
        s=d.snapshot(c,user,fid);allowed[user]=set(visible(c,fid,user,s))
    for row in d.rows(c,'feedback',fid):
        if row['event_id'] not in allowed.get(row['user_id'],set()):c.execute("DELETE FROM records WHERE kind='feedback' AND id=?",(row['id'],))
