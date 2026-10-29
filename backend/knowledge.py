"""Versioned non-clinical reading metadata and per-reader curated delivery."""
import hashlib
import re
import urllib.parse

ROLES=('dad','mom','member')
STAGES=('general','dating','early','middle','late','postpartum_early','postpartum_later','ended')


def code(value):
    import domain as d
    if value in ('',None,'all'):return 'all'
    if not isinstance(value,str) or not re.fullmatch(r'[A-Z]{2}(?:-[A-Z0-9]{1,12})?',value):raise d.Problem(400,'阅读地区使用大写代码，如 CN、CN-SH；留空只读通用内容')
    return value


def enum_list(value,allowed,label):
    import domain as d
    if not isinstance(value,list) or not value or len(value)>len(allowed) or any(type(v) is not str or v not in allowed for v in value) or len(set(value))!=len(value):raise d.Problem(400,label+'需为不重复的有效选项')
    return list(value)


def metadata(data,old=None):
    import domain as d
    previous=old or {}
    def field(key,default):return data.get(key,previous.get(key,default))
    url=field('source_url',None) or None
    if url:
        if not isinstance(url,str) or len(url)>1000:raise d.Problem(400,'来源链接过长或格式不正确')
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise d.Problem(400,'来源链接须为不含账号密码的 HTTPS 地址')
    regions=field('regions',['all'])
    if not isinstance(regions,list) or not 1<=len(regions)<=20 or any(type(r) is not str for r in regions):raise d.Problem(400,'请填写 1—20 个适用地区')
    regions=[code(r) for r in regions]
    if len(set(regions))!=len(regions) or ('all' in regions and len(regions)>1):raise d.Problem(400,'通用地区不与其他地区重复设置')
    curated=field('member_curated',True)
    if type(curated) is not bool:raise d.Problem(400,'家人策展标记须为布尔值')
    valid_from=field('valid_from',None) or None
    if valid_from:valid_from=str(d.date(valid_from))
    source_date=field('source_published_at',None) or None
    if source_date:
        source_date=str(d.date(source_date))
        if d.date(source_date)>d.today():raise d.Problem(400,'来源发布日期不能在未来')
    return {'target_roles':enum_list(field('target_roles',list(ROLES)),ROLES,'目标角色'),
            'stages':enum_list(field('stages',['general']),STAGES,'适用阶段'),'regions':regions,'member_curated':curated,
            'source_url':url,'source_published_at':source_date,'valid_from':valid_from,
            'rights':d.text(field('rights','一般生活内容，转载许可未补充；请核对来源'),500),
            'applicability':d.text(field('applicability','一般家庭生活整理；不用于诊疗判断'),500)}


def preference(c,fid,user):
    import domain as d
    id=hashlib.sha256(d.dumps([fid,user,'reading']).encode()).hexdigest()
    return next((r for r in d.rows(c,'knowledge_preferences',fid) if r['id']==id),{'id':id,'user_id':user,'region':'all'})


def save(c,fid,user,data):
    import domain as d
    d.member(c,fid,user);row=preference(c,fid,user);region=code(data.get('region'))
    if row['region']!=region:
        import briefings
        briefings.invalidate_family(c,fid,user)
    row['region']=region;d.put(c,'knowledge_preferences',row,fid)
    return {'ok':True}


def for_reader(c,fid,user,pregnancy=None):
    import domain as d
    me=d.member(c,fid,user);region=preference(c,fid,user)['region'];stage='general'
    if pregnancy and not pregnancy.get('restricted') and not pregnancy.get('paused') and not pregnancy.get('archived'):
        stage=pregnancy.get('timeline',{}).get('phase','general')
    result=[]
    for k in d.published_knowledge(c):
        try:meta=metadata({},k)
        except (d.Problem,ValueError):continue
        if me['role'] not in meta['target_roles'] or (me['role']=='member' and not meta['member_curated']):continue
        if 'general' not in meta['stages'] and stage not in meta['stages']:continue
        if 'all' not in meta['regions'] and region not in meta['regions']:continue
        result.append(dict(k,**meta))
    return result


def available(c,fid,user):
    import domain as d,dates
    d.member(c,fid,user);family=d.get(c,'family',fid);p=None
    if family.get('active_pregnancy_id'):
        source=d.get(c,'pregnancy',family['active_pregnancy_id'])
        if d.can_health(c,fid,user,source):p=dict(source,timeline=dates.timeline(source,family.get('timezone','Asia/Shanghai')))
    return for_reader(c,fid,user,p)


def for_ai(c,fid,user):
    # Shared health dates may guide reading, but health_read is not permission to
    # disclose an inferred stage to a model. AI retrieval stays in general material.
    return for_reader(c,fid,user,None)


def read(c,fid,user,id):
    import domain as d
    item=next((k for k in available(c,fid,user) if k['id']==id),None)
    if not item:raise d.Problem(409,'内容已下架、到期或不在当前阅读范围，请刷新')
    return {'knowledge':item}
