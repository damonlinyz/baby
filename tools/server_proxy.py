#!/usr/bin/env python3
"""Add one App site to the existing Caddyfile, with validation and rollback.

Standalone companion to server_install.py. Default is a non-mutating plan;
--apply requires root. --verify performs read-only DNS/backend/HTTPS checks.
"""
import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import tempfile
import time
import urllib.request

CONFIG=Path('/etc/caddy/Caddyfile')
FRAGMENT=Path('/etc/caddy/baba-app.caddy')
METADATA=Path('/etc/baba/installed.json')
LOCK=Path('/run/lock/baba-install.lock')
CADDY='/usr/bin/caddy'


def arguments(domain,address):
    if not isinstance(domain,str) or len(domain)>253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):raise ValueError('请填写小写域名，不含协议、端口、路径或命令')
    try:ip=ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:raise ValueError('请填写服务器的公网 IPv4 地址') from None
    if not ip.is_global:raise ValueError('此公网 HTTPS 接入流程需要公网 IPv4 地址')


def plan(domain,address):
    arguments(domain,address)
    return {'mode':'plan','domain':domain,'expected_ipv4':address,'config':str(CONFIG),
            'new_fragment':str(FRAGMENT),'upstream':'127.0.0.1:8766',
            'reloads':'caddy.service','modifies_database':False,'opens_firewall':False,
            'notice':'默认仅预览。--apply 检查 DNS 与本机服务，备份和验证后才重载 Caddy；失败回滚。--verify 只做可用性检查。'}


def fragment(domain):
    arguments(domain,'163.7.5.202')
    return f'''# Baba App site. Managed independently from existing sites.
{domain} {{
    encode zstd gzip
    request_body {{
        max_size 128KB
    }}
    reverse_proxy 127.0.0.1:8766
}}
'''.encode()


def run(*args):
    try:return subprocess.run(args,check=True,text=True,capture_output=True,timeout=40).stdout.strip()
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
        # Existing Caddy configuration/errors may contain credentials. Never echo them.
        raise ValueError('系统命令未通过：'+Path(args[0]).name+'；请在服务器本机检查错误，不分享含凭据的完整配置或日志') from None


def dns(domain,address):
    try:answers=socket.getaddrinfo(domain,443,type=socket.SOCK_STREAM)
    except OSError:raise ValueError('域名尚未解析，请等待 DNS 生效后重试') from None
    actual={str(ipaddress.ip_address(item[4][0])) for item in answers}
    if actual!={address}:raise ValueError('DNS 与目标 IPv4 不一致或存在尚未核对的 AAAA/CDN 记录；停止接入，请先核对解析')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('健康检查不接受跳转')


def health(url,local=False):
    handlers=[NoRedirect()]
    if local:handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(url,timeout=5) as response:
            raw=response.read(32769)
            if len(raw)>32768:return False
            body=json.loads(raw)
        return isinstance(body,dict) and body.get('ok') is True and body.get('schema_version')==1
    except (OSError,ValueError):return False


def https(domain,attempts=1):
    for attempt in range(attempts):
        if health('https://'+domain+'/api/health'):return 'passed'
        if attempt+1<attempts:time.sleep(2)
    return 'pending'


def verify(domain,address):
    result=plan(domain,address);dns(domain,address)
    local=health('http://127.0.0.1:8766/api/health',local=True)
    return {**result,'mode':'verify','dns':'matched','backend':'passed' if local else 'failed','https':https(domain),
            'notice':'https=passed 才表示本次 HTTPS 健康检查通过；pending 需核对证书、80/443 入口或出口回环，不关闭 TLS 验证。'}


def has_host(value,domain):
    if isinstance(value,dict):
        if isinstance(value.get('host'),list) and domain in value['host']:return True
        return any(has_host(v,domain) for v in value.values())
    if isinstance(value,list):return any(has_host(v,domain) for v in value)
    return False


def preflight(domain,address):
    if os.geteuid()!=0:raise ValueError('实际接入需在服务器控制台以 root 执行')
    for path in (CONFIG,CONFIG.parent):
        details=path.lstat()
        if path.is_symlink() or details.st_uid!=0 or details.st_mode&0o022:raise ValueError('Caddy 配置或父目录不是受保护的 root 路径，请先单独核对')
    if not CONFIG.is_file():raise ValueError('当前流程需要普通 Caddyfile，不替换其他格式或链接')
    if json.loads(METADATA.read_text()).get('domain')!=domain:raise ValueError('域名与已安装 App 的公共来源不一致，停止修改')
    for service in ('baba.service','caddy.service'):
        if run('systemctl','is-active',service)!='active':raise ValueError('服务尚未运行：'+service)
    command=run('systemctl','show','--property=ExecStart','--value','caddy.service')
    if not re.search(r'--config(?:=|\s+)'+re.escape(str(CONFIG))+r'(?:\s|$)',command):raise ValueError('Caddy 实际启动配置路径与预期不一致，停止修改')
    user=run('systemctl','show','--property=User','--value','caddy.service') or 'root'
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*\$?',user):raise ValueError('Caddy 服务账号需要单独核对')
    dns(domain,address)
    if not health('http://127.0.0.1:8766/api/health',local=True):raise ValueError('本机 App 健康检查未通过，不修改 Caddy')
    return user


def caddy_command(user,*args):
    return run(*(('runuser','-u',user,'--') if user!='root' else ()),CADDY,*args)


def new_file(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as output:
        output.write(data);output.flush();os.fsync(output.fileno())
        os.fchmod(output.fileno(),mode)


def stage(data,details):
    fd,name=tempfile.mkstemp(prefix='.baba-caddy-',dir=CONFIG.parent)
    path=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
            os.fchown(output.fileno(),details.st_uid,details.st_gid)
            os.fchmod(output.fileno(),stat.S_IMODE(details.st_mode))
    except BaseException:
        path.unlink(missing_ok=True);raise
    return path


def activate(domain,address):
    result=plan(domain,address)
    if os.geteuid()!=0:raise ValueError('实际接入需在服务器控制台以 root 执行')
    fd=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    with os.fdopen(fd,'r+') as lock:
        details=os.fstat(lock.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid!=0:raise ValueError('安装锁必须为 root 所有的普通文件')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('另一个部署进程正在运行') from None
        user=preflight(domain,address)
        before=CONFIG.read_bytes();details=CONFIG.stat();site=fragment(domain)
        adapted=json.loads(caddy_command(user,'adapt','--config',str(CONFIG),'--adapter','caddyfile'))
        import_line='import '+str(FRAGMENT)
        managed=(not FRAGMENT.is_symlink() and FRAGMENT.is_file() and FRAGMENT.read_bytes()==site
                 and import_line in before.decode().splitlines() and has_host(adapted,domain))
        if managed:
            caddy_command(user,'validate','--config',str(CONFIG),'--adapter','caddyfile')
            run('systemctl','reload','caddy.service')
            return {**result,'mode':'already_configured','proxy':'reloaded','https':https(domain,6),
                    'notice':'既有独立片段已核对并重载，没有重复添加。https=pending 时稍后用 --verify 复查。'}
        if os.path.lexists(FRAGMENT) or has_host(adapted,domain):raise ValueError('已有 App 片段或同名站点，不覆盖；请先核对已有接入')
        backup=CONFIG.parent/('Caddyfile.before-baba-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4))
        new_file(backup,before)
        candidate=None;created=False;committed=False
        after=before+b'\n\n'+import_line.encode()+b'\n'
        try:
            new_file(FRAGMENT,site,0o644);created=True
            candidate=stage(after,details)
            caddy_command(user,'validate','--config',str(candidate),'--adapter','caddyfile')
            if CONFIG.read_bytes()!=before:raise ValueError('原配置在校验期间被其他进程修改，停止接入并保留其修改')
            os.replace(candidate,CONFIG);committed=True
            run('systemctl','reload','caddy.service')
        except BaseException as original:
            try:
                if committed:
                    if CONFIG.read_bytes()!=after:raise ValueError('原配置被其他进程再次修改，未覆盖该修改；请在服务器核对备份 '+str(backup))
                    recovery=stage(before,details)
                    try:os.replace(recovery,CONFIG)
                    finally:recovery.unlink(missing_ok=True)
                if created and FRAGMENT.read_bytes()==site:FRAGMENT.unlink()
                if committed:run('systemctl','reload','caddy.service')
            except Exception:raise ValueError('接入失败且自动恢复未完全确认；备份在 '+str(backup)+'，请在服务器核对当前配置与 Caddy 状态，勿重启或盲目重跑') from None
            raise original
        finally:
            if candidate is not None:candidate.unlink(missing_ok=True)
        return {**result,'mode':'configured','proxy':'reloaded','backup':str(backup),'https':https(domain,6),
                'notice':'原站点内容保留，独立 App 站点已重载。https=passed 才表示 HTTPS 健康检查通过；pending 可稍后用 --verify 复查。自动备份尚未配置。'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain',required=True);p.add_argument('--address',required=True)
    mode=p.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--verify',action='store_true')
    args=p.parse_args()
    try:
        action=activate if args.apply else verify if args.verify else plan
        print(json.dumps(action(args.domain,args.address),ensure_ascii=False,indent=2))
    except KeyboardInterrupt:p.exit(1,'操作已中断；请核对上一步状态及保留的配置备份。\n')
    except Exception as exc:p.exit(1,'HTTPS 接入未完成：'+(str(exc) if isinstance(exc,ValueError) else '请在服务器核对依赖、权限和配置，不发送私钥或完整敏感日志')+'\n')


if __name__=='__main__':main()
