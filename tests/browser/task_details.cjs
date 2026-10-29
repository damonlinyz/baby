const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_BASE,ctx=await request.newContext({baseURL:base});
 const api=async(path,data,actor)=>{const headers=actor?{Authorization:'Bearer '+actor.session_token,'X-CSRF-Token':actor.csrf}:{};const r=data===undefined?await ctx.get('/api'+path,{headers}):await ctx.post('/api'+path,{headers,data});assert.equal(r.status(),200,await r.text());return r.json()};
 const dad=await api('/register',{login:'details-dad',name:'合成统筹',password:'browser-password-123'}),mom=await api('/register',{login:'details-mom',name:'合成妈妈',password:'browser-password-123'});
 const {family_id:fid}=await api('/families',{name:'任务详情验证'},dad);let seq=0;
 const command=(action,data)=>api('/action',{family_id:fid,action,data,request_id:'details-'+(++seq)},dad);
 const invitation=await command('invite.create',{role:'mom'});await api('/join',{code:invitation.invite_code},mom);
 const state=()=>api('/state?family_id='+fid,undefined,dad);
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.BABA_TEST_CHROMIUM_NO_SANDBOX==='1'?['--no-sandbox']:[]});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const login=async(page,user)=>{await page.goto(base);await page.locator('#auth-form [name=login]').fill(user);await page.locator('#auth-form [name=password]').fill('browser-password-123');await page.locator('#auth-form button').click();await page.locator('.journey').waitFor();await page.locator('#navigation [data-page=tasks]').click()};
  await login(page,'details-dad');await page.locator('[data-action=add-task]').click();await page.locator('#task-form [name=title]').fill('有步骤的整理');await page.locator('#task-form [name=steps_text]').fill(Array(13).fill('过多步骤').join('\n'));await page.locator('#task-form [name=knowledge_ids][value=base-1]').check();await page.locator('#task-form button.primary-btn').click();await page.locator('#toast').filter({hasText:'最多 12 条'}).waitFor();assert.equal(await page.locator('#task-form').count(),1);
  const safeSteps=['先盘点已有物品','显示文字 <img src=x onerror=alert(1)>'];await page.locator('#task-form [name=steps_text]').fill(safeSteps.join('\n'));await page.locator('#task-form button.primary-btn').click();await page.locator('#task-form').waitFor({state:'detached'});
  let task=(await state()).tasks.find(t=>t.title==='有步骤的整理');assert.deepEqual(task.steps,safeSteps);assert.equal(task.status,'todo');assert.equal(task.linked_knowledge[0].id,'base-1');
  await page.locator('[data-task-detail="'+task.id+'"]').click();await page.locator('[data-task-knowledge=base-1]').click();await page.locator('.reading').waitFor();assert.match(await page.locator('.reading').innerText(),/内容版本：1/);await page.locator('.modal [data-action=close]').click();
  // The old reference button must fail after a different client unlinks the content.
  await page.locator('[data-task-detail="'+task.id+'"]').click();await command('task.update',{id:task.id,version:task.version,knowledge_ids:[]});await page.locator('[data-task-knowledge=base-1]').click();await page.locator('#task-form').waitFor({state:'detached'});assert.equal(await page.locator('.reading').count(),0);
  task=(await state()).tasks.find(t=>t.id===task.id);await command('task.update',{id:task.id,version:task.version,assignee:mom.user.id});await page.reload();await page.locator('[data-task-detail="'+task.id+'"]').click();
  task=(await state()).tasks.find(t=>t.id===task.id);await command('task.update',{id:task.id,version:task.version,steps:['另一客户端的步骤']});await page.locator('#task-form [name=steps_text]').fill('过期表单不能覆盖');await page.locator('#task-form button.primary-btn').click();await page.locator('#task-form').waitFor({state:'detached'});assert.deepEqual((await state()).tasks.find(t=>t.id===task.id).steps,['另一客户端的步骤']);
  const momContext=await browser.newContext(),momPage=await momContext.newPage();momPage.on('pageerror',e=>errors.push(e.message));await login(momPage,'details-mom');await momPage.locator('[data-task-detail="'+task.id+'"]').click();assert.equal(await momPage.locator('#task-form button.primary-btn').count(),0);assert.match(await momPage.locator('#task-form ol').innerText(),/另一客户端的步骤/);await momPage.locator('.modal [data-action=close]').click();
  // Ordinary seed tasks also expose the original, explicitly non-clinical template source.
  const seed=(await state()).tasks.find(t=>t.template_source);await momPage.locator('[data-task-detail="'+seed.id+'"]').click();assert.match(await momPage.locator('.modal').innerText(),/原模板来源：产品编辑/);assert.equal(await momPage.locator('#task-form ol li').count(),3);
  await momContext.close();assert.deepEqual(errors,[]);console.log('PASS: bounded step editing, live reference reading and removal, captured task version, executor readonly details and life-template provenance');
 }finally{await browser.close();await ctx.dispose()}
})().catch(e=>{console.error(e);process.exit(1)});
