"""Non-clinical life-planning templates. No medical appointment is inferred here."""
TEMPLATES=[
 ('communication','pregnant',-260,'聊聊彼此期待的陪伴方式','先听完对方，再明确能分担的一件事','陪伴'),
 ('document-folder','pregnant',-245,'整理证件与就诊资料的存放位置','只整理文件，不复制或公开他人健康内容','资料准备'),
 ('work-plan','pregnant',-210,'了解双方工作与休息安排','核对单位请假流程，记录需要提前准备的材料','家庭协作'),
 ('housework','pregnant',-180,'重新分配家务与采购','按实际精力调整，每项指定负责人','家庭协作'),
 ('budget','pregnant',-150,'一起整理家庭预算','盘点已有用品，区分必需和可延后支出','物品准备'),
 ('support-network','pregnant',-120,'讨论产后的支持人选','确认谁能在哪段时间提供什么帮助','家庭协作'),
 ('bag-inventory','pregnant',-90,'盘点待产用品，不急着买齐','按医院要求与家里已有的物品列缺口','物品准备'),
 ('leave-material','pregnant',-70,'核对陪产休假材料与流程','政策可能不同，以单位和当地规定为准','资料准备'),
 ('hospital-route','pregnant',-50,'熟悉医院路线和交通方案','记录常用入口和联系人，不代替急救服务','出行准备'),
 ('hospital-bag','pregnant',-35,'核对机构给出的入院物品清单','只补缺少的物品，不大量囤货','物品准备'),
 ('contacts','pregnant',-21,'核对家庭紧急联系人','由家人确认电话号码与联系顺序','家庭协作'),
 ('home-ready','pregnant',-14,'一起整理回家后的休息空间','保持整洁，常用物品拿取方便','生活准备'),
 ('last-check','pregnant',-7,'再核对一次行李与证件','结合实际机构通知核对即可','物品准备'),
 ('postpartum-team','postpartum',1,'确定这一周的照护与家务分工','优先保证妈妈休息，按实际情况安排轮换','家庭协作'),
 ('birth-document','postpartum',5,'了解出生证明的办理流程','核对当地机构所需材料与时间','资料准备'),
 ('insurance','postpartum',10,'了解新生儿医保与户籍要求','政策按城市不同，向当地机构核对','资料准备'),
 ('rest-plan','postpartum',14,'回顾家人的休息是否足够','不追求完美，及时调整分工','陪伴'),
 ('help-review','postpartum',28,'聊聊最近哪些帮助最有用','保留真正减轻负担的安排','家庭协作'),
 ('documents-store','postpartum',45,'整理宝宝证件的存放位置','由监护人管理，分享遵循最小必要原则','资料准备'),
 ('family-time','postpartum',90,'安排一次轻松的家庭时间','按每个人实际精力安排','陪伴'),
 ('growth-archive','postpartum',180,'整理半年里想保存的小事','照片和记录需尊重家人意愿','陪伴'),
 ('birthday-plan','postpartum',350,'一起计划简简单单的周岁纪念','回顾陪伴与成长，不必攀比','陪伴')]
def sync(c,fid,p):
 import domain as d,datetime,hashlib,lifecycle,task_details
 if p.get('archived') or p['state'] in ('dating','ended'):return
 anchor=d.date(p['edd'] if p['state']=='pregnant' else p['birth_date'])
 coordinator=d.get(c,'family',fid)['coordinator']
 for key,stage,offset,title,note,category in TEMPLATES:
  if stage!=p['state']:continue
  if key in lifecycle.QUIET_TEMPLATE_KEYS and lifecycle.quiet(c,fid,p['id']):continue
  due=anchor+datetime.timedelta(days=offset)
  # Avoid flooding a newly recorded pregnancy with past dates; existing history is never altered.
  id='auto-'+hashlib.sha256((p['id']+key).encode()).hexdigest()[:24]
  row=c.execute("SELECT body FROM records WHERE kind='task' AND id=?",(id,)).fetchone()
  if row:
   import json
   t=json.loads(row['body'])
   if t['status'] not in ('done','skipped','cancelled') and not t.get('user_override') and t['due_date']!=str(due):
    d.invalidate_approvals(c,fid,'task',t['id']);d.reset_reminder(c,fid,t)
    if t['status']=='pending':t['status']='in_progress'
    t['due_date']=str(due);t['anchor_revision']=p['revision'];d.put(c,'task',t,fid)
  elif due>=d.today(d.get(c,'family',fid).get('timezone','Asia/Shanghai'))-datetime.timedelta(days=7):
   d.put(c,'task',{'id':id,**task_details.template_content(c,category),'template_key':key,'template_version':2,'pregnancy_id':p['id'],'stage':stage,'title':title,'note':note,'type':category,'assignee':coordinator,'status':'todo','due_date':str(due),'anchor_revision':p['revision'],'private_health_anchor':True,'user_override':False,'created_at':d.now()},fid)
