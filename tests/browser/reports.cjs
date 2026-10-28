const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_BASE,ctx=await request.newContext({baseURL:base});
 const api=async(path,data,actor)=>{const headers=actor?{Authorization:'Bearer '+actor.session_token,'X-CSRF-Token':actor.csrf}:{};const r=data===undefined?await ctx.get('/api'+path,{headers}):await ctx.post('/api'+path,{headers,data});assert.equal(r.status(),200,await r.text());return r.json()};
 const dad=await api('/register',{login:'weekly-dad',name:'合成统筹',password:'browser-password-123'}),mom=await api('/register',{login:'weekly-mom',name:'合成妈妈',password:'browser-password-123'});
 const {family_id:fid}=await api('/families',{name:'每周报告验证'},dad);let key=0;
 const command=(action,data,actor=dad)=>api('/action',{family_id:fid,action,data,request_id:'weekly-'+(++key)},actor);
 const state=()=>api('/state?family_id='+fid,undefined,dad);
 let s=await state();const current=s.calendar_date,task=s.tasks[0],item=s.items[0];await command('task.update',{id:task.id,version:task.version,status:'done'});await command('item.update',{id:item.id,version:item.version,status:'done'});
 const plus=days=>{const date=new Date(current+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+days);return date.toISOString().slice(0,10)};
 await command('task.create',{title:'本周已有安排 <script>',due_date:current});await command('task.create',{title:'未来安排不进本周简报',due_date:plus(14)});
 const invite=await command('invite.create',{role:'mom'});await api('/join',{code:invite.invite_code},mom);await command('pregnancy.create',{consent:true},mom);await command('appointment.create',{title:'仅授权后可见预约',date:current},mom);await command('consent.update',{scope:'health_read',grantee:dad.user.id,enabled:true},mom);
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.BABA_TEST_CHROMIUM_NO_SANDBOX==='1'?['--no-sandbox']:[]});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base);
  await page.locator('#auth-form [name=login]').fill('weekly-dad');await page.locator('#auth-form [name=password]').fill('browser-password-123');await page.locator('#auth-form button').click();await page.locator('.journey').waitFor();
  assert.match(await page.locator('.briefing').innerText(),/本周已有安排 <script>/);assert.equal((await page.locator('.briefing').innerText()).includes('未来安排不进本周简报'),false);assert.match(await page.locator('.briefing').innerText(),/仅授权后可见预约/);
  const loaded=()=>page.locator('#report-form').waitFor();await page.locator('[data-action=report]').click();await loaded();
  assert.match(await page.locator('.modal').innerText(),/完成 1 件任务/);assert.match(await page.locator('.modal').innerText(),/备齐 1 种物品/);assert.match(await page.locator('.modal').innerText(),/历史周并非当时的快照/);
  const r=(await state()).weekly_report;await page.locator('[data-report-week="'+r.previous_week+'"]').click();await loaded();assert.equal(await page.locator('#report-form [name=week]').inputValue(),r.previous_week);assert.match(await page.locator('.modal').innerText(),/完成 0 件任务/);
  await page.locator('.modal [data-action=report]').click();await loaded();assert.equal(await page.locator('#report-form [name=week]').inputValue(),r.week_start);
  await page.locator('#report-form [name=week]').fill('2025-12-31');await page.locator('#report-form button').click();await loaded();assert.equal(await page.locator('#report-form [name=week]').inputValue(),'2025-12-29');assert.match(await page.locator('.modal').innerText(),/2025-12-29 — 2026-01-04/);
  await command('consent.update',{scope:'health_read',grantee:dad.user.id,enabled:false},mom);await page.locator('.modal [data-action=report]').click();await loaded();assert.equal((await page.locator('.modal').innerText()).includes('仅授权后可见预约'),false);
  s=await state();const done=s.tasks.find(t=>t.id===task.id);await command('task.update',{id:done.id,version:done.version,status:'todo'});await page.locator('#report-form button').click();await loaded();assert.match(await page.locator('.modal').innerText(),/完成 0 件任务/);
  await page.locator('.modal [data-action=close]').click();await page.reload();await page.locator('[data-action=report]').click();await loaded();assert.match(await page.locator('.modal').innerText(),/完成 0 件任务/);assert.deepEqual(errors,[]);
  console.log('PASS: weekly facts, static date selection, historical navigation and date normalization, authorization revocation, corrected completion and safe title rendering');
 }finally{await browser.close();await ctx.dispose()}
})().catch(e=>{console.error(e);process.exit(1)});
