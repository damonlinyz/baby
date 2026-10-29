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
  showToast:value=>toasts.push(value),showModal:options=>options.success({confirm:true}),
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
 return {app,page,stored,calls,toasts,wx};
}
async function settled(page){for(let i=0;i<50;i++){await new Promise(setImmediate);if(!page.data.busy)return}assert.fail('page did not settle')}
const snapshot=()=>({calendar_date:'2026-10-28',task_series:[],orders:[],refunds:[],tasks:[],items:[],approvals:[],members:[{id:'mom',name:'合成妈妈'}],
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

test('WeChat recurring task creation sends typed interval and explicit calendar dates',async()=>{
 const {page,calls}=wechat(async options=>({statusCode:200,data:options.url.endsWith('/action')?{series_id:'series'}:snapshot()}));
 page.data.state=snapshot();page.data.form={title:'厨房家务',repeat:2,repeat_every:'2',due_date:'2026-10-28',repeat_until:'2026-11-25',assignee:0};
 page.addTask();await settled(page);
 const command=calls.find(c=>c.path.endsWith('/action')).data;
 assert.equal(command.action,'task.create');assert.equal(command.data.repeat,'weekly');assert.equal(command.data.repeat_every,2);
 assert.equal(command.data.due_date,'2026-10-28');assert.equal(command.data.repeat_until,'2026-11-25');assert.equal(command.data.assignee,'mom');
});

test('WeChat recurring picker uses public family calendar date with private pregnancy',()=>{
 const {page}=wechat(async()=>({statusCode:200,data:snapshot()}));page.data.state=snapshot();
 page.choose({currentTarget:{dataset:{key:'repeat'}},detail:{value:'1'}});
 assert.equal(page.data.form.due_date,'2026-10-28');assert.equal(page.data.state.pregnancy.restricted,true);
});

test('WeChat series stop respects cancellation and submits current series version',async()=>{
 const {page,calls,wx,toasts}=wechat(async options=>({statusCode:200,data:options.url.endsWith('/action')?{id:'series'}:snapshot()}));
 page.data.state={...snapshot(),task_series:[{id:'series',version:4,status:'active'}]};
 wx.showModal=options=>options.success({confirm:false});page.stopSeries({currentTarget:{dataset:{id:'series'}}});await settled(page);
 assert.equal(calls.length,0);
 wx.showModal=options=>options.success({confirm:true});page.stopSeries({currentTarget:{dataset:{id:'series'}}});await settled(page);
 const command=calls.find(c=>c.path.endsWith('/action')).data;
 assert.equal(command.action,'task_series.stop');assert.deepEqual(command.data,{id:'series',version:4});
 const count=calls.length;page.data.state.tasks=[{id:'stopped',status:'cancelled',series_stopped:true}];
 page.completeTask({currentTarget:{dataset:{id:'stopped'}}});await settled(page);
 assert.equal(calls.length,count);assert.equal(toasts.at(-1).title,'此重复安排已停止');
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

test('WeChat packing form submits edited quantity, priority, category, owner and captured version',async()=>{
 const {page,calls}=wechat(async o=>({statusCode:200,data:o.url.endsWith('/action')?{id:'item'}:snapshot()}));
 page.data.state={...snapshot(),role:'dad',items:[{id:'item',version:8,name:'证件',note:'原备注',quantity:2,pack:'证件资料包',level:'必备',assignee:'mom'}]};
 page.editItem({currentTarget:{dataset:{id:'item'}}});assert.equal(page.data.form.quantity,2);assert.equal(page.data.form.level,0);
 page.field({currentTarget:{dataset:{key:'quantity'}},detail:{value:'4'}});page.choose({currentTarget:{dataset:{key:'level'}},detail:{value:'2'}});
 page.addItem();await settled(page);const command=calls.find(c=>c.path.endsWith('/action')).data;
 assert.equal(command.action,'item.update');assert.deepEqual(command.data,{id:'item',version:8,name:'证件',note:'原备注',quantity:4,pack:'证件资料包',level:'可选',assignee:'mom'});
 assert.equal(page.data.modal,'');
});

test('WeChat packing delete cancellation sends nothing and accepted delete uses draft version',async()=>{
 const {page,calls,wx}=wechat(async o=>({statusCode:200,data:o.url.endsWith('/action')?{id:'item'}:snapshot()}));
 page.data.form={id:'item',version:7};wx.showModal=o=>o.success({confirm:false});page.deleteItem();await settled(page);assert.equal(calls.length,0);
 wx.showModal=o=>o.success({confirm:true});page.deleteItem();await settled(page);
 assert.deepEqual(calls[0].data.data,{id:'item',version:7});assert.equal(calls[0].data.action,'item.delete');
});

test('WeChat restore requires choosing a departed owner replacement before sending',async()=>{
 const {page,calls,toasts}=wechat(async o=>({statusCode:200,data:o.url.endsWith('/action')?{id:'item'}:snapshot()}));
 page.data.state={...snapshot(),deleted_items:[{id:'item',version:9,name:'衣物',assignee:'departed'}]};
 page.openRestoreItem({currentTarget:{dataset:{id:'item'}}});page.restoreItem();await settled(page);
 assert.equal(calls.length,0);assert.match(toasts.at(-1).title,/重新选择/);
 page.choose({currentTarget:{dataset:{key:'assignee'}},detail:{value:'0'}});page.restoreItem();await settled(page);
 assert.equal(calls[0].data.action,'item.restore');assert.deepEqual(calls[0].data.data,{id:'item',version:9,assignee:'mom'});
});

test('WeChat packing quantity rejects fractions before creating a command',async()=>{
 const {page,calls,toasts}=wechat(async()=>{throw Error('unexpected network')});page.data.form={name:'合成物品',quantity:'1.5'};
 page.addItem();await settled(page);assert.equal(calls.length,0);assert.match(toasts.at(-1).title,/整数/);
});

test('WeChat calendar export requires confirmation, shares an ICS file and removes the temporary copy',async()=>{
 const {page,calls,wx}=wechat(async()=>({statusCode:200,data:{filename:'baba-calendar.ics',content:'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n',event_count:0}}));
 const writes=[],removes=[],shares=[];wx.env={USER_DATA_PATH:'/synthetic-private'};
 wx.getFileSystemManager=()=>({writeFile:o=>{writes.push(o);o.success()},unlink:o=>{removes.push(o.filePath);o.success()}});
 wx.shareFileMessage=o=>{shares.push(o);o.success()};page.data.modal='calendar';page.data.form={start:'2026-10-28',end:'2026-11-28'};
 page.exportCalendar();await settled(page);assert.equal(calls.length,0);
 page.calendarConsent({detail:{value:['yes']}});page.exportCalendar();await settled(page);
 assert.match(calls[0].path,/\/calendar\?family_id=family&start=2026-10-28&end=2026-11-28$/);
 assert.equal(writes.length,1);assert.equal(writes[0].encoding,'utf8');assert.equal(shares[0].filePath,writes[0].filePath);assert.equal(shares[0].fileName,'baba-calendar.ics');assert.deepEqual(removes,[writes[0].filePath]);
});

test('WeChat calendar file is removed on cancellation and cannot be shared after session revocation',async()=>{
 const {page,app,wx}=wechat(async()=>({statusCode:200,data:{filename:'calendar.ics',content:'synthetic-calendar'}}));
 const removes=[],shares=[];let revoke=false;wx.env={USER_DATA_PATH:'/synthetic-private'};
 wx.getFileSystemManager=()=>({writeFile:o=>{if(revoke)app.clearSession();o.success()},unlink:o=>{removes.push(o.filePath);o.success()}});
 wx.shareFileMessage=o=>{shares.push(o);o.fail({errMsg:'shareFileMessage:fail cancel'})};
 page.data.form={start:'2026-10-28',end:'2026-10-28',confirmed:true};page.exportCalendar();await settled(page);assert.equal(shares.length,1);assert.equal(removes.length,1);
 revoke=true;page.data.form={start:'2026-10-28',end:'2026-10-28',confirmed:true};page.exportCalendar();await settled(page);
 assert.equal(shares.length,1);assert.equal(removes.length,2);assert.equal(page.data.auth,false);
});

test('WeChat startup removes only abandoned calendar export files',async()=>{
 const {app,wx}=wechat(async()=>{throw Error('unexpected request')});const removed=[];wx.env={USER_DATA_PATH:'/synthetic-private'};
 wx.getFileSystemManager=()=>({readdir:o=>o.success({files:['baba-calendar-123-abc.ics','other.ics','baba-session','../baba-calendar-1-x.ics','baba-calendar-123-abc.ics.keep']}),unlink:o=>{removed.push(o.filePath);o.success()}});
 await app.cleanCalendarFiles();assert.deepEqual(removed,['/synthetic-private/baba-calendar-123-abc.ics']);
});

const weekly=()=>({week_start:'2026-10-26',week_end:'2026-11-01',as_of:'2026-10-28',timezone:'Asia/Shanghai',is_current:true,scope:'visible_family',previous_week:'2026-10-19',next_week:null,counts:{tasks:1,items:0},completed:{tasks:[{id:'done',title:'合成完成',completed_date:'2026-10-27',assignee_name:'合成妈妈'}],items:[]},briefing:{enabled:true,entries:[],knowledge:[]}});

test('WeChat weekly report loads current facts and navigates to the server-normalized historical week',async()=>{
 const {page,calls}=wechat(async o=>{const r=weekly();if(o.url.includes('week=')){r.week_start='2026-10-19';r.week_end='2026-10-25';r.is_current=false}return {statusCode:200,data:r}});
 page.data.state=snapshot();page.report();await settled(page);assert.equal(page.data.modal,'report');assert.equal(page.data.weeklyReport.counts.tasks,1);assert.equal(page.data.form.week,'2026-10-26');
 page.setReportDate({detail:{value:'2026-10-21'}});page.selectedReport();await settled(page);
 assert.match(calls.at(-1).path,/\/report\?family_id=family&week=2026-10-21$/);assert.equal(page.data.form.week,'2026-10-19');assert.equal(page.data.weeklyReport.is_current,false);
});

test('WeChat report navigation hides earlier data while loading and discards a switched-family response',async()=>{
 const gate=deferred();const {page}=wechat(async()=>gate.promise);page.data.state=snapshot();page.data.weeklyReport=weekly();
 const result=page.loadReport('2026-10-19');assert.equal(page.data.weeklyReport,null);await new Promise(setImmediate);page.data.familyId='another-family';gate.resolve({statusCode:200,data:weekly()});await assert.rejects(result,e=>e.stale);assert.equal(page.data.weeklyReport,null);
});

test('WeChat denied report clears old details and foreground reload closes the report',async()=>{
 const {page}=wechat(async o=>o.url.includes('/report?')?{statusCode:403,data:{error:'access revoked'}}:{statusCode:200,data:snapshot()});page.data.state=snapshot();page.data.weeklyReport=weekly();
 page.report();await settled(page);assert.equal(page.data.weeklyReport,null);assert.equal(page.data.modal,'');
 page.data.weeklyReport=weekly();page.data.modal='report';page._shown=true;page.onShow();await settled(page);assert.equal(page.data.weeklyReport,null);assert.equal(page.data.modal,'');
});

test('WeChat recurring rule editor submits captured version, typed interval and new owner',async()=>{
 const {page,calls}=wechat(async o=>({statusCode:200,data:o.url.endsWith('/action')?{id:'series'}:snapshot()}));
 page.data.state={...snapshot(),task_series:[{id:'series',version:12,title:'整理',note:'说明',assignee:'mom',frequency:'daily',every:1,start_date:'2026-10-28',until_date:'2026-11-11'}]};
 page.editSeries({currentTarget:{dataset:{id:'series'}}});page.chooseSeriesFrequency({detail:{value:'1'}});page.field({currentTarget:{dataset:{key:'repeat_every'}},detail:{value:'2'}});page.clearRepeatUntil();
 page.saveSeries();await settled(page);const command=calls.find(c=>c.path.endsWith('/action')).data;assert.equal(command.action,'task_series.update');
 assert.deepEqual(command.data,{id:'series',version:12,title:'整理',note:'说明',assignee:'mom',repeat:'weekly',repeat_every:2,due_date:'2026-10-28',repeat_until:null});
});

test('WeChat recurring rule save respects cancel and rejects fractional intervals before sending',async()=>{
 const {page,calls,wx,toasts}=wechat(async()=>{throw Error('unexpected network')});page.data.state=snapshot();page.data.form={id:'series',version:2,repeat_every:'1',assignee:0};
 wx.showModal=o=>o.success({confirm:false});page.saveSeries();await settled(page);assert.equal(calls.length,0);
 page.data.form.repeat_every='1.5';page.saveSeries();await settled(page);assert.equal(calls.length,0);assert.match(toasts.at(-1).title,/整数/);
});

test('WeChat recurring pause and resume confirm and use current rule versions',async()=>{
 const {page,calls,wx}=wechat(async o=>({statusCode:200,data:o.url.endsWith('/action')?{id:'series'}:snapshot()}));
 page.data.state={...snapshot(),task_series:[{id:'series',version:3,status:'active'}]};wx.showModal=o=>o.success({confirm:false});page.toggleSeries({currentTarget:{dataset:{id:'series'}}});await settled(page);assert.equal(calls.length,0);
 wx.showModal=o=>o.success({confirm:true});page.toggleSeries({currentTarget:{dataset:{id:'series'}}});await settled(page);assert.equal(calls[0].data.action,'task_series.pause');assert.equal(calls[0].data.data.version,3);
 page.data.state.task_series=[{id:'series',version:4,status:'paused'}];page.toggleSeries({currentTarget:{dataset:{id:'series'}}});await settled(page);const commands=calls.filter(c=>c.path.endsWith('/action'));assert.equal(commands[1].data.action,'task_series.resume');assert.equal(commands[1].data.data.version,4);
});

test('WeChat paused or superseded occurrence cannot be completed directly',async()=>{
 const {page,calls,toasts}=wechat(async()=>{throw Error('unexpected request')});page.data.state={...snapshot(),tasks:[{id:'task',version:3,assignee:'mom',status:'cancelled',series_locked:'paused'}]};
 page.completeTask({currentTarget:{dataset:{id:'task'}}});await settled(page);assert.equal(calls.length,0);assert.match(toasts.at(-1).title,/暂停或关闭/);
});

test('WeChat task detail saves normalized steps and selected knowledge with the opening version',async()=>{
 const {page,calls}=wechat(async o=>({statusCode:200,data:o.url.endsWith('/action')?{id:'task'}:snapshot()}));
 page.data.role='dad';page.data.state={...snapshot(),knowledge:[{id:'k',title:'生活知识'}],tasks:[{id:'task',version:5,title:'整理',note:'原说明',assignee:'mom',steps:['原步骤'],knowledge_ids:['k'],linked_knowledge:[]}]};
 page.openTask({currentTarget:{dataset:{id:'task'}}});page.field({currentTarget:{dataset:{key:'steps_text'}},detail:{value:'  先沟通  \n\n再分工'}});page.chooseTaskKnowledge({detail:{value:['k']}});page.addTask();await settled(page);
 const c=calls.find(c=>c.path.endsWith('/action')).data;assert.equal(c.action,'task.update');assert.equal(c.data.version,5);assert.deepEqual(c.data.steps,['先沟通','再分工']);assert.deepEqual(c.data.knowledge_ids,['k']);assert.equal(page.data.taskDetail,null);
});

test('WeChat linked knowledge is fetched through the task-scoped endpoint before reading',async()=>{
 const {page,calls}=wechat(async()=>({statusCode:200,data:{knowledge:{id:'k',title:'最新生活知识',body:'当前版本'}}}));
 page.data.taskDetail={id:'task'};page.readTaskKnowledge({currentTarget:{dataset:{id:'k'}}});await settled(page);
 assert.match(calls[0].path,/\/task\/knowledge\?family_id=family&task_id=task&knowledge_id=k$/);assert.equal(page.data.modal,'read');assert.equal(page.data.knowledge.title,'最新生活知识');
});

test('WeChat readonly task exposes steps without enabling editing and foreground clears the draft',async()=>{
 const {page}=wechat(async()=>({statusCode:200,data:snapshot()}));page.data.role='mom';page.data.state={...snapshot(),tasks:[{id:'task',version:1,title:'只读任务',note:'说明',assignee:'mom',steps:['第一步'],knowledge_ids:[]}]};
 page.openTask({currentTarget:{dataset:{id:'task'}}});assert.equal(page.data.taskReadOnly,true);assert.equal(page.data.taskDetail.stepRows[0].text,'第一步');page._shown=true;page.onShow();await settled(page);assert.equal(page.data.taskDetail,null);assert.equal(page.data.modal,'');
});
