"""SQLite domain layer. Mutations run under BEGIN IMMEDIATE; callers supply authenticated actor."""
import os,json,sqlite3,secrets,hashlib,hmac,time,datetime,contextlib
import templates,dates,lifecycle,recurring,bag,reports
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
DB=Path(os.environ.get('BABA_DB',str(ROOT/'private'/'baba.sqlite3')))
class Problem(Exception):
 def __init__(self,status,message): self.status=status;self.message=message

def uid(): return secrets.token_hex(12)
def now(): return int(time.time())
def today(timezone=dates.DEFAULT_TIMEZONE): return dates.local_today(timezone)
def dumps(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'))
def text(x,n=200):
 if not isinstance(x,str) or not x.strip() or len(x)>n: raise Problem(400,'请填写有效内容（最多 %d 字）'%n)
 return x.strip()
def date(x):
 try:return dates.parse(x)
 except (TypeError,ValueError):raise Problem(400,'日期格式不正确')
@contextlib.contextmanager
def transaction():
 DB.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 c=sqlite3.connect(str(DB),timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=15000')
 try:
  c.execute('BEGIN IMMEDIATE');yield c;c.commit()
 except Exception:c.rollback();raise
 finally:c.close()
def init():
 with transaction() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,login TEXT NOT NULL UNIQUE,name TEXT NOT NULL,password TEXT NOT NULL,created INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,family_id TEXT,body TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(kind,id));
  CREATE INDEX IF NOT EXISTS records_family ON records(family_id,kind);
  CREATE TABLE IF NOT EXISTS memberships(family_id TEXT NOT NULL,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(family_id,user_id));
  CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,family_id TEXT,actor TEXT,type TEXT,body TEXT,created INTEGER);
  CREATE TABLE IF NOT EXISTS requests(user_id TEXT NOT NULL,key TEXT NOT NULL,result TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(user_id,key));
  CREATE TABLE IF NOT EXISTS command_requests(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,key TEXT NOT NULL,family_id TEXT NOT NULL,action TEXT NOT NULL,payload_hash TEXT NOT NULL,result TEXT NOT NULL,created INTEGER NOT NULL,invalidated INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,key));
  CREATE TABLE IF NOT EXISTS payment_events(provider TEXT NOT NULL,event_id TEXT NOT NULL,body_hash TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(provider,event_id));
  CREATE TABLE IF NOT EXISTS payment_transactions(provider TEXT NOT NULL,transaction_id TEXT NOT NULL,order_id TEXT NOT NULL,PRIMARY KEY(provider,transaction_id));
  CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY,count INTEGER NOT NULL,window INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  ''')
  c.execute("INSERT OR IGNORE INTO metadata VALUES('schema_version','1')")
  c.execute("INSERT OR IGNORE INTO metadata VALUES('instance_id',?)",(uid(),))
  attach_deletion_ledger(c)
 try:os.chmod(DB,0o600)
 except OSError:pass

def put(c,kind,obj,family_id=None):
 existing=c.execute('SELECT version FROM records WHERE kind=? AND id=?',(kind,obj['id'])).fetchone()
 obj=dict(obj);obj['version']=(existing['version']+1) if existing else 1
 c.execute('INSERT INTO records(kind,id,family_id,body,version) VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,version=excluded.version',(kind,obj['id'],family_id,dumps(obj),obj['version']))
 return obj
def get(c,kind,id):
 row=c.execute('SELECT body FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
 if not row:raise Problem(404,'记录不存在')
 return json.loads(row['body'])
def rows(c,kind,family_id=None):
 if family_id is None:rs=c.execute('SELECT body FROM records WHERE kind=?',(kind,))
 else:rs=c.execute('SELECT body FROM records WHERE kind=? AND family_id=?',(kind,family_id))
 return [json.loads(r['body']) for r in rs]
def audit(c,fid,actor,type,body):c.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(uid(),fid,actor,type,dumps(body),now()))
def member(c,fid,user,roles=None):
 m=c.execute('SELECT * FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,user)).fetchone()
 if not m or (roles and m['role'] not in roles):raise Problem(403,'你没有执行此操作的家庭权限')
 return dict(m)
def version(obj,data):
 if data.get('version')!=obj['version']:raise Problem(409,'内容已经更新，请刷新后重试')
def password_hash(p):
 if not isinstance(p,str) or len(p)<10 or len(p)>128:raise Problem(400,'密码需要 10—128 个字符')
 salt=secrets.token_hex(16);derived=hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),600000)
 return salt+':'+derived.hex()
def password_ok(p,stored):
 if not isinstance(p,str) or len(p)>128:return False
 salt,expected=stored.split(':');return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),600000).hex(),expected)
def rate(c,key,limit,period=60):
 t=now();r=c.execute('SELECT count,window FROM rate_limits WHERE key=?',(key,)).fetchone()
 if not r or t-r['window']>=period:c.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?)',(key,1,t))
 elif r['count']>=limit:raise Problem(429,'操作太频繁，请稍后再试')
 else:c.execute('UPDATE rate_limits SET count=count+1 WHERE key=?',(key,))
def auth_register(c,data):
 login=text(data.get('login'),80).lower();name=text(data.get('name'),30);pw=password_hash(data.get('password'))
 if c.execute('SELECT id FROM users WHERE login=?',(login,)).fetchone():raise Problem(409,'账号已存在')
 user=uid();c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(user,login,name,pw,now()));return user

def new_session(c,user):
 token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(24)
 c.execute('DELETE FROM sessions WHERE expires<?',(now(),));c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user,csrf,now()+604800));return token,csrf

def session(c,token):
 r=c.execute('SELECT user_id,csrf,expires FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
 if not r or r['expires']<=now():raise Problem(401,'请先登录')
 return dict(r)

def profile(c,user):
 u=dict(c.execute('SELECT id,name,login,created FROM users WHERE id=?',(user,)).fetchone());u['families']=[];u['is_system_admin']=user==os.environ.get('ADMIN_USER_ID')
 for m in c.execute('SELECT family_id,role FROM memberships WHERE user_id=? AND active=1',(user,)):
  f=get(c,'family',m['family_id']);u['families'].append({'id':f['id'],'name':f['name'],'role':m['role']})
 return u

TASKS=[('一起核对就诊预约和证件','确认日期与机构要求，避免临出门找材料','准备事项'),('安排一次不被打扰的聊天','听听对方今天的感受和需要','陪伴'),('盘点家里已有的用品','先盘点再采购，减少重复购买','准备事项'),('一起讨论本周家务分工','每个人认领一件力所能及的事','协作')]
ITEMS=[('入院换洗衣物','妈妈入院包','按季节和医院要求准备'),('个人洗漱用品','妈妈入院包','用熟悉的用品更安心'),('舒适防滑拖鞋','妈妈入院包','先核对医院使用要求'),('宝宝换洗衣物','宝宝用品包','适量准备，不大量囤货'),('纸尿裤','宝宝用品包','按实际需要准备'),('柔软毛巾','宝宝用品包','洗净晾干后收好'),('身份证及所需复印件','证件资料包','核对有效期'),('预约与产检资料','证件资料包','由资料本人保管'),('医保相关凭证','证件资料包','核对当地要求')]
KNOWLEDGE=[('陪伴与沟通','先倾听，再一起想办法','先问“今天哪件事让你觉得辛苦？”听完之后再问，对方想要倾听还是解决办法。把“有事叫我”变成明确分工，可以减少反复沟通。'),('备产准备','待产包从三类开始','先按妈妈用品、宝宝用品、证件资料分类。对照就诊机构要求，盘点已有的，再准备缺少的。不必一次买齐或大量囤货。'),('家庭协作','任务交给具体的人','一项任务指定一个负责人，写清时间和完成标准。需要确认的事项先提交，等待确认后再计入完成。'),('自我照顾','给正在学习的自己一点时间','成为父母需要学习。可以表达不确定，也可以向家人寻求支持。每天做一件具体的小事，同时给彼此留出休息空间。')]
def create_family(c,user,data):
 if len(profile(c,user)['families'])>=10:raise Problem(400,'已达到家庭数量上限')
 fid=uid();f={'id':fid,'name':text(data.get('name'),40),'coordinator':user,'mode':'off','policy_version':1,'created_at':now(),'active_pregnancy_id':None,'timezone':dates.DEFAULT_TIMEZONE}
 put(c,'family',f,fid);c.execute('INSERT INTO memberships VALUES(?,?,?,1)',(fid,user,'dad'))
 for title,note,type in TASKS:put(c,'task',{'id':uid(),'title':title,'note':note,'type':type,'assignee':user,'status':'todo','due_date':None,'completed_at':None,'created_at':now()},fid)
 for name,pack,note in ITEMS:put(c,'item',{'id':uid(),'name':name,'pack':pack,'note':note,'quantity':1,'assignee':user,'status':'todo','level':'建议'},fid)
 audit(c,fid,user,'family.created',{});return {'family_id':fid}
def can_health(c,f,user,p=None):
 if not p:return False
 if not health_members_active(c,f,user,p):return False
 if p['subject_id']==user:return True
 return any(g['subject']==p['subject_id'] and g['grantee']==user and g['scope']=='health_read' and not g.get('revoked_at') for g in rows(c,'consent',f))
def can_edit_health(c,f,user,p):
 if not health_members_active(c,f,user,p):return False
 if p['subject_id']==user:return True
 return any(g['subject']==p['subject_id'] and g['grantee']==user and g['scope']=='health_edit' and not g.get('revoked_at') for g in rows(c,'consent',f))
def health_members_active(c,fid,user,p):
 return all(c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,u)).fetchone() for u in {user,p['subject_id']})
def can_view_task(c,fid,user,task):
 p=get(c,'pregnancy',task['pregnancy_id']) if task.get('pregnancy_id') else None
 if task.get('private_health_anchor') and not can_health(c,fid,user,p):return False
 return task.get('template_key') not in lifecycle.BABY_TEMPLATE_KEYS or lifecycle.can_read_babies(c,fid,user,p)
def can_view_notification(c,fid,user,n):
 if n.get('pregnancy_id') and n.get('health_sensitive',True) and not can_health(c,fid,user,get(c,'pregnancy',n['pregnancy_id'])):return False
 row=c.execute("SELECT body FROM records WHERE kind='task' AND id=?",(n.get('ref'),)).fetchone()
 return not row or can_view_task(c,fid,user,json.loads(row['body']))
def notify(c,fid,user,title,ref=None):
 put(c,'notification',{'id':uid(),'recipient':user,'title':title,'ref':ref,'read':False,'created_at':now()},fid)
def require_active(c,fid,kind,id):
 obj=get(c,kind,id)
 r=c.execute('SELECT family_id FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
 if r['family_id']!=fid:raise Problem(403,'记录不属于当前家庭')
 return obj

def invalidate_approvals(c,fid,kind,target,status='cancelled'):
 for a in rows(c,'approval',fid):
  if a['resource']==kind and a['target']==target and a['status']=='pending':
   a['status']=status;put(c,'approval',a,fid)

def expire_approvals(c,fid):
 for a in rows(c,'approval',fid):
  if a['status']!='pending' or a['expires_at']>now():continue
  a['status']='expired';put(c,'approval',a,fid)
  if a['resource'] in ('task','item'):
   obj=require_active(c,fid,a['resource'],a['target'])
   if obj['status']=='pending' and obj['version']==a['payload']['version']:
    obj['status']='in_progress';put(c,a['resource'],obj,fid)

def clean_health_access(c,fid):
 # A revoked grant must not leave a queued reminder or a completion request usable.
 for n in rows(c,'notification',fid):
  if not can_view_notification(c,fid,n['recipient'],n):
   if not n.get('cancelled'):n['cancelled']=True;put(c,'notification',n,fid)
 for a in rows(c,'approval',fid):
  if a['status']!='pending' or a['resource'] not in ('task','item'):continue
  obj=require_active(c,fid,a['resource'],a['target'])
  if obj.get('private_health_anchor'):
   p=get(c,'pregnancy',obj['pregnancy_id'])
   if not all(can_view_task(c,fid,u,obj) for u in (a['proposer'],a['approver'])):
    a['status']='cancelled';put(c,'approval',a,fid)
    if obj['status']=='pending':obj['status']='in_progress';put(c,a['resource'],obj,fid)

def command(c,user,fid,action,key,data):
 member(c,fid,user)
 if not isinstance(data,dict):raise Problem(400,'操作参数需要为对象')
 digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 prior=c.execute('SELECT * FROM command_requests WHERE user_id=? AND key=?',(user,key)).fetchone()
 if prior:
  if prior['family_id']!=fid or prior['action']!=action or prior['payload_hash']!=digest:raise Problem(409,'请求编号已用于其他内容，请刷新后重试')
  if prior['invalidated']:raise Problem(409,'该请求涉及的资料已清理，请刷新查看当前状态')
  if action=='pregnancy.event' and not can_edit_health(c,fid,user,require_active(c,fid,'pregnancy',data.get('id'))):raise Problem(403,'该健康资料代录授权已失效')
  if action=='baby.update':
   baby=require_active(c,fid,'baby',data.get('id'));p=get(c,'pregnancy',baby['pregnancy_id'])
   if p['subject_id']!=user:raise Problem(403,'没有宝宝状态记录权限')
  return json.loads(prior['result'])
 # Old requests do not contain enough context to prove that a retry is identical.
 if c.execute('SELECT 1 FROM requests WHERE user_id=? AND key=?',(user,key)).fetchone():raise Problem(409,'旧请求无法核对原始内容，请刷新后重试')
 result=mutate(c,user,fid,action,data)
 c.execute('INSERT INTO command_requests(user_id,key,family_id,action,payload_hash,result,created) VALUES(?,?,?,?,?,?,?)',(user,key,fid,action,digest,dumps(result),now()))
 return result

def cancel_transfers(c,fid,user=None):
 for t in rows(c,'transfer',fid):
  if t['status']=='pending' and (user is None or user in (t['from'],t['to'])):
   t['status']='cancelled';put(c,'transfer',t,fid)

def cancel_member_approvals(c,fid,user):
 for a in rows(c,'approval',fid):
  if a['status']!='pending' or user not in (a['proposer'],a['approver']):continue
  a['status']='cancelled';put(c,'approval',a,fid)
  if a['resource'] in ('task','item'):
   obj=require_active(c,fid,a['resource'],a['target'])
   if obj['status']=='pending':obj['status']='in_progress';put(c,a['resource'],obj,fid)

def delete_account(c,user,password):
 r=c.execute('SELECT password FROM users WHERE id=?',(user,)).fetchone()
 if not r or not password_ok(password,r['password']):raise Problem(403,'请重新输入账号密码以确认')
 if c.execute("SELECT 1 FROM memberships WHERE user_id=? AND role='dad' AND active=1",(user,)).fetchone():raise Problem(409,'请先转交家庭统筹权限，再删除账号')
 attach_deletion_ledger(c)
 c.execute('INSERT OR IGNORE INTO deletion_log.tombstones VALUES(?,?)',(user,now()))
 return erase_account(c,user)

def deletion_ledger_path(db=None):return Path(str(db or DB)+'.deletions.sqlite3')

def attach_deletion_ledger(c):
 if not any(r[1]=='deletion_log' for r in c.execute('PRAGMA database_list')):
  c.execute('ATTACH DATABASE ? AS deletion_log',(str(deletion_ledger_path()),))
 # Attached-database atomic commit requires rollback journals on both files.
 if c.execute('PRAGMA main.journal_mode').fetchone()[0]!='delete' or c.execute('PRAGMA deletion_log.journal_mode').fetchone()[0]!='delete':raise Problem(503,'删除记录需要 SQLite DELETE 日志模式，请先检查数据库配置')
 os.chmod(deletion_ledger_path(),0o600)
 c.execute('CREATE TABLE IF NOT EXISTS deletion_log.metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS deletion_log.tombstones(user_id TEXT PRIMARY KEY,deleted_at INTEGER NOT NULL)')
 instance=c.execute("SELECT value FROM main.metadata WHERE key='instance_id'").fetchone()[0]
 bound=c.execute("SELECT value FROM deletion_log.metadata WHERE key='instance_id'").fetchone()
 if bound and bound[0]!=instance:raise Problem(503,'删除记录与业务数据库不匹配')
 c.execute("INSERT OR IGNORE INTO deletion_log.metadata VALUES('instance_id',?)",(instance,))

def erase_account(c,user):
 # Used by authenticated deletion and by offline recovery's tombstone replay.
 owned={p['id'] for p in rows(c,'pregnancy') if p.get('subject_id')==user}
 affected={r['family_id'] for r in c.execute('SELECT family_id FROM memberships WHERE user_id=?',(user,))}
 for fid in affected:
  cancel_transfers(c,fid,user);cancel_member_approvals(c,fid,user)
  m=c.execute('SELECT role,active FROM memberships WHERE family_id=? AND user_id=?',(fid,user)).fetchone()
  if m['role']=='mom' and m['active']:apply_policy(c,fid,'off')
  recurring.stop_for_member(c,fid,user,user,'assignee_deleted')
 for f in rows(c,'family'):
  if f.get('active_pregnancy_id') in owned:f['active_pregnancy_id']=None;put(c,'family',f,f['id'])
 removed=set(owned)
 # Remove private derived tasks; ordinary household tasks survive without a health anchor.
 for row in c.execute("SELECT id,family_id,body FROM records WHERE kind='task'").fetchall():
  t=json.loads(row['body'])
  if t.get('pregnancy_id') not in owned:continue
  if t.get('private_health_anchor'):
   removed.add(t['id']);c.execute("DELETE FROM records WHERE kind='task' AND id=?",(t['id'],))
  else:
   t['pregnancy_id']=None;t['stage']=None;put(c,'task',t,row['family_id'])
 for kind in ('pregnancy','pregnancy_event','baby_event','outbox','appointment','baby','chat','consent','wechat_identity','notification_preferences','ai_usage','transfer'):
  for obj in rows(c,kind):
   if obj.get('pregnancy_id') in owned or obj['id'] in owned or any(obj.get(k)==user for k in ('subject_id','owner','subject','grantee','user_id','from','to')):
    removed.add(obj['id']);c.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,obj['id']))
 for a in rows(c,'approval'):
  if a.get('target') in removed or user in (a['proposer'],a['approver']):
   removed.add(a['id']);c.execute("DELETE FROM records WHERE kind='approval' AND id=?",(a['id'],))
 for n in rows(c,'notification'):
  if n['recipient']==user or n.get('pregnancy_id') in owned or n.get('ref') in removed:c.execute("DELETE FROM records WHERE kind='notification' AND id=?",(n['id'],))
 for event in c.execute("SELECT id,actor,body FROM events WHERE type LIKE 'health.%'").fetchall():
  if event['actor']==user or json.loads(event['body']).get('pregnancy_id') in owned:c.execute('DELETE FROM events WHERE id=?',(event['id'],))
 # Erase results but retain deduplication keys, so a retry cannot recreate deleted data.
 for fid in affected:c.execute("UPDATE command_requests SET result='{}',invalidated=1 WHERE family_id=?",(fid,))
 c.execute('DELETE FROM requests WHERE user_id=?',(user,))
 c.execute('DELETE FROM users WHERE id=?',(user,))
 return {'ok':True}

def can_view_approval(c,fid,user,a):
 if a['resource'] not in ('task','item'):return True
 obj=require_active(c,fid,a['resource'],a['target'])
 return can_view_task(c,fid,user,obj)

def snapshot(c,user,fid):
 me=member(c,fid,user);f=get(c,'family',fid)
 expire_approvals(c,fid)
 members=[dict(r) for r in c.execute('SELECT u.id,u.name,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.family_id=? AND m.active=1',(fid,))]
 tasks=rows(c,'task',fid);all_items=rows(c,'item',fid);items=[i for i in all_items if not i.get('deleted')]
 tasks=[t for t in tasks if can_view_task(c,fid,user,t)]
 if me['role']=='member':tasks=[t for t in tasks if t['assignee']==user];items=[i for i in items if i['assignee']==user]
 p=get(c,'pregnancy',f['active_pregnancy_id']) if f.get('active_pregnancy_id') else None
 if p and not can_health(c,fid,user,p):p={'id':p['id'],'restricted':True}
 elif p:p['timeline']=dates.timeline(p,f.get('timezone',dates.DEFAULT_TIMEZONE))
 babies=[b for b in rows(c,'baby',fid) if lifecycle.can_read_babies(c,fid,user,get(c,'pregnancy',b['pregnancy_id']))]
 approvals=rows(c,'approval',fid)
 approvals=[a for a in approvals if me['role'] in ('dad','mom') and (a['approver']==user or a['proposer']==user)]
 approvals=[a for a in approvals if can_view_approval(c,fid,user,a)]
 appointments=[]
 for a in rows(c,'appointment',fid):
  ap=get(c,'pregnancy',a['pregnancy_id']) if a.get('pregnancy_id') else None
  if can_health(c,fid,user,ap) or (not ap and a['owner']==user):appointments.append(a)
 knowledge=published_knowledge(c)
 orders=[o for o in rows(c,'order',fid) if o['payer']==user]
 order_ids={o['id'] for o in orders}
 refunds=[r for r in rows(c,'refund',fid) if r['order_id'] in order_ids]
 result={'calendar_date':str(today(f.get('timezone',dates.DEFAULT_TIMEZONE))),'user':profile(c,user),'role':me['role'],'family':f,'members':members,'tasks':tasks,'task_series':[s for s in rows(c,'task_series',fid) if me['role']!='member' or s['assignee']==user],'items':items,'deleted_items':[i for i in all_items if i.get('deleted')] if me['role']=='dad' else [],'pregnancy':p,'babies':babies,'pregnancies':[{'id':x['id'],'state':x['state'],'archived':x.get('archived',False)} for x in rows(c,'pregnancy',fid) if can_health(c,fid,user,x)] if me['role']!='member' else [],'appointments':appointments,'approvals':approvals,'knowledge':knowledge,'notifications':[n for n in rows(c,'notification',fid) if n['recipient']==user and can_view_notification(c,fid,user,n)], 'reminder_preferences':next((x for x in rows(c,'notification_preferences',fid) if x['user_id']==user),{'enabled':True}), 'consents':[g for g in rows(c,'consent',fid) if g['subject']==user],'orders':orders,'refunds':refunds,'entitlements':[e for e in rows(c,'entitlement',fid) if e['owner']==user or e.get('bound_family')==fid], 'transfers':[t for t in rows(c,'transfer',fid) if t['to']==user and t['status']=='pending' and t['expires_at']>now()], 'activities':[{'type':r['type'],'created_at':r['created']} for r in c.execute("SELECT type,created FROM events WHERE family_id=? AND type NOT LIKE 'health.%' ORDER BY created DESC LIMIT 15",(fid,))]}
 result['weekly_report']=reports.build(result)
 return result
def propose(c,fid,user,resource,action,payload,target=None):
 mom=c.execute("SELECT user_id FROM memberships WHERE family_id=? AND role='mom' AND active=1",(fid,)).fetchone()
 if not mom:raise Problem(409,'需要妈妈加入家庭后才能启用确认')
 a={'id':uid(),'resource':resource,'action':action,'target':target,'payload':payload,'proposer':user,'approver':mom['user_id'],'status':'pending','policy_version':get(c,'family',fid)['policy_version'],'created_at':now(),'expires_at':now()+172800}
 put(c,'approval',a,fid);notify(c,fid,a['approver'],'有一项家庭事务等待你确认',a['id']);return {'pending':True,'approval_id':a['id']}
def apply_policy(c,fid,value):
 f=get(c,'family',fid);f['mode']=value;f['policy_version']+=1;put(c,'family',f,fid)
 for a in rows(c,'approval',fid):
  if a['status']!='pending':continue
  a['status']='cancelled';put(c,'approval',a,fid)
  if a['resource'] in ('task','item'):
   obj=require_active(c,fid,a['resource'],a['target'])
   if obj['status']=='pending':obj['status']='in_progress';put(c,a['resource'],obj,fid)

def family_timezone(c,fid,value):
 try:zone=dates.timezone_name(value)
 except ValueError as e:raise Problem(400,str(e))
 current=today(zone)
 for p in rows(c,'pregnancy',fid):
  if (p['state']=='pregnant' and p.get('edd') and date(p['edd'])>current+datetime.timedelta(days=280)) or (p['state']=='postpartum' and p.get('birth_date') and date(p['birth_date'])>current):raise Problem(409,'新时区与现有日期记录冲突，请由资料本人核对后再修改')
 for b in rows(c,'baby',fid):
  if b['status']!='entry_corrected' and (date(b['birth_date'])>current or (b.get('status_date') and date(b['status_date'])>current)):raise Problem(409,'新时区与现有日期记录冲突，请由资料本人核对后再修改')
 return zone

def apply_family_settings(c,fid,payload):
 f=get(c,'family',fid)
 zone=family_timezone(c,fid,payload.get('timezone',f.get('timezone',dates.DEFAULT_TIMEZONE)))
 changed_zone=zone!=f.get('timezone',dates.DEFAULT_TIMEZONE)
 changed_policy=payload.get('mode',f['mode'])!=f['mode']
 f['name']=text(payload.get('name',f['name']),40);f['timezone']=zone;put(c,'family',f,fid)
 if changed_zone or changed_policy:apply_policy(c,fid,payload.get('mode',f['mode']))
 else:
  for a in rows(c,'approval',fid):
   if a['resource']=='family' and a['status']=='pending':a['status']='cancelled';put(c,'approval',a,fid)
 if changed_zone:
  for kind in ('task','appointment'):
   for obj in rows(c,kind,fid):
    if obj['status'] in ('todo','in_progress','scheduled') and (obj.get('due_date') or kind=='appointment'):
     reset_reminder(c,fid,obj);put(c,kind,obj,fid)
def complete(c,fid,kind,obj):
 obj['status']='done';obj['completed_at']=now();reset_reminder(c,fid,obj);return put(c,kind,obj,fid)

def cancel_notifications(c,fid,ref):
 for n in rows(c,'notification',fid):
  if n.get('ref')==ref and not n['read'] and not n.get('cancelled'):
   n['cancelled']=True;put(c,'notification',n,fid)

def reset_reminder(c,fid,obj):
 cancel_notifications(c,fid,obj['id'])
 obj['reminder_revision']=obj.get('reminder_revision',0)+1
 if 'reminded' in obj:obj['reminded']=False

def pregnancy_for_event(c,user,fid,data):
 member(c,fid,user)
 p=require_active(c,fid,'pregnancy',data.get('id'));version(p,data)
 if not can_edit_health(c,fid,user,p):raise Problem(403,'该健康资料需由本人授权后才能代录')
 if p.get('archived'):raise Problem(409,'已归档孕次不可修改，请建立新孕次')
 return p

def pregnancy_history(c,user,fid,pid):
 member(c,fid,user);p=require_active(c,fid,'pregnancy',pid)
 if not can_health(c,fid,user,p):raise Problem(403,'查看日期修订历史需要本人授权')
 return sorted((e for e in rows(c,'pregnancy_event',fid) if e['pregnancy_id']==pid),key=lambda e:e['revision'])

def process_outbox(c,fid):
 pending=[e for e in rows(c,'outbox',fid) if e['status']=='pending' and e.get('available_at',0)<=now()]
 for event in sorted(pending,key=lambda e:e['created_at'])[:100]:
  event['attempts']+=1
  try:
   p=require_active(c,fid,'pregnancy',event['pregnancy_id'])
   if p['revision']!=event['pregnancy_revision'] or p.get('paused') or p.get('archived') or p['state']=='ended':
    event['status']='cancelled'
   else:
    sent=0
    for user in event['recipients']:
     if not can_health(c,fid,user,p):continue
     id=event['id']+'-'+user
     if not c.execute("SELECT 1 FROM records WHERE kind='notification' AND id=?",(id,)).fetchone():
      put(c,'notification',{'id':id,'recipient':user,'title':'时间轴安排已更新，请查看待办','ref':event['event_id'],'pregnancy_id':p['id'],'read':False,'created_at':now()},fid)
     sent+=1
    event['status']='sent' if sent else 'cancelled'
   event['processed_at']=now()
  except Problem:
   event['status']='failed' if event['attempts']>=3 else 'pending'
   event['available_at']=now()+30*event['attempts']
  put(c,'outbox',event,fid)

def transition_fingerprint(c,user,fid,data):
 payload={k:v for k,v in data.items() if k!='preview_token'}
 state=[dict(r) for r in c.execute('SELECT kind,id,body,version FROM records WHERE family_id=? ORDER BY kind,id',(fid,))]
 f=get(c,'family',fid)
 body={'user':user,'data':payload,'state':state,'day':str(today(f.get('timezone',dates.DEFAULT_TIMEZONE)))}
 return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def preview_transition(c,user,fid,data):
 if not isinstance(data,dict):raise Problem(400,'操作参数需要为对象')
 p=pregnancy_for_event(c,user,fid,data)
 if not can_health(c,fid,user,p):raise Problem(403,'预览健康日期变更需要本人授权查看')
 token=transition_fingerprint(c,user,fid,data)
 before={t['id']:t for t in rows(c,'task',fid)}
 notices={n['id']:n for n in rows(c,'notification',fid)}
 c.execute('SAVEPOINT pregnancy_preview')
 try:
  transition(c,fid,user,p,data)
  changes=[]
  for t in rows(c,'task',fid):
   old=before.get(t['id'])
   if not old:change='created'
   elif old['status']!=t['status'] and t['status']=='cancelled':change='cancelled'
   elif old['status']=='cancelled' and t['status']!='cancelled':change='restored'
   elif old.get('due_date')!=t.get('due_date'):change='rescheduled'
   else:continue
   changes.append({'id':t['id'],'title':t['title'],'change':change,'before_date':old.get('due_date') if old else None,'after_date':t.get('due_date')})
  cancelled=sum(not n.get('cancelled') and get(c,'notification',id).get('cancelled',False) for id,n in notices.items())
  after=get(c,'pregnancy',p['id'])
  babies=[{'name':b['name'],'status':b['status']} for b in rows(c,'baby',fid) if b['pregnancy_id']==p['id'] and b['status']!='entry_corrected' and lifecycle.can_read_babies(c,fid,user,p)] if data.get('event')=='birth' else []
  result={'babies':babies,'preview_token':token,'state':after['state'],'paused':after.get('paused',False),'effective_edd':after.get('edd'),'dating_source':after.get('dating_source'),'lmp':after.get('lmp'),'changes':changes,'counts':{kind:sum(t['change']==kind for t in changes) for kind in ('created','rescheduled','cancelled','restored')},'cancelled_notifications':cancelled,'preserved_completed':sum(t.get('pregnancy_id')==p['id'] and t['status']=='done' for t in before.values()),'pinned_appointments':sum(a.get('pregnancy_id')==p['id'] and a['status']=='scheduled' for a in rows(c,'appointment',fid))}
 finally:
  c.execute('ROLLBACK TO SAVEPOINT pregnancy_preview');c.execute('RELEASE SAVEPOINT pregnancy_preview')
 return result

def transition(c,fid,user,p,data):
 before_record=dict(p)
 event_id=uid()
 event=data.get('event');d=data.get('date');before=p['state'];before_revision=p['revision'];allowed={'dating':{'set_edd','set_lmp','end','pause','resume','archive'},'pregnant':{'set_edd','set_lmp','birth','end','pause','resume','archive'},'postpartum':{'correct_birth','correct_birth_record','pause','resume','archive'},'ended':{'pause','resume','archive'}}
 current=today(get(c,'family',fid).get('timezone',dates.DEFAULT_TIMEZONE))
 if event not in allowed.get(before,set()):raise Problem(409,'当前阶段不支持这项变更')
 reason=data.get('reason','')
 if not isinstance(reason,str) or len(reason)>300:raise Problem(400,'修订说明最多 300 字')
 if event in ('set_edd','set_lmp'):
  if event=='set_lmp':
   lmp=date(d)
   if lmp>current:raise Problem(400,'末次月经日期不能在未来')
   if p.get('edd') and p.get('dating_source')=='clinician_entered':raise Problem(409,'已使用医生确认日期，不能用末次月经估算覆盖；如需修订请录入医生确认的预产期')
   p['lmp']=d;d=str(dates.estimate_edd(d))
  edd=date(d)
  if edd<current-datetime.timedelta(days=35) or edd>current+datetime.timedelta(days=280):raise Problem(400,'预产期超出可录入范围，请核对')
  source='lmp_estimate' if event=='set_lmp' else data.get('source','user_entered')
  if source not in dates.SOURCES:raise Problem(400,'日期来源不正确')
  if source=='lmp_estimate' and p.get('dating_source')=='clinician_entered':raise Problem(409,'不能用末次月经估算覆盖医生确认日期')
  p['edd']=d;p['dating_source']=source;p['state']='pregnant';p['dating_revision']=p.get('dating_revision',0)+1
 elif event in ('birth','correct_birth'):
  birth=date(d)
  if birth>current:raise Problem(400,'出生日期不能在未来')
  if birth<current-datetime.timedelta(days=730):raise Problem(400,'请核对出生日期')
  p['birth_date']=d;p['state']='postpartum'
  if event=='birth':
   count=data.get('babies',1)
   if type(count) is not int or count not in (1,2,3):raise Problem(400,'宝宝数量需为 1—3')
   outcomes=data.get('birth_outcomes')
   if outcomes is not None:
    if user!=p['subject_id']:raise Problem(403,'分娩结果由本孕次本人以监护人身份确认')
    if data.get('guardian_consent') is not True:raise Problem(400,'分娩结果需要监护人单独确认')
    if not isinstance(outcomes,list) or len(outcomes)!=count or any(x not in ('growing','hospitalized','lost') for x in outcomes):raise Problem(400,'请逐个确认宝宝状态，数量需与分娩记录一致')
   p['birth_event_id']=event_id
   for i in range(count):
    baby={'id':uid(),'pregnancy_id':p['id'],'birth_date':d,'birth_date_follows_pregnancy':True,'name':'宝宝'+(str(i+1) if count>1 else ''),'status':outcomes[i] if outcomes is not None else 'unconfirmed'}
    if outcomes is not None:baby['status_date']=d
    saved=put(c,'baby',baby,fid)
    if outcomes is not None:put(c,'baby_event',{'id':uid(),'event':'birth_confirmation','pregnancy_id':p['id'],'baby_id':baby['id'],'actor':user,'before':dict(baby,status='unconfirmed'),'after':saved,'reason':'','effective_date':d,'guardian_notice_version':'1.0','created_at':now()},fid)
   lifecycle.restore_postpartum_tasks(c,fid,p)
  else:
   for b in rows(c,'baby',fid):
    if b['pregnancy_id']==p['id'] and b['status']!='entry_corrected' and b.get('birth_date_follows_pregnancy',True):
     if b.get('status_date') and date(b['status_date'])<birth:raise Problem(409,'出生日期晚于已记录的宝宝状态日期，请先核对记录')
     b['birth_date']=d;put(c,'baby',b,fid)
 elif event=='correct_birth_record':lifecycle.correct_birth(c,fid,user,p,data,event_id)
 elif event=='end':
  if date(d)>current:raise Problem(400,'结束日期不能在未来')
  p['end_date']=d;p['state']='ended';p['paused']=True
 elif event=='pause':p['paused']=True
 elif event=='resume':p['paused']=False
 elif event=='archive':p['archived']=True;p['paused']=True
 p['revision']+=1;put(c,'pregnancy',p,fid);templates.sync(c,fid,p)
 audit(c,fid,user,'health.transition',{'pregnancy_id':p['id'],'event':event,'before':before,'after':p['state'],'before_revision':before_revision,'revision':p['revision'],'effective_date':d if event in ('set_edd','set_lmp','birth','correct_birth','end') else str(current),'dating_source':p.get('dating_source'),'reason':reason.strip()})
 if event in ('birth','end','archive','pause','correct_birth_record'):
  for n in rows(c,'notification',fid):
   if n.get('pregnancy_id')==p['id'] and not n['read']:n['cancelled']=True;put(c,'notification',n,fid)
 if event in ('birth','end'):
  for t in rows(c,'task',fid):
   if t.get('pregnancy_id')==p['id'] and t.get('stage')=='pregnant' and t['status'] not in ('done','skipped','cancelled'):
    invalidate_approvals(c,fid,'task',t['id']);t['status_before_lifecycle']=t['status'];t['status']='cancelled';t['lifecycle_cancelled_by']=event_id;put(c,'task',t,fid)
 after=get(c,'pregnancy',p['id'])
 put(c,'pregnancy_event',{'id':event_id,'pregnancy_id':p['id'],'actor':user,'event':event,'before':before_record,'after':after,'revision':p['revision'],'effective_date':d if event in ('set_edd','set_lmp','birth','correct_birth','end') else str(current),'reason':reason.strip(),'created_at':now()},fid)
 if event in ('set_edd','set_lmp','birth','correct_birth'):
  recipients=[r['user_id'] for r in c.execute('SELECT user_id FROM memberships WHERE family_id=? AND active=1',(fid,)) if can_health(c,fid,r['user_id'],after)]
  put(c,'outbox',{'id':'timeline-'+event_id,'event_id':event_id,'pregnancy_id':p['id'],'pregnancy_revision':p['revision'],'recipients':recipients,'status':'pending','attempts':0,'created_at':now(),'available_at':now()},fid)
 return {'state':p['state'],'event_id':event_id}

def mutate(c,user,fid,action,data):
 me=member(c,fid,user);role=me['role'];f=get(c,'family',fid)
 if action=='notification.preferences':
  prefs={'id':fid+'-'+user,'user_id':user,'enabled':data.get('enabled') is True};put(c,'notification_preferences',prefs,fid)
  if not prefs['enabled']:
   task_ids={t['id'] for t in rows(c,'task',fid)}
   for n in rows(c,'notification',fid):
    if n['recipient']==user and n.get('ref') in task_ids and not n['read'] and not n.get('cancelled'):n['cancelled']=True;put(c,'notification',n,fid)
  return {'ok':True}
 if action=='ai.activate_gift':
  gifts=[e for e in rows(c,'entitlement',fid) if e['owner']==user and e['benefit']=='ai_gift' and e['status']=='unactivated']
  if not gifts:raise Problem(409,'没有待激活的赠送 AI')
  e=gifts[0];e.update(status='active',starts_at=now(),ends_at=now()+2592000);put(c,'entitlement',e,fid);audit(c,fid,user,action,{});return {'ok':True}
 if action=='family.update':
  if role!='dad':raise Problem(403,'只有统筹者可以修改协作设置')
  version(f,data);mode=data.get('mode',f['mode'])
  if mode not in ('off','selected','all'):raise Problem(400,'确认模式不正确')
  zone=family_timezone(c,fid,data.get('timezone',f.get('timezone',dates.DEFAULT_TIMEZONE)))
  payload={'name':text(data.get('name',f['name']),40),'mode':mode,'timezone':zone,'version':f['version']}
  if (mode!=f['mode'] or zone!=f.get('timezone',dates.DEFAULT_TIMEZONE)) and f['mode']!='off':return propose(c,fid,user,'family','policy',payload,fid)
  if mode!='off' and not c.execute("SELECT 1 FROM memberships WHERE family_id=? AND role='mom' AND active=1",(fid,)).fetchone():raise Problem(409,'妈妈加入后才可启用确认')
  apply_family_settings(c,fid,payload)
  audit(c,fid,user,action,{})
 elif action=='invite.create':
  if role!='dad':raise Problem(403,'只有统筹者可以邀请家人')
  r=data.get('role','member')
  if r not in ('mom','member'):raise Problem(400,'邀请角色无效')
  token=secrets.token_urlsafe(18);put(c,'invite',{'id':uid(),'token_hash':hashlib.sha256(token.encode()).hexdigest(),'role':r,'expires_at':now()+86400,'used':False},fid);audit(c,fid,user,action,{})
  return {'invite_code':token,'expires_at':now()+86400}
 elif action in ('member.remove','member.leave','family.transfer'):
  target=data.get('user_id') if action!='member.leave' else user
  if role!='dad' and target!=user:raise Problem(403,'只有统筹者可管理其他成员')
  target_member=member(c,fid,target)
  if action=='family.transfer':
   if role!='dad' or target==user:raise Problem(400,'请选择另一位成员接任')
   if target_member['role']=='mom':raise Problem(409,'当前版本不支持妈妈兼任统筹，请转交给其他成员')
   cancel_transfers(c,fid)
   return {'transfer_id':put(c,'transfer',{'id':uid(),'from':user,'to':target,'status':'pending','expires_at':now()+86400},fid)['id']}
  if target==f['coordinator']:raise Problem(409,'统筹者请先完成转交')
  c.execute('UPDATE memberships SET active=0 WHERE family_id=? AND user_id=?',(fid,target))
  for g in rows(c,'consent',fid):
   if g['subject']==target or g['grantee']==target:g['revoked_at']=now();put(c,'consent',g,fid)
  if target_member['role']=='mom':apply_policy(c,fid,'off')
  cancel_transfers(c,fid,target)
  cancel_member_approvals(c,fid,target)
  clean_health_access(c,fid)
  recurring.stop_for_member(c,fid,target,user,'assignee_left')
  audit(c,fid,user,action,{'user_id':target})
 elif action=='transfer.accept':
  t=require_active(c,fid,'transfer',data.get('id'));version(t,data)
  if t['to']!=user or t['status']!='pending' or t['expires_at']<=now():raise Problem(403,'无法接受该转交')
  if f['coordinator']!=t['from']:raise Problem(409,'家庭统筹已变更，请重新发起转交')
  member(c,fid,t['from'],('dad',))
  # Preserve mother's role; coordinator is an independent capability.
  if role=='mom':raise Problem(409,'当前版本不支持妈妈兼任统筹，请转交给其他成员')
  c.execute("UPDATE memberships SET role='member' WHERE family_id=? AND user_id=?",(fid,t['from']));c.execute("UPDATE memberships SET role='dad' WHERE family_id=? AND user_id=?",(fid,user));f['coordinator']=user;put(c,'family',f,fid);t['status']='accepted';put(c,'transfer',t,fid)
  cancel_transfers(c,fid)
  cancel_member_approvals(c,fid,t['from'])
  for inv in rows(c,'invite',fid):
   if not inv['used']:inv['used']=True;put(c,'invite',inv,fid)
  for g in rows(c,'consent',fid):
   if g['grantee']==t['from'] and g['scope']=='health_edit' and not g.get('revoked_at'):g['revoked_at']=now();put(c,'consent',g,fid)
  audit(c,fid,user,'family.transferred',{'from':t['from'],'to':user})
 elif action in ('task_series.stop','task_series.update','task_series.pause','task_series.resume'):
  if role!='dad':raise Problem(403,'只有统筹者可以管理重复安排')
  series=require_active(c,fid,'task_series',data.get('id'));version(series,data)
  if action=='task_series.update':return recurring.update(c,fid,series,user,data)
  return {'task_series.stop':recurring.stop,'task_series.pause':recurring.pause,'task_series.resume':recurring.resume}[action](c,fid,series,user)
 elif action in ('item.create','item.update','item.delete','item.restore'):
  return bag.mutate(c,user,fid,action,data)
 elif action in ('task.create','appointment.create'):
  if action=='task.create' and data.get('repeat','none')!='none':return recurring.create(c,fid,user,data)
  if action=='appointment.create':
   if role not in ('dad','mom'):raise Problem(403,'没有预约管理权限')
   if f.get('active_pregnancy_id'):
    p=get(c,'pregnancy',f['active_pregnancy_id'])
    if not can_edit_health(c,fid,user,p):raise Problem(403,'需要健康资料本人授权代录')
   elif role!='mom':raise Problem(403,'预约资料需由本人建立健康档案后录入')
   due=str(date(data.get('date')));obj={'id':uid(),'title':text(data.get('title'),100),'date':due,'note':text(data.get('note') or '以机构实际安排为准',500),'owner':user,'status':'scheduled','pregnancy_id':f.get('active_pregnancy_id'),'reminded':False};put(c,'appointment',obj,fid)
  else:
   if role!='dad':raise Problem(403,'只有统筹者可以新建与分配家庭事项')
   assignee=data.get('assignee') or user;member(c,fid,assignee)
   due=data.get('due_date') or None
   if due:date(due)
   if action=='task.create':obj={'id':uid(),'title':text(data.get('title'),100),'note':str(data.get('note',''))[:500],'type':data.get('type','自定义'),'assignee':assignee,'status':'todo','due_date':due,'created_at':now(),'pregnancy_id':f.get('active_pregnancy_id'),'stage':get(c,'pregnancy',f['active_pregnancy_id'])['state'] if f.get('active_pregnancy_id') else None};put(c,'task',obj,fid)
   if assignee!=user:notify(c,fid,assignee,'你收到一项新的家庭安排')
  audit(c,fid,user,action,{'id':obj['id']});return {'id':obj['id']}
 elif action in ('task.update','appointment.update'):
  kind=action.split('.')[0];obj=require_active(c,fid,kind,data.get('id'));version(obj,data);original=dict(obj)
  if kind=='task' and not can_view_task(c,fid,user,obj):raise Problem(403,'此事项需要相应资料的查看授权')
  if kind=='task' and obj.get('series_id') and role=='member' and obj['assignee']!=user:raise Problem(403,'只能操作自己负责的重复任务实例')
  if kind=='task' and obj.get('series_stopped'):raise Problem(409,'此重复安排已停止，实例仅保留历史；需要继续时请新建任务')
  if kind=='task' and obj.get('series_locked'):raise Problem(409,'此实例已暂停或因规则变更关闭，不能直接修改；请管理重复安排')
  if kind=='appointment':
   if obj['owner']!=user:raise Problem(403,'只有录入人可修改预约')
   if obj.get('pregnancy_id') and not can_edit_health(c,fid,user,get(c,'pregnancy',obj['pregnancy_id'])):raise Problem(403,'健康资料代录授权已失效')
   obj['status']=data.get('status',obj['status'])
   if obj['status'] not in ('scheduled','done','cancelled'):raise Problem(400,'预约状态不正确')
   if 'date' in data:obj['date']=str(date(data['date']))
   if 'title' in data:obj['title']=text(data['title'],100)
   if 'note' in data:obj['note']=text(data['note'] or '以机构实际安排为准',500)
   if obj['date']!=original['date'] or obj['status']!=original['status']:reset_reminder(c,fid,obj)
  else:
   desired=data.get('status',obj['status'])
   if 'status' in data:obj.pop('lifecycle_cancelled_by',None);obj.pop('status_before_lifecycle',None)
   if desired not in ('todo','in_progress','done','skipped','cancelled') and not (desired=='pending' and 'status' not in data):raise Problem(400,'状态不正确')
   if 'assignee' in data or 'due_date' in data or 'title' in data or 'name' in data or 'note' in data:
    if role!='dad':raise Problem(403,'只有统筹者可以编辑分工与内容')
    for key in ('title','name','note','due_date','assignee'):
     if key in data:
      if key=='assignee':member(c,fid,data[key])
      if key=='due_date' and data[key]:date(data[key])
      if key in ('title','name'):text(data[key],100)
      obj[key]=data[key]
      if key=='due_date':obj['user_override']=True
   if obj.get('series_id') and any(obj.get(k)!=original.get(k) for k in ('title','note','assignee','due_date')):obj['series_override']=True
   if desired!=obj['status']:
    if obj['assignee']!=user:raise Problem(403,'只能提交自己负责事项的执行结果')
    if desired=='done' and f['mode']!='off' and role!='mom' and (f['mode']=='all' or kind=='task'):
     result=propose(c,fid,user,kind,'complete',{'version':obj['version']},obj['id']);obj['status']='pending';reset_reminder(c,fid,obj);put(c,kind,obj,fid)
     # approval must bind the pending resource version.
     a=get(c,'approval',result['approval_id']);a['payload']['version']=obj['version']+1;put(c,'approval',a,fid);return result
    obj['status']=desired;obj['completed_at']=now() if desired=='done' else None
   for a in rows(c,'approval',fid):
    if a['target']==obj['id'] and a['status']=='pending':a['status']='cancelled';put(c,'approval',a,fid)
   if obj['status']=='pending':obj['status']='in_progress'
   if any(obj.get(k)!=original.get(k) for k in ('status','assignee','due_date')):reset_reminder(c,fid,obj)
  put(c,kind,obj,fid);audit(c,fid,user,action,{'id':obj['id']})
 elif action=='approval.decide':
  a=require_active(c,fid,'approval',data.get('id'));version(a,data)
  if a['approver']!=user:raise Problem(403,'只能由指定确认人处理')
  if a['status']!='pending' or a['expires_at']<=now():raise Problem(409,'该确认已失效')
  if a['policy_version']!=f['policy_version']:raise Problem(409,'确认策略已变更，请重新提交')
  decision=data.get('decision')
  if decision not in ('approved','rejected'):raise Problem(400,'确认结果不正确')
  if a['resource']=='family':
   if f['version']!=a['payload']['version']:raise Problem(409,'家庭设置已变更，请重新发起')
   if decision=='approved':apply_family_settings(c,fid,a['payload'])
  else:
   obj=require_active(c,fid,a['resource'],a['target'])
   if obj['version']!=a['payload']['version'] or obj['status']!='pending':raise Problem(409,'事项内容已变更，请重新提交')
   if not can_view_task(c,fid,user,obj):raise Problem(403,'此事项需要相应资料的查看授权')
   if decision=='approved':complete(c,fid,a['resource'],obj)
   else:obj['status']='in_progress';put(c,a['resource'],obj,fid)
  a['status']=decision;a['decided_at']=now();a['reason']=str(data.get('reason',''))[:300];put(c,'approval',a,fid);notify(c,fid,a['proposer'],'你的家庭确认申请已有结果');audit(c,fid,user,action,{'id':a['id'],'decision':decision})
 elif action=='pregnancy.create':
  if role!='mom':raise Problem(403,'请由妈妈本人建立自己的健康时间轴')
  if f.get('active_pregnancy_id'):
   old=get(c,'pregnancy',f['active_pregnancy_id'])
   if not old.get('archived'):raise Problem(409,'请先归档当前孕次')
  if data.get('consent') is not True:raise Problem(400,'需要本人单独同意处理健康日期')
  p={'id':uid(),'subject_id':user,'state':'dating','paused':False,'archived':False,'edd':None,'birth_date':None,'revision':1,'created_at':now()};put(c,'pregnancy',p,fid);f['active_pregnancy_id']=p['id'];put(c,'family',f,fid);audit(c,fid,user,'health.consent',{'purpose':'health_storage','notice_version':'1.0'})
 elif action=='pregnancy.event':
  p=pregnancy_for_event(c,user,fid,data)
  if 'preview_token' in data and data['preview_token']!=transition_fingerprint(c,user,fid,data):raise Problem(409,'预览后家庭安排已变化，请重新预览再提交')
  return transition(c,fid,user,p,data)
 elif action=='baby.update':
  if 'preview_token' in data and data['preview_token']!=transition_fingerprint(c,user,fid,data):raise Problem(409,'预览后家庭安排已变化，请重新预览')
  return lifecycle.record_baby(c,fid,user,data)
 elif action=='consent.update':
  if role!='mom':raise Problem(403,'健康资料仅由本人授权')
  grantee=data.get('grantee');scope=data.get('scope')
  if scope not in ('health_read','health_edit','ai_context','baby_read'):raise Problem(400,'授权用途无效')
  if scope=='baby_read' and data.get('enabled') is True and data.get('guardian_consent') is not True:raise Problem(400,'共享宝宝资料需监护人单独确认')
  member(c,fid,grantee)
  if scope=='health_edit' and grantee!=f['coordinator']:raise Problem(400,'代录仅授予家庭统筹者')
  for g in rows(c,'consent',fid):
   if g['subject']==user and g['grantee']==grantee and g['scope']==scope and not g.get('revoked_at'):g['revoked_at']=now();put(c,'consent',g,fid)
  if data.get('enabled') is True:put(c,'consent',{'id':uid(),'subject':user,'grantee':grantee,'scope':scope,'notice_version':'1.0','granted_at':now(),'revoked_at':None},fid)
  clean_health_access(c,fid)
  audit(c,fid,user,'health.consent.changed',{'scope':scope,'enabled':data.get('enabled') is True})
 elif action=='notification.read':
  n=require_active(c,fid,'notification',data.get('id'))
  if n['recipient']!=user:raise Problem(403,'无权读取此提醒')
  n['read']=True;put(c,'notification',n,fid)
 else:raise Problem(404,'暂不支持此操作')
 return {'ok':True}

def join(c,user,code):
 token_hash=hashlib.sha256(text(code,100).encode()).hexdigest()
 inv=next((i for i in rows(c,'invite') if hmac.compare_digest(i['token_hash'],token_hash)),None)
 if not inv or inv['used'] or inv['expires_at']<=now():raise Problem(400,'邀请码无效或已过期')
 row=c.execute("SELECT family_id FROM records WHERE kind='invite' AND id=?",(inv['id'],)).fetchone();fid=row['family_id']
 if c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,user)).fetchone():raise Problem(409,'你已经加入这个家庭')
 if c.execute('SELECT count(*) FROM memberships WHERE family_id=? AND active=1',(fid,)).fetchone()[0]>=5:raise Problem(409,'家庭最多支持 5 位成员')
 if inv['role']=='mom' and c.execute("SELECT 1 FROM memberships WHERE family_id=? AND role='mom' AND active=1",(fid,)).fetchone():raise Problem(409,'家庭已绑定妈妈')
 c.execute('INSERT INTO memberships VALUES(?,?,?,1) ON CONFLICT(family_id,user_id) DO UPDATE SET role=excluded.role,active=1',(fid,user,inv['role']));inv['used']=True;put(c,'invite',inv,fid);audit(c,fid,user,'member.join',{});return {'family_id':fid}

def seed_knowledge(c):
 for i,(topic,title,body) in enumerate(KNOWLEDGE):
  if not c.execute("SELECT 1 FROM records WHERE kind='knowledge' AND id=?",('base-'+str(i),)).fetchone():put(c,'knowledge',{'id':'base-'+str(i),'topic':topic,'title':title,'body':body,'status':'published','clinical':False,'source':'产品编辑 · 一般家庭生活内容','source_url':None,'content_version':1,'reviewer':'初始生活内容','published_at':now(),'valid_until':None})

def published_knowledge(c):
 result=[]
 for k in rows(c,'knowledge'):
  if k.get('status')!='published' or k.get('clinical'):continue
  until=k.get('valid_until')
  if until:
   try:
    if date(until)<today():continue
   except Problem:continue
  result.append(k)
 return result

def citations_current(c,result):
 current={k['id']:k['content_version'] for k in published_knowledge(c)}
 return all(current.get(ref['id'])==ref['version'] for ref in result.get('citations',[]))

def publish_knowledge(c,user,data):
 if user!=os.environ.get('ADMIN_USER_ID'):raise Problem(403,'需要系统内容管理员权限')
 if data.get('clinical'):raise Problem(409,'本版不发布医疗生成知识，请先完成专业审校发布流程')
 id=data.get('id') or uid();old=get(c,'knowledge',id) if data.get('id') else None
 if old:version(old,data)
 status=data.get('status','draft')
 if status not in ('draft','published','retired'):raise Problem(400,'内容状态不正确')
 until=data.get('valid_until',old.get('valid_until') if old else None) or None
 if until:
  until=str(date(until))
  if status=='published' and date(until)<today():raise Problem(400,'不能发布已过有效期的知识')
 k={'id':id,'title':text(data.get('title'),100),'body':text(data.get('body'),20000),'topic':text(data.get('topic'),40),'clinical':False,'source':text(data.get('source'),300),'content_version':old['content_version']+1 if old else 1,'status':status,'reviewer':user,'published_at':now() if status=='published' else None,'valid_until':until}
 put(c,'knowledge_version',dict(k,id=id+'-v'+str(k['content_version']),knowledge_id=id))
 put(c,'knowledge',k);audit(c,None,user,'knowledge.'+status,{'id':id,'content_version':k['content_version']})
 return {'id':id}

def tick():
 with transaction() as c:
  for a in rows(c,'appointment'):
   if a['status']!='scheduled' or a.get('reminded'):continue
   r=c.execute("SELECT family_id FROM records WHERE kind='appointment' AND id=?",(a['id'],)).fetchone();fid=r['family_id']
   current=today(get(c,'family',fid).get('timezone',dates.DEFAULT_TIMEZONE))
   if not current<=date(a['date'])<=current+datetime.timedelta(days=3):continue
   p=get(c,'pregnancy',a['pregnancy_id']) if a.get('pregnancy_id') else None
   if p and (p['paused'] or p['archived'] or p['state']=='ended'):continue
   if p and not can_health(c,fid,a['owner'],p):continue
   if not c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,a['owner'])).fetchone():continue
   key='appointment-'+a['id']+('-r'+str(a['reminder_revision']) if a.get('reminder_revision') else '')
   n={'id':key,'recipient':a['owner'],'title':'有一项预约即将到来，请查看安排','ref':a['id'],'pregnancy_id':a.get('pregnancy_id'),'read':False,'created_at':now()}
   if not c.execute("SELECT 1 FROM records WHERE kind='notification' AND id=?",(n['id'],)).fetchone():put(c,'notification',n,fid)
   a['reminded']=True;put(c,'appointment',a,fid)
  for f in rows(c,'family'):expire_approvals(c,f['id']);recurring.sync(c,f['id']);process_outbox(c,f['id'])

  for task in rows(c,'task'):
   if task['status'] not in ('todo','in_progress') or not task.get('due_date'):continue
   fid=c.execute("SELECT family_id FROM records WHERE kind='task' AND id=?",(task['id'],)).fetchone()[0]
   current=today(get(c,'family',fid).get('timezone',dates.DEFAULT_TIMEZONE))
   if not current<=date(task['due_date'])<=current+datetime.timedelta(days=1):continue
   if not c.execute('SELECT 1 FROM memberships WHERE family_id=? AND user_id=? AND active=1',(fid,task['assignee'])).fetchone():continue
   if task.get('pregnancy_id'):
    p=get(c,'pregnancy',task['pregnancy_id'])
    if p['paused'] or p['archived'] or p['state']=='ended':continue
    if not can_view_task(c,fid,task['assignee'],task):continue
   prefs=next((x for x in rows(c,'notification_preferences',fid) if x['user_id']==task['assignee']),None)
   if prefs and not prefs['enabled']:continue
   id='task-'+task['id']+'-'+str(task.get('due_date'))+('-r'+str(task['reminder_revision']) if task.get('reminder_revision') else '')
   if not c.execute("SELECT 1 FROM records WHERE kind='notification' AND id=?",(id,)).fetchone():
    put(c,'notification',{'id':id,'recipient':task['assignee'],'title':'有一项家庭安排临近，请查看待办','ref':task['id'],'pregnancy_id':task.get('pregnancy_id'),'health_sensitive':bool(task.get('private_health_anchor')),'read':False,'created_at':now()},fid)
