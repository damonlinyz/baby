const {chromium,request}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.TEST_BASE,ctx=await request.newContext({baseURL:base});
 const api=async(path,data,actor)=>{const headers=actor?{Authorization:'Bearer '+actor.session_token,'X-CSRF-Token':actor.csrf}:{};const r=data===undefined?await ctx.get('/api'+path,{headers}):await ctx.post('/api'+path,{headers,data});assert.equal(r.status(),200,await r.text());return r.json()};
 const dad=await api('/register',{login:'calendar-dad',name:'合成统筹',password:'browser-password-123'});
 const mom=await api('/register',{login:'calendar-mom',name:'合成妈妈',password:'browser-password-123'});
 const {family_id:fid}=await api('/families',{name:'日历验证'},dad);let key=0;
 const command=(action,data,actor=dad)=>api('/action',{family_id:fid,action,data,request_id:'calendar-'+(++key)},actor);
 const invitation=await command('invite.create',{role:'mom'});await api('/join',{code:invitation.invite_code},mom);
 const state=()=>api('/state?family_id='+fid,undefined,dad),day=(await state()).calendar_date;
 await command('task.create',{title:'日历整理，纸笔；备份\\路径',due_date:day,note:'不导出的备注'});
 const done=await command('task.create',{title:'不导出已完成事项',due_date:day});const t=(await state()).tasks.find(t=>t.id===done.id);await command('task.update',{id:t.id,version:t.version,status:'done'});
 await command('pregnancy.create',{consent:true},mom);await command('appointment.create',{title:'合成健康预约',date:day,note:'机构私密要求'},mom);await command('consent.update',{scope:'health_read',grantee:dad.user.id,enabled:true},mom);
 const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.BABA_TEST_CHROMIUM_NO_SANDBOX==='1'?['--no-sandbox']:[]});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#auth-form [name=login]').fill('calendar-dad');await page.locator('#auth-form [name=password]').fill('browser-password-123');await page.locator('#auth-form button').click();await page.locator('.journey').waitFor();await page.locator('#navigation [data-page=tasks]').click();
  const open=async()=>{await page.locator('[data-action=calendar-export]').click();await page.locator('#calendar-form [name=start]').fill(day);await page.locator('#calendar-form [name=end]').fill(day)};
  const save=async()=>{const promise=page.waitForEvent('download');await page.locator('#calendar-form button').click();const file=await promise;assert.match(file.suggestedFilename(),/^baba-calendar-.*\.ics$/);const stream=await file.createReadStream();let content='';for await(const part of stream)content+=part.toString('utf8');await page.locator('#calendar-form').waitFor({state:'detached'});return content};
  await open();await page.locator('#calendar-form button').click();assert.equal(await page.locator('#calendar-form').count(),1);await page.locator('#calendar-form [name=confirmed]').check();let content=await save();
  assert.equal(content.split('BEGIN:VEVENT').length-1,2);assert.match(content,/DTSTART;VALUE=DATE:/);assert.equal(content.includes('不导出的备注'),false);assert.equal(content.includes('不导出已完成事项'),false);assert.equal(content.includes('机构私密要求'),false);assert.equal(content.includes('合成健康预约'),true);assert.equal(content.includes('VALARM'),false);
  // Revoke after opening the export form: current authorization must win over the old page.
  await open();await page.locator('#calendar-form [name=confirmed]').check();await command('consent.update',{scope:'health_read',grantee:dad.user.id,enabled:false},mom);content=await save();assert.equal(content.includes('合成健康预约'),false);assert.equal(content.split('BEGIN:VEVENT').length-1,1);
  await open();await page.locator('#calendar-form [name=start]').fill('2026-10-28');await page.locator('#calendar-form [name=end]').fill('2026-10-27');await page.locator('#calendar-form [name=confirmed]').check();await page.locator('#calendar-form button').click();await page.locator('#toast').filter({hasText:'起止顺序'}).waitFor();assert.equal(await page.locator('#calendar-form').count(),1);
  assert.deepEqual(errors,[]);console.log('PASS: ICS file download, explicit confirmation, all-day dates, visible-only events, closed-event filtering, authorization recheck and invalid range handling');
 }finally{await browser.close();await ctx.dispose()}
})().catch(e=>{console.error(e);process.exit(1)});
