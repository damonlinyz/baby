const Recovery=require('./lib/recovery');
App({
 globalData:{baseUrl:'http://127.0.0.1:8766',csrf:'',token:''},
 onLaunch(){
  this.globalData.token=wx.getStorageSync('baba-session')||'';
  this.globalData.csrf=wx.getStorageSync('baba-csrf')||'';
  this.calendarCleanup=this.cleanCalendarFiles();
  this.client();
 },
 cleanCalendarFiles(){
  if(!wx.getFileSystemManager||!wx.env?.USER_DATA_PATH)return Promise.resolve();
  const fs=wx.getFileSystemManager(),directory=wx.env.USER_DATA_PATH;
  return new Promise(resolve=>fs.readdir({dirPath:directory,success:r=>Promise.all((r.files||[]).filter(name=>/^baba-calendar-\d+-[a-z0-9]+\.ics$/.test(name)).map(name=>new Promise(done=>fs.unlink({filePath:directory+'/'+name,success:done,fail:done})))).then(resolve),fail:resolve}));
 },
 client(){
  if(this._recovery)return this._recovery;
  const g=this.globalData;
  this._recovery=Recovery.create({
   scope:()=>g.baseUrl+'|'+g.token,
   onUnauthorized:()=>this.clearSession(),
   onChange:()=>{for(const page of getCurrentPages())page.setData({requestPending:this._recovery?.pending().pending||false})},
   send:(path,data)=>new Promise((resolve,reject)=>wx.request({
    url:g.baseUrl+'/api'+path,method:data===undefined?'GET':'POST',data,
    timeout:path==='/ai'?90000:15000,
    header:{'Content-Type':'application/json','Authorization':'Bearer '+g.token,'X-CSRF-Token':g.csrf},
    success:r=>resolve({status:r.statusCode,body:r.data}),
    fail:()=>reject(Error('无法连接服务，请检查网络'))
   }))
  });
  return this._recovery;
 },
 clearSession(){
  this.globalData.token='';this.globalData.csrf='';
  this._recovery?.reset();
  wx.removeStorageSync('baba-session');wx.removeStorageSync('baba-csrf');wx.removeStorageSync('baba-family');
  for(const page of getCurrentPages())page.onSessionExpired?.();
 },
 async request(path,data){
  const result=await this.client().request(path,data),g=this.globalData;
  if(result.session_token&&g.token!==result.session_token){g.token=result.session_token;wx.setStorageSync('baba-session',g.token);this.client().reset()}
  if(result.csrf){g.csrf=result.csrf;wx.setStorageSync('baba-csrf',g.csrf)}
  if(path==='/logout'||path==='/account/delete')this.clearSession();
  return result;
 },
 command(data,confirm){return this.client().command(data,confirm)},
 retryCommand(confirm){return this.client().retry(confirm)},
 hasPending(){return this.client().pending().pending}
});
