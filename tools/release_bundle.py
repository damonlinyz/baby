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


def checksum(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def inspect_apk(root,apk,manifest,expected_signer_sha256):
    if not shutil.which('apksigner') or not shutil.which('aapt'):raise ValueError('校验 APK 需要 Android SDK 的 apksigner 与 aapt')
    pin=str(expected_signer_sha256 or '').replace(':','').lower()
    if not re.fullmatch('[a-f0-9]{64}',pin):raise ValueError('请明确提供预期发布证书 SHA-256，不能自动信任任意签名')
    package=manifest.get('plus',{}).get('distribute',{}).get('android',{}).get('packagename')
    if not package or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',package):raise ValueError('请先配置正式 Android 包名')
    source=Path(apk).resolve(strict=True)
    if not source.is_file() or not 0<source.stat().st_size<2**31:raise ValueError('APK 必须为小于 2 GiB 的真实文件')
    with tempfile.TemporaryDirectory(prefix='baba-apk-check-') as directory:
        copy=Path(directory)/'verified.apk';shutil.copyfile(source,copy)
        if not 0<copy.stat().st_size<2**31:raise ValueError('APK 大小发生变化')
        certs=run(root,'apksigner','verify','--verbose','--print-certs',str(copy))
        signers=[s.replace(':','').lower() for s in re.findall(r'Signer #\d+ certificate SHA-256 digest:\s*([a-fA-F0-9:]+)',certs)]
        if signers!=[pin]:raise ValueError('APK 发布者签名与预期指纹不一致')
        badging=run(root,'aapt','dump','badging',str(copy));lines=badging.splitlines();version=manifest['version']
        if not lines or not all(s in lines[0] for s in ("name='"+package+"'","versionCode='"+str(version['code'])+"'","versionName='"+version['name']+"'")):raise ValueError('APK 包名或版本与源码清单不一致')
        sdk=re.search(r"^sdkVersion:'(\d+)'$",badging,re.M)
        if not sdk or not 16<=int(sdk[1])<=100:raise ValueError('APK 最低 Android 系统信息无效')
        return {'name':'baba-android.apk','package':package,'min_sdk':int(sdk[1]),'signer_sha256':pin,'size_bytes':copy.stat().st_size,'sha256':checksum(copy)}


def build(root,output,apk=None,expected_signer_sha256=None):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('输出目录已存在，请选择新目录')
    if run(root,'git','status','--porcelain'):raise ValueError('发布资产必须来自干净的 Git 工作区，请先提交改动')
    revision=run(root,'git','rev-parse','HEAD');manifest=json.loads(run(root,'git','show',revision+':clients/app/manifest.json'))
    version=manifest['version']['name'];code=manifest['version']['code']
    if not re.fullmatch(r'\d+\.\d+\.\d+',version) or type(code) is not int or code<=0:raise ValueError('请核对版本号与版本代码')
    apk_info=inspect_apk(root,apk,manifest,expected_signer_sha256) if apk else None
    tree=run(root,'git','ls-tree','-r','-z',revision).split('\0')
    entries=[entry.split('\t',1) for entry in tree if entry]
    files=[name for _,name in entries]
    modes={name:metadata.split()[0] for metadata,name in entries}
    allowed=[]
    for name in files:
        path=Path(name)
        if name in ('README.md','THIRD_PARTY_NOTICES.md','.env.example') or (len(path.parts)==1 and path.suffix in ('.html','.css','.js')) or (path.parts and path.parts[0] in ('backend','clients','docs','deploy','tools','tests','demo','website')):
            if modes[name] not in ('100644','100755'):raise ValueError('发布源码不能包含符号链接')
            if path.suffix in ('.pem','.key','.db','.sqlite','.sqlite3','.p12','.pfx','.keystore','.jks','.apk','.aab','.ipa','.dmg','.exe') or (path.name.startswith('.env') and path.name!='.env.example') or any(part in ('private','backups','node_modules') for part in path.parts) or 'credential' in path.name.lower() or 'service-account' in path.name.lower():raise ValueError('拒绝打包私有运行文件或安装包')
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
        if apk_info:
            shutil.copyfile(apk,stage/'baba-android.apk')
            if checksum(stage/'baba-android.apk')!=apk_info['sha256']:raise ValueError('签名核验后 APK 内容发生变化')
        metadata={'schema_version':2,'version':version,'version_code':code,'commit':revision,'android_package':manifest.get('plus',{}).get('distribute',{}).get('android',{}).get('packagename') or None,
                  'contains_verified_apk':bool(apk_info),'android':apk_info,'minimum_android':apk_info['min_sdk'] if apk_info else None,'notice':'源码包不等于已签名安装包；实际真机兼容性仍需单独验收。'}
        (stage/'release.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        checksums=[]
        for path in sorted(stage.iterdir()):checksums.append(checksum(path)+'  '+path.name)
        (stage/'SHA256SUMS').write_text('\n'.join(checksums)+'\n')
        stage.rename(output)
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--apk');parser.add_argument('--expected-signer-sha256',help='预期的公开发布证书 SHA-256，不是签名私钥')
    args=parser.parse_args()
    try:print(json.dumps(build(Path(__file__).resolve().parents[1],args.output,args.apk,args.expected_signer_sha256),ensure_ascii=False))
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:parser.exit(1,'发布打包未完成：'+(str(exc) if isinstance(exc,ValueError) else '请核对工作区和工具配置')+'\n')
