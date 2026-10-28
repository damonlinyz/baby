const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_BASE,ctx=await request.newContext({baseURL:base});
 const api=async(path,data,actor)=>{const headers=actor?{Authorization:'Bearer '+actor.session_token,'X-CSRF-Token':actor.csrf}:{};const r=data===undefined?await ctx.get('/api'+path,{headers}):await ctx.post('/api'+path,{headers,data});assert.equal(r.status(),200,await r.text());return r.json()};
 const dad=await api('/register',{login:'recurring-dad',name:'合成统筹',password:'browser-password-123'});
 const {family_id:fid}=await api('/families',{name:'重复家务验证'},dad);
 const state=()=>api('/state?family_id='+fid,undefined,dad);
 const current=(await state()).calendar_date;
 const plus=days=>{const date=new Date(current+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+days);return date.toISOString().slice(0,10)};
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.BABA_TEST_CHROMIUM_NO_SANDBOX==='1'?['--no-sandbox']:[]});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#auth-form [name=login]').fill('recurring-dad');await page.locator('#auth-form [name=password]').fill('browser-password-123');await page.locator('#auth-form button').click();await page.locator('.journey').waitFor();
  await page.locator('#navigation [data-page=tasks]').click();
  const create=async(title,frequency,every,end)=>{
   await page.locator('[data-action=add-task]').click();await page.locator('#task-form [name=title]').fill(title);await page.locator('#task-form [name=repeat]').selectOption(frequency);
   await page.locator('#task-form [name=due_date]').fill(current);await page.locator('#task-form [name=repeat_every]').fill(String(every));await page.locator('#task-form [name=repeat_until]').fill(end);
   await page.locator('#task-form button.primary-btn').click();await page.locator('#task-form').waitFor({state:'detached'});
  };
  const seen=[];let lost=false;
  const drop=async route=>{const data=route.request().postDataJSON();seen.push(data);if(!lost){lost=true;await route.fetch();await route.abort('failed')}else await route.continue()};
  await page.route('**/api/action',drop);await create('每两天整理厨房','daily',2,plus(6));await page.unroute('**/api/action',drop);
  assert.equal(seen.length,2);assert.equal(seen[0].request_id,seen[1].request_id);
  let s=await state();assert.equal(s.task_series.length,1);const series=s.task_series[0];let tasks=s.tasks.filter(t=>t.series_id===series.id);
  assert.deepEqual(tasks.map(t=>t.due_date),[current,plus(2),plus(4),plus(6)]);
  await page.locator('[data-task-detail="'+tasks[0].id+'"]').click();assert.match(await page.locator('.modal').innerText(),/只影响本次/);
  await page.locator('#task-form [name=title]').fill('厨房单次改期');await page.locator('#task-form [name=due_date]').fill(plus(1));await page.locator('#task-form button.primary-btn').click();await page.locator('#task-form').waitFor({state:'detached'});
  s=await state();assert.equal(s.tasks.find(t=>t.id===tasks[0].id).series_date,current);assert.equal(s.tasks.find(t=>t.id===tasks[0].id).due_date,plus(1));assert.equal(s.tasks.find(t=>t.id===tasks[1].id).title,'每两天整理厨房');
  await page.locator('[data-task="'+tasks[1].id+'"]').click();await page.waitForFunction(id=>state.tasks.find(t=>t.id===id).status==='done',tasks[1].id);
  page.once('dialog',dialog=>dialog.accept());await page.locator('[data-stop-series="'+series.id+'"]').click();await page.locator('[data-stop-series="'+series.id+'"]').waitFor({state:'detached'});
  s=await state();tasks=s.tasks.filter(t=>t.series_id===series.id);assert.equal(s.task_series[0].status,'stopped');assert.equal(tasks.filter(t=>t.status==='done').length,1);assert.equal(tasks.filter(t=>t.status==='cancelled').length,3);
  const closed=tasks.find(t=>t.status==='cancelled');assert.equal(await page.locator('[data-task="'+closed.id+'"]').isDisabled(),true);
  await page.locator('[data-task-detail="'+closed.id+'"]').click();assert.equal(await page.locator('#task-form button.primary-btn').count(),0);await page.locator('.modal [data-action=close]').click();
  await create('每周家庭整理','weekly',1,plus(14));s=await state();const weekly=s.task_series.find(t=>t.title==='每周家庭整理');assert.deepEqual(s.tasks.filter(t=>t.series_id===weekly.id).map(t=>t.due_date),[current,plus(7),plus(14)]);
  // Disabled recurrence fields cannot block the unchanged one-time task flow.
  await page.locator('[data-action=add-task]').click();await page.locator('#task-form [name=title]').fill('普通单次任务');await page.locator('#task-form [name=repeat]').selectOption('daily');await page.locator('#task-form [name=repeat_every]').fill('0');await page.locator('#task-form [name=repeat]').selectOption('none');await page.locator('#task-form button.primary-btn').click();await page.locator('#task-form').waitFor({state:'detached'});
  s=await state();assert.equal(s.task_series.length,2);assert.equal(s.tasks.filter(t=>t.title==='普通单次任务'&&!t.series_id).length,1);
  assert.deepEqual(errors,[]);console.log('PASS: recurring daily and weekly creation, lost-response replay, single-instance edit, independent completion, stop and one-time-task compatibility');
 }finally{await browser.close();await ctx.dispose()}
})().catch(e=>{console.error(e);process.exit(1)});
