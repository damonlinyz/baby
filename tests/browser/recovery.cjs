const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_BASE,ctx=await request.newContext({baseURL:base});
 const api=async(path,data,actor)=>{const headers=actor?{Authorization:'Bearer '+actor.session_token,'X-CSRF-Token':actor.csrf}:{};const r=data===undefined?await ctx.get('/api'+path,{headers}):await ctx.post('/api'+path,{headers,data});assert.equal(r.status(),200,await r.text());return r.json()};
 const dad=await api('/register',{login:'retry-dad',name:'合成统筹',password:'browser-password-123'}),mom=await api('/register',{login:'retry-mom',name:'合成妈妈',password:'browser-password-123'});
 const {family_id:fid}=await api('/families',{name:'弱网验证家庭'},dad),act=(actor,action,data)=>api('/action',{family_id:fid,action,data,request_id:crypto.randomUUID()},actor);
 const invitation=await act(dad,'invite.create',{role:'mom'});await api('/join',{code:invitation.invite_code},mom);await act(mom,'pregnancy.create',{consent:true});let s=await api('/state?family_id='+fid,undefined,mom);
 await act(mom,'pregnancy.event',{id:s.pregnancy.id,version:s.pregnancy.version,event:'set_edd',date:process.env.TEST_EDD});await act(mom,'consent.update',{scope:'health_read',grantee:dad.user.id,enabled:true});
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.BABA_TEST_CHROMIUM_NO_SANDBOX==='1'?['--no-sandbox']:[]});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#auth-form [name=login]').fill('retry-dad');await page.locator('#auth-form [name=password]').fill('browser-password-123');await page.locator('#auth-form button').click();await page.locator('.journey .week').waitFor();
  const seen=[];let losses=1;
  const dropAction=async route=>{const body=route.request().postDataJSON();seen.push(body);if(losses-->0){await route.fetch();await route.abort('failed')}else await route.continue()};
  await page.route('**/api/action',dropAction);
  const add=async title=>{await page.locator('[data-action=add-task]').first().click();await page.locator('#task-form [name=title]').fill(title);await page.locator('#task-form button.primary-btn').click()};
  await add('丢响应只创建一次');await page.locator('#task-form').waitFor({state:'detached'});assert.equal(seen.length,2);assert.equal(seen[0].request_id,seen[1].request_id);
  s=await api('/state?family_id='+fid,undefined,dad);assert.equal(s.tasks.filter(t=>t.title==='丢响应只创建一次').length,1);
  await page.unroute('**/api/action',dropAction);
  let refreshLoss=2;const dropState=async route=>{if(refreshLoss-->0)await route.abort('failed');else await route.continue()};
  await page.route('**/api/state?*',dropState);
  const refreshIds=[];const capture=async route=>{refreshIds.push(route.request().postDataJSON());await route.continue()};await page.route('**/api/action',capture);
  await add('已保存但刷新失败');await page.locator('#request-status [data-action=recover-command]').waitFor();await page.locator('[data-action=reload]').waitFor();assert.equal(refreshIds.length,1);
  await page.locator('[data-action=recover-command]').click();await page.waitForFunction(()=>!apiRecovery.pending().pending&&!busy);assert.equal(refreshIds.length,2);assert.equal(refreshIds[0].request_id,refreshIds[1].request_id);
  s=await api('/state?family_id='+fid,undefined,dad);assert.equal(s.tasks.filter(t=>t.title==='已保存但刷新失败').length,1);
  await page.unroute('**/api/state?*',dropState);await page.unroute('**/api/action',capture);
  losses=2;seen.length=0;await page.route('**/api/action',dropAction);await add('两次丢响应仍只创建一次');await page.locator('[data-action=recover-command]').waitFor();assert.equal(seen.length,2);
  await page.locator('#task-form [name=title]').fill('不能偷偷提交新修改');await page.locator('#task-form button.primary-btn').click();await page.waitForFunction(()=>!busy);assert.equal(seen.length,2);
  await page.locator('[data-action=recover-command]').click();await page.waitForFunction(()=>!apiRecovery.pending().pending&&!busy);assert.equal(seen.length,3);assert.equal(seen[0].request_id,seen[2].request_id);
  s=await api('/state?family_id='+fid,undefined,dad);assert.equal(s.tasks.filter(t=>t.title==='两次丢响应仍只创建一次').length,1);assert.equal(s.tasks.some(t=>t.title==='不能偷偷提交新修改'),false);
  await page.unroute('**/api/action',dropAction);
  await page.locator('.journey [data-action=timeline]').click();await page.locator('#timeline-form').waitFor();assert.equal(await page.locator('#timeline-form [name=date]').inputValue(),process.env.TEST_EDD);
  await act(mom,'consent.update',{scope:'health_read',grantee:dad.user.id,enabled:false});await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await page.waitForFunction(()=>!busy);
  await page.locator('.journey .week').waitFor();assert.match(await page.locator('.journey .week').innerText(),/关心，也尊重边界/);assert.equal(await page.locator('.modal').count(),0);assert.equal((await page.locator('body').innerText()).includes(process.env.TEST_EDD),false);
  // Two reads in the same session may return in reverse order around revocation.
  await act(mom,'consent.update',{scope:'health_read',grantee:dad.user.id,enabled:true});
  let releaseOld,oldReady;const oldGate=new Promise(r=>releaseOld=r),ready=new Promise(r=>oldReady=r);let readCount=0;
  const reorder=async route=>{if(++readCount===1){const response=await route.fetch();oldReady();await oldGate;await route.fulfill({response})}else await route.continue()};
  await page.route('**/api/state?*',reorder);
  const oldRead=page.evaluate(()=>refresh().then(()=>({stale:false}),e=>({stale:e.stale})));await ready;
  await act(mom,'consent.update',{scope:'health_read',grantee:dad.user.id,enabled:false});await page.evaluate(()=>refresh());
  releaseOld();assert.equal((await oldRead).stale,true);assert.match(await page.locator('.journey .week').innerText(),/关心，也尊重边界/);
  await page.unroute('**/api/state?*',reorder);
  await page.locator('[data-action=add-task]').first().click();await page.locator('#task-form [name=title]').fill('退出前草稿');
  await page.evaluate(async()=>{await api('/logout',{});try{await api('/me')}catch{}});await page.locator('#auth-form').waitFor();assert.equal(await page.locator('.modal').count(),0);assert.equal((await page.locator('body').innerText()).includes('弱网验证家庭'),false);
  assert.deepEqual(errors,[]);console.log('PASS: real HTTP lost-response replay, refresh recovery, changed-input blocking, foreground revocation, out-of-order snapshots and expired-session clearing');
 }finally{await browser.close();await ctx.dispose()}
})().catch(e=>{console.error(e);process.exit(1)});
