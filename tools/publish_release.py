#!/usr/bin/env python3
"""Upload a verified APK bundle to GitHub Releases; defaults to a reviewable draft."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import download_site
import release_bundle


def publish(root,bundle,notes,publish_now=False,dry_run=False):
    root=Path(root).resolve();bundle=Path(bundle).resolve();notes=Path(notes).resolve(strict=True)
    if bundle.is_symlink() or not bundle.is_dir() or not notes.is_file():raise ValueError('发布目录或更新说明无效')
    if not notes.read_text(encoding='utf-8').strip() or len(notes.read_text(encoding='utf-8'))>10000:raise ValueError('更新说明需为 1—10000 字符的 UTF-8 文本')
    names={'baba-source.zip','baba-android.apk','release.json','SHA256SUMS'}
    if {p.name for p in bundle.iterdir()}!=names or any(p.is_symlink() or not p.is_file() for p in bundle.iterdir()):raise ValueError('必须提供完整的、仅含四个发布资产的 APK 打包目录')
    metadata=json.loads((bundle/'release.json').read_text());config=download_site.configuration(root)
    if metadata.get('schema_version')!=2 or metadata.get('contains_verified_apk') is not True:raise ValueError('不能把源码包当作 App 发布')
    expected={}
    for line in (bundle/'SHA256SUMS').read_text().splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9_.-]+)',line)
        if not match or match[2] in expected:raise ValueError('发布校验清单格式无效')
        expected[match[2]]=match[1]
    if set(expected)!=names-{'SHA256SUMS'} or any(release_bundle.checksum(bundle/name)!=digest for name,digest in expected.items()):raise ValueError('发布资产与 SHA256SUMS 不一致')
    commit=metadata.get('commit','')
    if not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}',commit):raise ValueError('构建提交无效')
    source=json.loads(release_bundle.run(root,'git','show',commit+':clients/app/manifest.json'))
    if source['version']['name']!=metadata.get('version') or source['version']['code']!=metadata.get('version_code') or metadata['version_code']<config['minimum_android_version_code']:raise ValueError('发布版本与来源提交不一致或已被禁止')
    info=release_bundle.inspect_apk(root,bundle/'baba-android.apk',source,config.get('android_signer_sha256'))
    if info.get('package')!=config.get('android_package') or metadata.get('android_package')!=config.get('android_package'):raise ValueError('安装包身份与公开下载页配置不一致')
    if info!=metadata.get('android'):raise ValueError('发布前 APK 实测信息与清单不一致')
    if not re.fullmatch(r'\d+\.\d+\.\d+',metadata['version']):raise ValueError('版本格式无效')
    tag='v'+metadata['version']
    command=['gh','release','create',tag,*[str(bundle/name) for name in sorted(names)],'--repo',config['repository'],'--target',commit,'--title','爸爸在呢 '+tag,'--notes-file',str(notes)]
    command+=['--latest'] if publish_now else ['--draft']
    if not dry_run:subprocess.run(command,cwd=root,check=True)
    return {'ok':True,'tag':tag,'draft':not publish_now,'dry_run':dry_run,'repository':config['repository']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',required=True);p.add_argument('--notes-file',required=True);p.add_argument('--publish',action='store_true',help='明确将核验通过的安装包设为正式发布；默认只创建草稿');p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    try:print(json.dumps(publish(Path(__file__).resolve().parents[1],args.bundle,args.notes_file,args.publish,args.dry_run),ensure_ascii=False))
    except Exception as exc:p.exit(1,'发布未完成：'+(str(exc) if isinstance(exc,ValueError) else '请检查安装包、Android SDK、GitHub 权限和网络')+'\n')
