#!/usr/bin/env python3
"""Prepare reviewable release assets from a clean Git commit; never publish automatically."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def run(root,*args):return subprocess.run(args,cwd=root,check=True,text=True,capture_output=True).stdout.strip()


def build(root,output,apk=None):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('输出目录已存在，请选择新目录')
    if run(root,'git','status','--porcelain'):raise ValueError('发布资产必须来自干净的 Git 工作区，请先提交改动')
    revision=run(root,'git','rev-parse','HEAD');manifest=json.loads(run(root,'git','show',revision+':clients/app/manifest.json'))
    version=manifest['version']['name'];code=manifest['version']['code']
    if not re.fullmatch(r'\d+\.\d+\.\d+',version) or type(code) is not int or code<=0:raise ValueError('请核对版本号与版本代码')
    apk_bytes=None
    if apk:
        # A ZIP-like file is not proof of an Android signature or matching application identity.
        if not shutil.which('apksigner') or not shutil.which('aapt'):raise ValueError('校验 APK 需要 Android SDK 的 apksigner 与 aapt')
        package=manifest.get('plus',{}).get('distribute',{}).get('android',{}).get('packagename')
        if not package:raise ValueError('请先配置正式 Android 包名')
        run(root,'apksigner','verify','--verbose',str(Path(apk).resolve()))
        badging=run(root,'aapt','dump','badging',str(Path(apk).resolve()))
        if not all(s in badging.splitlines()[0] for s in ("name='"+package+"'","versionCode='"+str(code)+"'","versionName='"+version+"'")):raise ValueError('APK 包名或版本与源码清单不一致')
        apk_bytes=Path(apk).read_bytes()
    tree=run(root,'git','ls-tree','-r','-z',revision).split('\0')
    entries=[entry.split('\t',1) for entry in tree if entry]
    files=[name for _,name in entries]
    modes={name:metadata.split()[0] for metadata,name in entries}
    allowed=[]
    for name in files:
        path=Path(name)
        if name in ('README.md','THIRD_PARTY_NOTICES.md','.env.example') or (len(path.parts)==1 and path.suffix in ('.html','.css','.js')) or (path.parts and path.parts[0] in ('backend','clients','docs','deploy','tools','tests','demo')):
            if modes[name] not in ('100644','100755'):raise ValueError('发布源码不能包含符号链接')
            if path.suffix in ('.pem','.key','.db','.sqlite','.sqlite3','.p12','.pfx','.keystore','.jks') or (path.name.startswith('.env') and path.name!='.env.example') or any(part in ('private','backups','node_modules') for part in path.parts) or 'credential' in path.name.lower() or 'service-account' in path.name.lower():raise ValueError('拒绝打包私有运行文件')
            allowed.append(name)
    if not allowed:raise ValueError('没有可打包的源码')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.release-',dir=output.parent) as directory:
        stage=Path(directory)
        with zipfile.ZipFile(stage/'baba-source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(allowed):
                # Commit bytes, not a second filesystem read; a concurrent edit cannot enter the archive.
                data=subprocess.run(['git','show',revision+':'+name],cwd=root,check=True,capture_output=True).stdout
                info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                archive.writestr(info,data)
        if apk_bytes is not None:(stage/'baba-android.apk').write_bytes(apk_bytes)
        metadata={'version':version,'version_code':code,'commit':revision,'android_package':manifest.get('plus',{}).get('distribute',{}).get('android',{}).get('packagename') or None,
                  'contains_verified_apk':apk_bytes is not None,'minimum_android':None,'notice':'源码包不等于已签名安装包；最低系统版本及真机兼容性以实际 APK 验收记录为准。'}
        (stage/'release.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        checksums=[]
        for path in sorted(stage.iterdir()):checksums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
        (stage/'SHA256SUMS').write_text('\n'.join(checksums)+'\n')
        stage.rename(output)
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--apk')
    args=parser.parse_args()
    try:print(json.dumps(build(Path(__file__).resolve().parents[1],args.output,args.apk),ensure_ascii=False))
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:parser.exit(1,'发布打包未完成：'+(str(exc) if isinstance(exc,ValueError) else '请核对工作区和工具配置')+'\n')
