const test=require('node:test');
const assert=require('node:assert/strict');
const Recovery=require('../clients/weapp/lib/recovery');
const input=(id='first',title='合成任务')=>({family_id:'family',action:'task.create',data:{title,note:'synthetic'},request_id:id});
const ok=body=>({status:200,body});
const make=(send,extra={})=>Recovery.create({send,scope:()=> 'session',delay:async()=>{},...extra});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};

test('lost committed response retries exactly the same command',async()=>{
 const seen=[],ledger=new Map();let effects=0;
 const client=make(async(path,data)=>{seen.push(JSON.stringify(data));if(!ledger.has(data.request_id)){effects++;ledger.set(data.request_id,{id:'task'})}if(seen.length===1)throw Error('lost response');return ok(ledger.get(data.request_id))});
 assert.deepEqual(await client.command(input(),async()=>{}),{id:'task'});
 assert.equal(effects,1);assert.equal(seen.length,2);assert.equal(seen[0],seen[1]);assert.equal(client.pending().pending,false);
});

test('two lost responses retain the request id and block edited commands',async()=>{
 const ids=[];let available=false;
 const client=make(async(path,data)=>{ids.push(data.request_id);if(!available)throw Error('offline');return ok({id:'task'})});
 await assert.rejects(client.command(input(),async()=>{}),e=>e.recoverable&&!e.committed);
 await assert.rejects(client.command(input('new','修改后的名称'),async()=>{}),/上次提交/);
 assert.equal(ids.length,2);available=true;
 await client.command(input('fresh-id'),async()=>{});
 assert.deepEqual(ids,['first','first','first']);assert.equal(client.pending().pending,false);
});

test('successful save with failed refresh remains recoverable using original request',async()=>{
 const ids=[];let confirmations=0;
 const client=make(async(path,data)=>{ids.push(data.request_id);return ok({id:'task'})});
 await assert.rejects(client.command(input(),async()=>{throw Error('refresh offline')}),e=>e.committed&&e.recoverable);
 assert.equal(client.pending().committed,true);
 await client.retry(async()=>{confirmations++});
 assert.deepEqual(ids,['first','first']);assert.equal(confirmations,1);assert.equal(client.pending().pending,false);
});

test('concurrent identical commands share the flight including confirmation',async()=>{
 const gate=deferred();let sends=0,confirms=0;
 const client=make(async()=>{sends++;await gate.promise;return ok({ok:true})});
 const first=client.command(input(),async()=>{confirms++});
 const second=client.command({request_id:'other',data:{note:'synthetic',title:'合成任务'},action:'task.create',family_id:'family'},async()=>{confirms++});
 assert.equal(first,second);gate.resolve();await first;assert.equal(sends,1);assert.equal(confirms,1);
});

test('definitive permission, version and rate-limit failures are never retried',async()=>{
 for(const status of [400,403,409,429]){
  let sends=0;const client=make(async()=>{sends++;return {status,body:{error:'refused'}}});
  await assert.rejects(client.command(input(),async()=>assert.fail()),e=>e.status===status);
  assert.equal(sends,1);assert.equal(client.pending().pending,false);
 }
});

test('reads retry once while unrelated posts including purchases do not',async()=>{
 let calls=0;const client=make(async()=>{calls++;throw Error('offline')});
 await assert.rejects(client.request('/state?family_id=family'));assert.equal(calls,2);
 for(const path of ['/families','/login','/orders','/ai','/refunds','/pregnancy/preview']){
  calls=0;await assert.rejects(client.request(path,{request_id:'id'}));assert.equal(calls,1,path);
 }
});

test('server errors and malformed successful responses retry commands with the same id',async()=>{
 for(const response of [{status:503,body:{error:'busy'}},{status:200,body:null}]){
  const ids=[];const client=make(async(path,data)=>{ids.push(data.request_id);return ids.length===1?response:ok({ok:true})});
  await client.command(input(),async()=>{});assert.deepEqual(ids,['first','first']);
 }
});

test('late response from previous account cannot be returned or confirmed',async()=>{
 const gate=deferred();let session='old',confirmed=0;
 const client=make(async()=>gate.promise,{scope:()=>session});
 const result=client.command(input(),async()=>{confirmed++});session='new';gate.resolve(ok({private:'old-health'}));
 await assert.rejects(result,e=>e.stale);assert.equal(confirmed,0);assert.equal(client.pending().pending,false);
});

test('late old-account 401 cannot sign out the new account',async()=>{
 const gate=deferred();let session='old',signedOut=0;
 const client=make(async()=>gate.promise,{scope:()=>session,onUnauthorized:()=>signedOut++});
 const read=client.request('/state');session='new';gate.resolve({status:401,body:{error:'expired'}});
 await assert.rejects(read,e=>e.stale);assert.equal(signedOut,0);
});

test('reset cancels an automatic retry before another request is sent',async()=>{
 const gate=deferred();let sends=0;
 const client=make(async()=>{sends++;throw Error('offline')},{delay:()=>gate.promise});
 const result=client.command(input(),async()=>{});await new Promise(setImmediate);client.reset();gate.resolve();
 await assert.rejects(result,e=>e.stale);assert.equal(sends,1);assert.equal(client.pending().pending,false);
});

test('active-account expiry clears pending state and invokes sign-out once',async()=>{
 let count=0,client;client=make(async()=>({status:401,body:{error:'expired'}}),{onUnauthorized:()=>{count++;client.reset()}});
 await assert.rejects(client.command(input(),async()=>{}),e=>e.status===401);
 assert.equal(count,1);assert.equal(client.pending().pending,false);
});

test('caller editing the original object cannot mutate a retained command',async()=>{
 let available=false;const titles=[];const original=input();
 const client=make(async(path,data)=>{titles.push(data.data.title);if(!available)throw Error('offline');return ok({ok:true})});
 await assert.rejects(client.command(original,async()=>{}));original.data.title='later edit';available=true;
 await client.retry(async()=>{});assert.deepEqual(titles,['合成任务','合成任务','合成任务']);
});

test('timeout DOMExceptions produce a recoverable user message',async()=>{
 const client=make(async()=>{throw new DOMException('aborted','AbortError')});
 await assert.rejects(client.command(input(),async()=>{}),e=>e.recoverable&&/核对上次提交/.test(e.message));
});

test('retry with no pending command has no network side effects',async()=>{
 let calls=0;const client=make(async()=>{calls++;return ok({ok:true})});
 await assert.rejects(client.retry(async()=>{}),/没有待核对/);assert.equal(calls,0);
});
