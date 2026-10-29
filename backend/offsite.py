#!/usr/bin/env python3
"""Optional HTTPS encrypted-backup replica and generic webhook health alarm.
No plaintext database, recovery key, URL credential, or family data is uploaded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import backup_jobs as jobs
import maintenance as m

MAX_BYTES=256*1024*1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise m.MaintenanceError('异地端点不允许重定向')


def endpoint(name):
    value=os.environ.get(name,'');url=urllib.parse.urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise m.MaintenanceError('请配置无账号、查询参数或片段的 HTTPS 异地端点')
    return value.rstrip('/')


def remote(method,url,data=None,max_bytes=MAX_BYTES):
    headers={'Content-Type':'application/octet-stream'}
    token=os.environ.get('BABA_OFFSITE_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,data=data,method=method,headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:
            result=response.read(max_bytes+1)
            if len(result)>max_bytes:raise ValueError('too large')
            return result
    except Exception:raise m.MaintenanceError('异地请求失败；请核对存储权限、网络和 TLS，不输出端点或密钥')


def sync(database,directory,kind):
    root=jobs.private_directory(directory);base=endpoint('BABA_OFFSITE_URL')
    with jobs.job_lock(root):
        state_path=root/(kind+'.offsite.json');previous=jobs.read_json(state_path) if state_path.exists() else {}
        state={'version':1,'kind':kind,'state':'running','started_at':jobs.clock(),'last_success':previous.get('last_success')}
        jobs.write_json(state_path,state)
        try:
            local=jobs.status(database,root,kind,26 if kind=='full' else 1)
            bundle=root/kind/local['run_id'];manifest=jobs.read_bundle(bundle)
            payload_path=bundle/'payload.cms'
            if payload_path.stat().st_size>MAX_BYTES:raise m.MaintenanceError('密文超过异地工具单批 256 MiB 限制')
            payload=payload_path.read_bytes();marker=(bundle/'manifest.json').read_bytes()
            if hashlib.sha256(payload).hexdigest()!=manifest['encrypted_sha256']:raise m.MaintenanceError('上传前密文校验失败')
            prefix='/'.join((base,urllib.parse.quote(manifest['instance_id'],safe=''),kind,manifest['run_id']))
            # Replays upload identical immutable batch bytes. Manifest is the commit marker.
            remote('PUT',prefix+'/payload.cms',payload)
            if hashlib.sha256(remote('GET',prefix+'/payload.cms')).hexdigest()!=manifest['encrypted_sha256']:raise m.MaintenanceError('异地回读密文校验失败')
            remote('PUT',prefix+'/manifest.json',marker)
            if remote('GET',prefix+'/manifest.json',max_bytes=100000)!=marker:raise m.MaintenanceError('异地清单回读校验失败')
            success={'run_id':manifest['run_id'],'instance_id':manifest['instance_id'],'created_at':manifest['created_at'],
                     'verified_at':jobs.clock(),'encrypted_sha256':manifest['encrypted_sha256'],'destination':hashlib.sha256(base.encode()).hexdigest()}
            state.update(state='success',finished_at=jobs.clock(),last_success=success);jobs.write_json(state_path,state)
            return {'ok':True,'kind':kind,'run_id':manifest['run_id'],'verified':True}
        except Exception:
            state.update(state='failed',finished_at=jobs.clock());jobs.write_json(state_path,state);raise


def check(database,directory,kind):
    root=jobs.private_directory(directory);base=endpoint('BABA_OFFSITE_URL')
    local=jobs.status(database,root,kind,26 if kind=='full' else 1)
    state=jobs.read_json(root/(kind+'.offsite.json'));success=state.get('last_success') or {}
    if state.get('state')!='success' or success.get('destination')!=hashlib.sha256(base.encode()).hexdigest() or success.get('instance_id')!=jobs.database_identity(database):raise m.MaintenanceError('异地副本尚未成功，或目的地/实例已变化')
    age=jobs.clock()-success.get('created_at',0)
    if not 0<=age<=(26 if kind=='full' else 1)*3600:raise m.MaintenanceError('异地副本已过期或时钟异常')
    if not jobs.RUN_ID.fullmatch(success.get('run_id','')):raise m.MaintenanceError('异地回执无效')
    prefix='/'.join((base,urllib.parse.quote(success['instance_id'],safe=''),kind,success['run_id']))
    marker=json.loads(remote('GET',prefix+'/manifest.json',max_bytes=100000));jobs.validate_manifest(marker)
    if any(marker[k]!=success[k] for k in ('instance_id','run_id','encrypted_sha256','created_at')):raise m.MaintenanceError('异地清单与回执不一致')
    remote('HEAD',prefix+'/payload.cms',max_bytes=0)
    return {'ok':True,'kind':kind,'age_seconds':age,'local_run_id':local['run_id'],'offsite_run_id':success['run_id']}


def alarm(ok,kind):
    # Operator-configured endpoint only; never use a backup token for an alarm service.
    url=endpoint('BABA_BACKUP_ALERT_URL')
    data=json.dumps({'service':'baba-backup','kind':kind,'status':'healthy' if ok else 'needs_attention'}).encode()
    headers={'Content-Type':'application/json'}
    if os.environ.get('BABA_BACKUP_ALERT_TOKEN'):headers['Authorization']='Bearer '+os.environ['BABA_BACKUP_ALERT_TOKEN']
    try:
        with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url,data=data,headers=headers),timeout=10) as response:response.read(10000)
    except Exception:raise m.MaintenanceError('备份告警端点请求失败')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('operation',choices=['sync','check']);p.add_argument('--db',required=True);p.add_argument('--directory',required=True);p.add_argument('--kind',choices=['full','ledger'],required=True);p.add_argument('--alert',action='store_true',help='向已配置运维端点发送通用健康状态，不包含业务资料')
    args=p.parse_args();ok=False
    try:
        result=(sync if args.operation=='sync' else check)(args.db,args.directory,args.kind);ok=True
    except Exception:result={'ok':False,'error':'异地备份未通过，请在可信主机核对本地批次、存储权限和网络'}
    if args.alert:
        try:alarm(ok,args.kind)
        except Exception:result={'ok':False,'error':'备份检查或告警投递失败'};ok=False
    print(json.dumps(result,ensure_ascii=False));return 0 if ok else 1


if __name__=='__main__':sys.exit(main())
