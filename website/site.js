(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else{root.BabaDownloads=api;api.start()}})(typeof globalThis==='object'?globalThis:this,function(){
 const digest=/^[a-f0-9]{64}$/,repoPattern=/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/;
 function normalize(data,config){
  if(!config||!repoPattern.test(config.repository)||config.android_asset!=='baba-android.apk'||!Number.isInteger(config.minimum_android_version_code)||config.minimum_android_version_code<1)throw Error('invalid configuration');
  if(!data||data.schema_version!==1||data.repository!==config.repository)throw Error('invalid release');
  if(data.status==='pending')return {status:'pending'};
  const a=data.android,tag=data.tag,base='https://github.com/'+config.repository+'/releases/download/'+tag+'/';
  if(data.status!=='available'||!/^v\d+\.\d+\.\d+$/.test(tag)||data.version!==tag.slice(1)||!Number.isInteger(data.version_code)||data.version_code<config.minimum_android_version_code||!Number.isInteger(config.minimum_android_version_code))throw Error('invalid version');
  if(!a||!config.android_package||a.package!==config.android_package||a.name!==config.android_asset||!Number.isInteger(a.size_bytes)||a.size_bytes<=0||a.size_bytes>=2**31||!digest.test(a.sha256)||!digest.test(a.signer_sha256)||a.signer_sha256!==config.android_signer_sha256||!Number.isInteger(a.min_sdk)||a.min_sdk<16||a.min_sdk>100||!Number.isFinite(Date.parse(data.published_at)))throw Error('invalid Android release');
  if(a.url!==base+'baba-android.apk'||data.release_url!=='https://github.com/'+config.repository+'/releases/tag/'+tag||data.checksums_url!==base+'SHA256SUMS'||!/^([a-f0-9]{40}|[a-f0-9]{64})$/.test(data.commit))throw Error('invalid download origin');
  if(typeof data.notes!=='string'||data.notes.length>10000)throw Error('invalid notes');
  return data;
 }
 const sdkNames={16:'4.1',17:'4.2',18:'4.3',19:'4.4',20:'4.4W',21:'5.0',22:'5.1',23:'6.0',24:'7.0',25:'7.1',26:'8.0',27:'8.1',28:'9',29:'10',30:'11',31:'12',32:'12L',33:'13',34:'14',35:'15',36:'16'};
 function start(){
  const $=selector=>document.querySelector(selector);let ready=null;
  const unavailable=message=>{ready=null;const link=$('#android-download');link.removeAttribute('href');link.setAttribute('aria-disabled','true');link.tabIndex=-1;link.textContent='Android 安装包尚未发布';$('#release-badge').textContent='准备中';$('#release-state').textContent=message;$('#release-details').hidden=true;$('#release-notes').hidden=true};
  async function load(){
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
   try{
    const responses=await Promise.all(['config.json','release.json'].map(path=>fetch(path,{cache:'no-store',signal:controller.signal})));if(responses.some(r=>!r.ok))throw Error('unavailable');
    const [config,data]=await Promise.all(responses.map(r=>r.json())),r=normalize(data,config);
    if(r.status==='pending'){unavailable('正式安装包还在准备中。发布后，这里会提供可下载的文件和版本信息。');return}
    ready=r;$('#release-badge').textContent='已发布';$('#release-state').textContent='当前可下载版本已发布，建议核对文件摘要后安装。';
    const button=$('#android-download');button.href=r.android.url;button.setAttribute('aria-disabled','false');button.removeAttribute('tabindex');button.textContent='下载 Android 版 APK ↧';
    $('#release-version').textContent='v'+r.version+' · '+r.version_code;$('#release-size').textContent=(r.android.size_bytes/1048576).toFixed(1)+' MB';$('#release-system').textContent=sdkNames[r.android.min_sdk]?'Android '+sdkNames[r.android.min_sdk]+' 及以上':'Android API '+r.android.min_sdk+' 及以上';$('#release-date').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(r.published_at));
    $('#release-sha').textContent=r.android.sha256;$('#release-signer').textContent=r.android.signer_sha256;$('#release-link').href=r.release_url;$('#checksum-link').href=r.checksums_url;$('#release-details').hidden=false;
    if(r.notes.trim()){$('#release-notes-text').textContent=r.notes;$('#release-notes').hidden=false}
   }catch{unavailable('暂时无法确认安装包信息。请稍后重试，或前往 GitHub 的发布记录查看。');$('#release-badge').textContent='暂未确认';$('#android-download').textContent='暂时无法获取下载';}
   finally{clearTimeout(timer)}
  }
  $('#android-download').addEventListener('click',event=>{if(!ready)event.preventDefault()});
  $('#copy-sha').addEventListener('click',async()=>{if(!ready)return;const box=$('#notice');try{await navigator.clipboard.writeText(ready.android.sha256);box.textContent='已复制文件 SHA-256'}catch{box.textContent='可直接选中上方的文件摘要进行复制'}box.hidden=false;setTimeout(()=>{box.hidden=true},3500)});
  load();
 }
 return {normalize,start};
});
