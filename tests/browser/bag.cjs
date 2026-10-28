const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_BASE,ctx=await request.newContext({baseURL:base});
 const api=async(path,data,actor)=>{const headers=actor?{Authorization:'Bearer '+actor.session_token,'X-CSRF-Token':actor.csrf}:{};const r=data===undefined?await ctx.get('/api'+path,{headers}):await ctx.post('/api'+path,{headers,data});assert.equal(r.status(),200,await r.text());return r.json()};
 const dad=await api('/register',{login:'bag-dad',name:'合成统筹',password:'browser-password-123'});
 const mom=await api('/register',{login:'bag-mom',name:'合成妈妈',password:'browser-password-123'});
 const {family_id:fid}=await api('/families',{name:'物品维护验证'},dad);
 let key=0;const command=(action,data,actor=dad)=>api('/action',{family_id:fid,action,data,request_id:'bag-'+(++key)},actor);
 const invitation=await command('invite.create',{role:'mom'});await api('/join',{code:invitation.invite_code},mom);
 const state=()=>api('/state?family_id='+fid,undefined,dad);
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.BABA_TEST_CHROMIUM_NO_SANDBOX==='1'?['--no-sandbox']:[]});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#auth-form [name=login]').fill('bag-dad');await page.locator('#auth-form [name=password]').fill('browser-password-123');await page.locator('#auth-form button').click();await page.locator('.journey').waitFor();
  await page.locator('#navigation [data-page=bag]').click();await page.locator('[data-action=add-item]').click();
  await page.locator('#item-form [name=name]').fill('合成衣物');await page.locator('#item-form [name=quantity]').fill('3');await page.locator('#item-form [name=pack]').selectOption('宝宝用品包');await page.locator('#item-form [name=level]').selectOption('必备');await page.locator('#item-form [name=note]').fill('核对实际需要');await page.locator('#item-form button.primary-btn').click();await page.locator('#item-form').waitFor({state:'detached'});
  let s=await state(),i=s.items.find(i=>i.name==='合成衣物');assert.equal(i.quantity,3);assert.equal(i.level,'必备');
  await page.locator('[data-item="'+i.id+'"]').click();await page.waitForFunction(id=>state.items.find(i=>i.id===id).status==='done',i.id);
  await page.locator('[data-item-edit="'+i.id+'"]').click();await page.locator('#item-form [name=quantity]').fill('5');await page.locator('#item-form [name=level]').selectOption('可选');await page.locator('#item-form [name=pack]').selectOption('妈妈入院包');await page.locator('#item-form button.primary-btn').click();await page.locator('#item-form').waitFor({state:'detached'});
  i=(await state()).items.find(t=>t.id===i.id);assert.equal(i.quantity,5);assert.equal(i.status,'todo');assert.equal(i.completed_at,null);
  s=await state();await command('family.update',{version:s.family.version,mode:'all'});await page.reload();await page.locator('[data-item="'+i.id+'"]').waitFor();await page.locator('[data-item="'+i.id+'"]').click();await page.waitForFunction(id=>state.items.find(i=>i.id===id).status==='pending',i.id);
  const approval=(await state()).approvals.find(a=>a.resource==='item'&&a.target===i.id&&a.status==='pending');assert.ok(approval);
  const seen=[];let lost=false;const drop=async route=>{const body=route.request().postDataJSON();if(body.action==='item.delete'){seen.push(body);if(!lost){lost=true;await route.fetch();await route.abort('failed');return}}await route.continue()};
  await page.route('**/api/action',drop);await page.locator('[data-item-edit="'+i.id+'"]').click();page.once('dialog',dialog=>dialog.accept());await page.locator('[data-item-delete="'+i.id+'"]').click();await page.locator('#item-form').waitFor({state:'detached'});await page.unroute('**/api/action',drop);
  assert.equal(seen.length,2);assert.equal(seen[0].request_id,seen[1].request_id);
  s=await state();assert.equal(s.items.some(t=>t.id===i.id),false);assert.equal(s.deleted_items.filter(t=>t.id===i.id).length,1);assert.equal(s.approvals.find(a=>a.id===approval.id).status,'cancelled');
  const downloadPromise=page.waitForEvent('download');await page.locator('[data-action=export-bag]').click();const download=await downloadPromise;const stream=await download.createReadStream();let contents='';for await(const part of stream)contents+=part.toString('utf8');assert.equal(contents.includes('合成衣物'),false);
  await page.locator('[data-item-restore="'+i.id+'"]').click();await page.locator('#item-restore-form [name=assignee]').selectOption(mom.user.id);await page.locator('#item-restore-form button').click();await page.locator('#item-restore-form').waitFor({state:'detached'});
  i=(await state()).items.find(t=>t.id===i.id);assert.equal(i.assignee,mom.user.id);assert.equal(i.status,'in_progress');assert.equal(await page.locator('[data-item="'+i.id+'"]').isDisabled(),true);
  // Content editor retains its opening version when another client updates the item.
  await page.locator('[data-item-edit="'+i.id+'"]').click();await command('item.update',{id:i.id,version:i.version,quantity:7});await page.locator('#item-form [name=quantity]').fill('8');await page.locator('#item-form button.primary-btn').click();await page.locator('#item-form').waitFor({state:'detached'});
  assert.equal((await state()).items.find(t=>t.id===i.id).quantity,7);
  // Departed owners must be explicitly replaced when restoring.
  i=(await state()).items.find(t=>t.id===i.id);await command('item.delete',{id:i.id,version:i.version});await command('member.leave',{},mom);await page.reload();await page.locator('[data-item-restore="'+i.id+'"]').click();assert.equal(await page.locator('#item-restore-form [name=assignee]').inputValue(),'');await page.locator('#item-restore-form button').click();assert.equal(await page.locator('#item-restore-form').count(),1);
  await page.locator('#item-restore-form [name=assignee]').selectOption(dad.user.id);await page.locator('#item-restore-form button').click();await page.locator('#item-restore-form').waitFor({state:'detached'});
  assert.equal((await state()).items.find(t=>t.id===i.id).assignee,dad.user.id);assert.deepEqual(errors,[]);
  console.log('PASS: packing create/edit, quantity and priority, completion reset, pending deletion with lost response, export exclusion, restore assignment and stale-version protection');
 }finally{await browser.close();await ctx.dispose()}
})().catch(e=>{console.error(e);process.exit(1)});
