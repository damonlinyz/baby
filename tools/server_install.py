#!/usr/bin/env python3
"""Plan or perform a first Ubuntu installation, bound to loopback only.

No Caddy, Docker, firewall, SSH, existing service or existing data is modified.
This is deliberately an initial installer, not an updater or uninstall command.
"""
import argparse
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import urllib.request
from zoneinfo import ZoneInfo

SOURCE='https://github.com/damonlinyz/baby.git'
CODE=Path('/opt/baba')
DATA=Path('/var/lib/baba')
CONFIG=Path('/etc/baba')
UNIT=Path('/etc/systemd/system/baba.service')
LOCK=Path('/run/lock/baba-install.lock')
PORT=8766


def validate(revision,domain):
    if not isinstance(revision,str) or not re.fullmatch('[a-f0-9]{40}',revision):raise ValueError('必须指定完整的 40 位 Git 提交，不接受可变分支或标签')
    if not isinstance(domain,str) or len(domain)>253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):raise ValueError('域名只能填写小写 ASCII 主机名，不含协议、端口、路径或命令')


def environment(domain):
    validate('0'*40,domain)
    return f'''# Managed first-install configuration; keep root-owned 0600.
HOST=127.0.0.1
PORT={PORT}
PUBLIC_ORIGIN=https://{domain}
BABA_DB={DATA}/private/baba.sqlite3
SERVICE_MODE=self_hosted
PAYMENTS_ENABLED=0
AI_SALES_APPROVED=0
WX_NOTIFY_ENABLED=0
WX_POSTER_ENABLED=0
AI_API_KEY=
PYTHONDONTWRITEBYTECODE=1
PYTHONUNBUFFERED=1
'''


def service():
    return f'''[Unit]
Description=Baba private family application
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=baba
Group=baba
WorkingDirectory={CODE}
EnvironmentFile={CONFIG}/service.env
ExecStart=/usr/bin/python3 {CODE}/backend/server.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={DATA}/private
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
TasksMax=128
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


def caddy(domain):
    validate('0'*40,domain)
    return f'''# Review and import into the existing Caddy configuration only after DNS is ready.
# This file is generated but is NOT automatically activated.
{domain} {{
    encode zstd gzip
    request_body {{
        max_size 128KB
    }}
    reverse_proxy 127.0.0.1:{PORT}
}}
'''


def plan(revision,domain):
    validate(revision,domain)
    return {'mode':'plan','source':SOURCE,'commit':revision,'public_origin':'https://'+domain,
            'listener':f'127.0.0.1:{PORT}','new_user':'baba',
            'new_paths':[str(p) for p in (CODE,DATA,CONFIG,UNIT)],
            'database':str(DATA/'private/baba.sqlite3'),
            'caddy_fragment':str(CONFIG/'Caddyfile.fragment'),
            'activates_caddy':False,'opens_firewall':False,'enables_payments':False,
            'installs_backup_timers':False,'installs_packages':False,
            'notice':'预览不修改系统；--apply 才执行首次安装。已有目录、账号、端口或服务冲突时停止。仅本机服务，不代表 HTTPS 或备份已上线。'}


def run(*args):
    return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()


def new_paths():
    for path in (CODE,DATA,CONFIG,UNIT):
        if os.path.lexists(path):raise ValueError('已有路径，拒绝覆盖或当作升级执行：'+str(path))


def preflight():
    if os.geteuid()!=0:raise ValueError('实际安装需在服务器控制台以 root 执行')
    system=Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$',system,re.M) or not re.search(r'^VERSION_ID="?24\.04"?$',system,re.M):raise ValueError('此安装器仅支持已核对的 Ubuntu 24.04')
    if not Path('/run/systemd/system').is_dir():raise ValueError('需要正在运行 systemd 的服务器，不在容器或当前工作区安装')
    for program in ('git','systemctl','useradd','systemd-analyze'):
        if not shutil.which(program):raise ValueError('缺少系统工具：'+program+'；请先确认系统软件安装方式')
    if not Path('/usr/bin/python3').is_file():raise ValueError('缺少 /usr/bin/python3')
    ZoneInfo('Asia/Shanghai')
    for path in (Path('/opt'),Path('/var/lib'),Path('/etc'),UNIT.parent):
        if path.is_symlink() or not path.is_dir():raise ValueError('安装父目录不符合预期：'+str(path))
    new_paths()
    for lookup in (pwd.getpwnam,grp.getgrnam):
        try:lookup('baba')
        except KeyError:pass
        else:raise ValueError('baba 账号或组已存在，需单独核对已有部署')
    if run('systemctl','show','--property=LoadState','--value','baba.service')!='not-found':raise ValueError('已存在 baba 服务，拒绝替换')
    for path in (CODE.parent,DATA.parent):
        if shutil.disk_usage(path).free<2*1024**3:raise ValueError('安装分区至少保留 2 GiB 可用空间，不自动清理任何现有数据')
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',PORT))
        except OSError:raise ValueError('本机 8766 端口已占用，不停止已有进程') from None


def checkout(target,revision):
    run('git','init','-q',str(target))
    run('git','-C',str(target),'remote','add','origin',SOURCE)
    run('git','-C',str(target),'fetch','--depth=1','origin',revision)
    run('git','-C',str(target),'checkout','--detach','FETCH_HEAD')
    if run('git','-C',str(target),'rev-parse','HEAD')!=revision:raise ValueError('下载源码与指定提交不一致')
    # Do not allow an archive/checkout to introduce links into privileged paths.
    entries=run('git','-C',str(target),'ls-tree','-r',revision).splitlines()
    if any(line.split()[0] not in ('100644','100755') for line in entries):raise ValueError('安装源码不接受符号链接或子模块')
    for name in ('backend/server.py','backend/domain.py','index.html','clients/app/manifest.json'):
        if not (target/name).is_file():raise ValueError('源码缺少应用文件')
    # The service account can read committed code, but cannot modify it.
    for parent,dirs,files in os.walk(target):
        os.chmod(parent,0o755)
        for name in files:os.chmod(Path(parent)/name,0o644)


def write_new(path,text,mode=0o600):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:output.write(text)


def healthy():
    # Local health verification only; never disable a remote connection's proxy/TLS.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(20):
        try:
            with opener.open(f'http://127.0.0.1:{PORT}/api/health',timeout=2) as response:body=json.loads(response.read(32768))
            flags=body.get('integrations',{})
            if body.get('ok') is True and flags.get('service_mode')=='self_hosted' and flags.get('sales_enabled') is False and flags.get('ai') is False:return True
        except (OSError,ValueError):pass
        time.sleep(0.5)
    return False


def install(revision,domain):
    result=plan(revision,domain)
    descriptor=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    with os.fdopen(descriptor,'r+') as lock:
        details=os.fstat(lock.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid!=os.geteuid():raise ValueError('安装锁不是本人拥有的普通文件')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('另一个安装进程正在运行') from None
        preflight()
        # Download and verify before creating an account, persistent data or unit.
        with tempfile.TemporaryDirectory(prefix='.baba-install-',dir=CODE.parent) as temp:
            source=Path(temp)/'source';checkout(source,revision)
            run('useradd','--system','--user-group','--home-dir',str(DATA),'--no-create-home','--shell','/usr/sbin/nologin','baba')
            user=pwd.getpwnam('baba')
            for path in (DATA,DATA/'private'):
                path.mkdir(mode=0o700);os.chown(path,user.pw_uid,user.pw_gid)
            CONFIG.mkdir(mode=0o750)
            write_new(CONFIG/'service.env',environment(domain))
            write_new(CONFIG/'Caddyfile.fragment',caddy(domain),0o644)
            write_new(CONFIG/'installed.json',json.dumps({'commit':revision,'domain':domain},indent=2)+'\n')
            if os.path.lexists(CODE):raise ValueError('源码目标在检查后被其他进程创建，停止安装')
            source.rename(CODE)
            write_new(UNIT,service(),0o644)
        try:
            run('systemd-analyze','verify',str(UNIT))
            run('systemctl','daemon-reload');run('systemctl','start','baba.service')
            if not healthy():raise ValueError('本机健康检查未通过；请查看 journalctl -u baba.service，保留现场与数据，不重复安装')
            run('systemctl','enable','baba.service')
        except Exception:
            subprocess.run(['systemctl','stop','baba.service'],check=False,capture_output=True)
            raise
        return {**result,'mode':'installed','health':'passed','notice':'本机后端健康检查通过。Caddy、DNS、HTTPS、自动备份仍需按部署文档完成；不要开放 8766 公网端口。'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--revision',required=True,help='固定的完整 Git 提交 SHA')
    p.add_argument('--domain',required=True,help='独立 App 子域名，不含 https://')
    p.add_argument('--apply',action='store_true',help='以 root 实际安装；默认仅输出计划')
    args=p.parse_args()
    try:print(json.dumps(install(args.revision,args.domain) if args.apply else plan(args.revision,args.domain),ensure_ascii=False,indent=2))
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else '请检查依赖、网络和 systemd 状态；保留现有文件，不重复安装。命令失败的具体输出只在服务器上核对，勿发送密钥。'
        p.exit(1,'安装未完成：'+message+'\n')


if __name__=='__main__':main()
