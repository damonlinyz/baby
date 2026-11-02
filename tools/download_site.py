#!/usr/bin/env python3
"""Build a public-only Pages site from a verified stable GitHub release."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

FILES=('index.html','site.css','site.js','icon.svg','config.json')
MAX_ASSET=2**31


def canonical(value):return json.dumps(value,ensure_ascii=False,indent=2)+'\n'


def configuration(root):
    c=json.loads((root/'website/config.json').read_text())
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',c.get('repository','')) or c.get('android_asset')!='baba-android.apk':raise ValueError('请核对公开下载仓库与固定 APK 文件名')
    if type(c.get('minimum_android_version_code')) is not int or c['minimum_android_version_code']<1:raise ValueError('最低允许的版本代码无效')
    package=c.get('android_package')
    if package is not None and (not isinstance(package,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',package)):raise ValueError('公开安装包身份配置无效')
    if c.get('android_signer_sha256') is not None and not re.fullmatch('[a-f0-9]{64}',c['android_signer_sha256']):raise ValueError('发布者证书须为小写 SHA-256 指纹')
    return c


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        u=urllib.parse.urlsplit(newurl)
        if u.scheme!='https' or u.hostname not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com') or u.username or u.password:raise ValueError('拒绝非 GitHub HTTPS 下载重定向')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def api(path):
    headers={'Accept':'application/vnd.github+json','User-Agent':'BabaDownloadSite/1','X-GitHub-Api-Version':'2022-11-28'}
    if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
    # API requests must not follow redirects carrying an Authorization header.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('GitHub API 不允许重定向')
    req=urllib.request.Request('https://api.github.com'+path,headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
            raw=response.read(2000001)
            if len(raw)>2000000:raise ValueError('发布信息过大')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code==404:return None
        raise ValueError('GitHub 发布 API 暂不可用，请检查权限或限流') from None
    except (OSError,ValueError):raise ValueError('GitHub 发布 API 暂不可用，不更新下载页') from None


def public_request(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('只读取 GitHub 的公开 HTTPS 发布附件')
    return urllib.request.Request(url,headers={'User-Agent':'BabaDownloadSite/1'})


def public_bytes(url,limit=100000):
    # Public release assets use no API credential, including on CDN redirects.
    req=public_request(url)
    try:
        with urllib.request.build_opener(SecureRedirect()).open(req,timeout=30) as response:data=response.read(limit+1)
    except Exception:raise ValueError('无法读取公开发布附件，不更新下载页') from None
    if len(data)>limit:raise ValueError('发布元数据附件过大')
    return data


def download_apk(url,path,size,digest):
    path=Path(path)
    if path.exists() or path.is_symlink():raise ValueError('核验文件已存在')
    if type(size) is not int or not 0<size<MAX_ASSET or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('APK 核验参数无效')
    h=hashlib.sha256();total=0;created=False
    try:
        with urllib.request.build_opener(SecureRedirect()).open(public_request(url),timeout=60) as response:
            with path.open('xb') as output:
                created=True
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    total+=len(chunk)
                    if total>size:raise ValueError('APK 大小不一致')
                    output.write(chunk);h.update(chunk)
        if total!=size or h.hexdigest()!=digest:raise ValueError('APK 大小或摘要不一致')
    except Exception:
        if created:path.unlink(missing_ok=True)
        raise ValueError('公开 APK 文件无法通过大小与摘要核验') from None


def verify_remote_apk(config,release,manifest):
    import release_bundle
    repo=config['repository'];commit=manifest['commit'];apk=manifest['android']
    source=api(f'/repos/{repo}/contents/clients/app/manifest.json?ref={commit}')
    try:
        if not source or source.get('encoding')!='base64':raise ValueError()
        app=json.loads(base64.b64decode(source['content'],validate=False))
    except Exception:raise ValueError('无法核对安装包来源提交的 App 配置') from None
    if app.get('version')!={'name':manifest['version'],'code':manifest['version_code']}:raise ValueError('App 源码版本与发布清单不一致')
    with tempfile.TemporaryDirectory(prefix='baba-download-verify-') as directory:
        file=Path(directory)/'baba-android.apk';url=f'https://github.com/{repo}/releases/download/{release["tag_name"]}/baba-android.apk'
        download_apk(url,file,apk['size_bytes'],apk['sha256'])
        measured=release_bundle.inspect_apk(Path(__file__).resolve().parents[1],file,app,config['android_signer_sha256'])
        if measured!=apk:raise ValueError('真实 APK 的签名、包名、版本或系统要求与清单不一致')


def release_feed(config,release,manifest,checksums,tag_commit):
    repo=config['repository']
    if release.get('draft') is not False or release.get('prerelease') is not False:raise ValueError('草稿和预发布不能进入正式下载页')
    tag=release.get('tag_name','')
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag):raise ValueError('正式版本标签须为 vX.Y.Z')
    if manifest.get('schema_version')!=2 or manifest.get('contains_verified_apk') is not True:raise ValueError('发布中没有经过签名核验的真实 APK')
    if manifest.get('version')!=tag[1:] or type(manifest.get('version_code')) is not int or manifest['version_code']<config['minimum_android_version_code']:raise ValueError('发布版本不匹配或低于允许的版本代码')
    commit=manifest.get('commit','')
    if not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}',commit) or commit!=tag_commit:raise ValueError('标签提交与安装包构建来源不一致')
    apk=manifest.get('android') or {};pin=config.get('android_signer_sha256')
    if not pin or apk.get('signer_sha256')!=pin or not re.fullmatch('[a-f0-9]{64}',apk.get('sha256','')):raise ValueError('安装包签名未固定或与配置不符')
    if not config.get('android_package') or apk.get('package')!=config['android_package'] or manifest.get('android_package')!=config['android_package']:raise ValueError('正式 Android 包名未固定或与配置不符')
    if type(apk.get('min_sdk')) is not int or not 16<=apk['min_sdk']<=100:raise ValueError('最低 Android 系统信息缺失')
    assets=release.get('assets',[]);by_name={}
    for a in assets:
        if a.get('name') in by_name:raise ValueError('发布包含重名附件')
        by_name[a.get('name')]=a
    base=f'https://github.com/{repo}/releases/download/{tag}/'
    for name in ('baba-android.apk','release.json','SHA256SUMS'):
        a=by_name.get(name,{})
        if a.get('state')!='uploaded' or type(a.get('size')) is not int or not 0<a['size']<MAX_ASSET or a.get('browser_download_url')!=base+name:raise ValueError('正式发布附件缺失、未上传或不属于本仓库')
    a=by_name['baba-android.apk']
    if apk.get('name')!='baba-android.apk' or apk.get('size_bytes')!=a['size'] or a.get('digest')!='sha256:'+apk['sha256']:raise ValueError('GitHub 附件摘要/大小与构建清单不一致')
    expected={}
    for line in checksums.splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9_.-]+)',line)
        if not match or match[2] in expected:raise ValueError('SHA256SUMS 格式或文件名无效')
        expected[match[2]]=match[1]
    if expected.get('baba-android.apk')!=apk['sha256']:raise ValueError('APK 校验清单不一致')
    if release.get('html_url')!=f'https://github.com/{repo}/releases/tag/{tag}':raise ValueError('版本详情地址不属于本仓库')
    date=release.get('published_at');notes=release.get('body') or ''
    import datetime
    try:datetime.datetime.fromisoformat(date.replace('Z','+00:00'))
    except (AttributeError,ValueError):raise ValueError('发布日期无效')
    if not isinstance(notes,str) or len(notes)>10000:raise ValueError('更新说明过长')
    return {'schema_version':1,'repository':repo,'status':'available','tag':tag,'version':manifest['version'],'version_code':manifest['version_code'],
            'commit':commit,'published_at':date,'release_url':release['html_url'],'checksums_url':base+'SHA256SUMS','notes':notes,
            'android':{**{k:apk[k] for k in ('name','package','size_bytes','sha256','signer_sha256','min_sdk')},'url':base+'baba-android.apk'}}


def latest(config):
    repo=config['repository'];release=api(f'/repos/{repo}/releases/latest')
    if release is None:return {'schema_version':1,'repository':repo,'status':'pending'}
    tag=release.get('tag_name','')
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag):raise ValueError('正式版本标签无效')
    base=f'https://github.com/{repo}/releases/download/{tag}/'
    names={a.get('name'):a for a in release.get('assets',[])}
    if any(names.get(name,{}).get('browser_download_url')!=base+name for name in ('release.json','SHA256SUMS')):raise ValueError('正式发布缺少元数据和校验清单')
    raw=public_bytes(base+'release.json');checksums=public_bytes(base+'SHA256SUMS').decode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    if not re.search(r'^'+digest+r'  release\.json$',checksums,re.M) or names['release.json'].get('digest')!='sha256:'+digest:raise ValueError('发布元数据校验失败')
    tag_info=api(f'/repos/{repo}/commits/{urllib.parse.quote(tag,safe="")}')
    manifest=json.loads(raw);feed=release_feed(config,release,manifest,checksums,(tag_info or {}).get('sha'))
    verify_remote_apk(config,release,manifest)
    return feed


def build(root,output,live=False):
    root=Path(root).resolve();output=Path(output).resolve();config=configuration(root)
    if output.exists():raise ValueError('站点输出目录已存在，请选择新目录')
    feed=latest(config) if live else {'schema_version':1,'repository':config['repository'],'status':'pending'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pages-',dir=output.parent) as temp:
        stage=Path(temp)
        for name in FILES:
            source=root/'website'/name
            if source.is_symlink() or not source.is_file():raise ValueError('下载页素材缺失或为符号链接')
            if name=='index.html':(stage/name).write_text(source.read_text().replace('https://github.com/damonlinyz/baby','https://github.com/'+config['repository']))
            else:shutil.copyfile(source,stage/name)
        (stage/'release.json').write_text(canonical(feed));(stage/'.nojekyll').touch()
        stage.rename(output)
    return {'ok':True,'status':feed['status'],'output':str(output)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--github',action='store_true',help='从本仓库最新稳定 Release 生成已校验下载信息')
    args=parser.parse_args()
    try:print(canonical(build(Path(__file__).resolve().parents[1],args.output,args.github)))
    except Exception as exc:parser.exit(1,'下载页未更新：'+(str(exc) if isinstance(exc,ValueError) else '请检查发布信息、网络与权限')+'\n')
