const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const Recovery=require('../clients/weapp/lib/recovery');
const root=path.resolve(__dirname,'..');
const input=(id='id')=>({family_id:'family',action:'task.create',data:{title:'合成任务'},request_id:id});
const copy=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};

function wechat(respond){
 let app,page;
 const stored=new Map([['baba-session','token'],['baba-csrf','csrf'],['baba-family','family']]);
 const calls=[],toasts=[];
 const wx={
  getStorageSync:key=>stored.get(key),setStorageSync:(key,value)=>stored.set(key,value),removeStorageSync:key=>stored.delete(key),
  showToast:value=>toasts.push(value),
  request(options){calls.push({path:options.url,data:copy(options.data===undefined?null:options.data)});Promise.resolve().then(()=>respond(options)).then(result=>options.success(result),error=>options.fail(error))}
 };
 const context=vm.createContext({wx,require:()=>({create:options=>Recovery.create({...options,delay:async()=>{}})}),
  App:value=>{app=value},getApp:()=>app,getCurrentPages:()=>page?[page]:[],
  Page:value=>{page=value;page.data=copy(value.data);page.setData=function(update){for(const [key,value]of Object.entries(update)){const bits=key.split('.');let target=this.data;for(const bit of bits.slice(0,-1)){target[bit]??={};target=target[bit]}target[bits.at(-1)]=value}}}
 });
 vm.runInContext(fs.readFileSync(path.join(root,'clients/weapp/app.js'),'utf8'),context);
 app.onLaunch();
 vm.runInContext(fs.readFileSync(path.join(root,'clients/weapp/pages/main/main.js'),'utf8'),context);
 page.data.familyId='family';page.data.auth=true;page.data.user={id:'mom'};
 return {app,page,stored,calls,toasts};
}
async function settled(page){for(let i=0;i<50;i++){await new Promise(setImmediate);if(!page.data.busy)return}assert.fail('page did not settle')}
const snapshot=()=>({orders:[],refunds:[],tasks:[],items:[],approvals:[],members:[{id:'mom',name:'合成妈妈'}],
 user:{id:'mom'},family:{id:'family',coordinator:'dad',mode:'off'},consents:[],babies:[],knowledge:[],
 pregnancy:{id:'pregnancy',restricted:true},role:'mom'});

test('WeChat Page.action retains original id after save succeeds but refresh fails',async()=>{
 let online=false,effects=0;const ledger=new Set();
 const fixture=wechat(async options=>{
  if(options.url.endsWith('/action')){if(!ledger.has(options.data.request_id)){ledger.add(options.data.request_id);effects++}return {statusCode:200,data:{id:'task'}}}
  if(!online)throw Error('offline refresh');return {statusCode:200,data:snapshot()};
 });
 const {page,app,calls}=fixture;
 await assert.rejects(page.action('task.create',{title:'合成任务'}),e=>e.committed&&e.recoverable);
 assert.equal(app.hasPending(),true);assert.equal(page.data.state,null);assert.equal(page.data.checking,true);online=true;
 await page.action('task.create',{title:'合成任务'});
 const commands=calls.filter(c=>c.path.endsWith('/action'));
 assert.equal(commands.length,2);assert.equal(commands[0].data.request_id,commands[1].data.request_id);assert.equal(effects,1);
 assert.equal(page.data.state.pregnancy.restricted,true);assert.equal(app.hasPending(),false);
});

test('WeChat 401 clears credentials, health views, drafts and pending commands',async()=>{
 const fixture=wechat(async options=>{if(options.url.endsWith('/action'))throw Error('offline');return {statusCode:401,data:{error:'expired'}}});
 const {app,page,stored}=fixture;
 Object.assign(page.data,{state:{pregnancy:{edd:'private-date'}},modal:'baby',timelineDraft:{date:'private-date'},babyDraft:{name:'private-child'},aiText:'private-question',inviteCode:'private-code'});
 await assert.rejects(app.command(input(),async()=>{}));
 await assert.rejects(app.request('/me'),e=>e.status===401);
 assert.equal(app.globalData.token,'');assert.equal(app.globalData.csrf,'');assert.equal(stored.has('baba-session'),false);
 assert.equal(stored.has('baba-family'),false);assert.equal(page.data.auth,false);assert.equal(page.data.state,null);
 assert.equal(page.data.modal,'');assert.equal(page.data.timelineDraft,null);assert.equal(page.data.babyDraft,null);
 assert.equal(page.data.aiText,'');assert.equal(page.data.inviteCode,'');assert.equal(app.hasPending(),false);
});

test('late WeChat old-account failure cannot erase a replacement session',async()=>{
 const gate=deferred();const {app}=wechat(async()=>gate.promise);
 const read=app.request('/state?family_id=family');await new Promise(setImmediate);
 app.clearSession();app.globalData.token='new-token';app.globalData.csrf='new-csrf';
 gate.resolve({statusCode:401,data:{error:'old account expired'}});
 await assert.rejects(read,e=>e.stale);assert.equal(app.globalData.token,'new-token');
});

test('returning to WeChat foreground closes old drafts and fetches revoked permissions',async()=>{
 const {page}=wechat(async()=>({statusCode:200,data:snapshot()}));
 page._shown=true;page.data.state={pregnancy:{edd:'private-date'}};page.data.modal='timeline-preview';page.data.timelineDraft={date:'private-date'};
 page.onShow();await settled(page);
 assert.equal(page.data.modal,'');assert.equal(page.data.timelineDraft,null);assert.equal(page.data.checking,false);
 assert.equal(page.data.state.pregnancy.restricted,true);assert.equal(JSON.stringify(page.data.state).includes('private-date'),false);
});

test('offline foreground refresh hides old WeChat health state behind reload view',async()=>{
 const {page}=wechat(async()=>{throw Error('offline')});page._shown=true;
 page.data.state={pregnancy:{edd:'private-date'}};page.data.modal='baby-preview';page.data.babyDraft={name:'private-child'};
 page.onShow();await settled(page);
 assert.equal(page.data.state,null);assert.equal(page.data.checking,true);assert.equal(page.data.auth,true);assert.equal(page.data.babyDraft,null);
});

test('switching WeChat service origin discards a late success',async()=>{
 const gate=deferred();const {app}=wechat(async()=>gate.promise);
 const result=app.request('/me');await new Promise(setImmediate);app.globalData.baseUrl='https://other-service.example';
 gate.resolve({statusCode:200,data:{session_token:'old-server-token',csrf:'old-server-csrf'}});
 await assert.rejects(result,e=>e.stale);assert.equal(app.globalData.token,'token');
});

test('older same-account WeChat snapshot cannot overwrite newer revoked permissions',async()=>{
 const gate=deferred();let reads=0;
 const {page}=wechat(async()=>++reads===1?gate.promise:{statusCode:200,data:snapshot()});
 const old=page.refresh();await new Promise(setImmediate);await page.refresh();
 const privateState=snapshot();privateState.pregnancy={id:'pregnancy',edd:'private-date',timeline:{title:'older-title'}};
 gate.resolve({statusCode:200,data:privateState});await assert.rejects(old,e=>e.stale);
 assert.equal(page.data.state.pregnancy.restricted,true);assert.equal(JSON.stringify(page.data.state).includes('private-date'),false);
});

function browser(respond){
 const events=[];const context=vm.createContext({RequestRecovery:{create:options=>Recovery.create({...options,delay:async()=>{}})},
  location:{origin:'https://synthetic.example'},document:{dispatchEvent:event=>events.push(event.type)},
  CustomEvent:class{constructor(type){this.type=type}},AbortController,setTimeout,clearTimeout,
  fetch:async(url,options)=>respond(url,options)
 });
 vm.runInContext(fs.readFileSync(path.join(root,'api-client.js'),'utf8'),context);
 return {context,events,api:context.api,command:context.apiCommand};
}
const response=(status,body)=>({status,json:async()=>body});

test('browser adapter retries a lost command with identical serialized headers and body',async()=>{
 const seen=[];const {command}=browser(async(url,options)=>{seen.push(options);if(seen.length===1)throw Error('connection reset');return response(200,{id:'task'})});
 await command(input(),async()=>{});assert.equal(seen.length,2);assert.equal(seen[0].body,seen[1].body);
 assert.equal(seen[0].headers['X-CSRF-Token'],seen[1].headers['X-CSRF-Token']);assert.equal(seen[0].cache,'no-store');
});

test('browser late 401 after a new login does not dispatch sign-out',async()=>{
 const gate=deferred();const {api,events,context}=browser(async url=>url.endsWith('/login')?response(200,{csrf:'new-session'}):gate.promise);
 const old=api('/state');await api('/login',{login:'new'});gate.resolve(response(401,{error:'expired old session'}));
 await assert.rejects(old,e=>e.stale);assert.equal(events.includes('signed-out'),false);assert.equal(vm.runInContext('csrf',context),'new-session');
});
