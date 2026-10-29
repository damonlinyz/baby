const app=getApp(),Poster=require('../../lib/poster');
const confirmation=(title,content)=>new Promise(resolve=>wx.showModal({title,content,success:r=>resolve(r.confirm),fail:()=>resolve(false)}));
module.exports={
 onHide(){if(['security','recovery-code','account-recover'].includes(this.data.modal))this.close()},
 onUnload(){this.cleanPoster()},
 taskLibrary(){this.run(async()=>{const r=await app.request('/task-library');this.setData({modal:'task-library',libraryGroups:[...new Set(r.templates.map(t=>t.group))].map(group=>({group,templates:r.templates.filter(t=>t.group===group)})),form:{}})})},
 selectTemplate(e){this.setData({modal:'library-assign',form:{template_id:e.currentTarget.dataset.id,assignee:0,due_date:''}})},
 createFromLibrary(){this.run(async()=>{if(!await confirmation('添加生活任务','本人确认此生活事项适合家庭当前需要，不绑定或推算健康日期。'))return;const f=this.data.form;await this.action('task.from_library',{template_id:f.template_id,assignee:this.data.state.members[f.assignee||0].id,due_date:f.due_date||null,confirmed:true});this.close()})},

 aiHistory(){this.run(async()=>{const r=await app.request('/ai/history?family_id='+encodeURIComponent(this.data.familyId));this.setData({modal:'ai-history',historyItems:r.items,form:{}})})},
 deleteHistory(e){this.run(async()=>{if(!await confirmation('删除问答','删除正文不会返还已使用额度。'))return;await this.action('ai_history.delete',{id:e.currentTarget.dataset.id});const r=await app.request('/ai/history?family_id='+encodeURIComponent(this.data.familyId));this.setData({historyItems:r.items})})},
 enhanceBrief(){this.run(async()=>{if(!await confirmation('一般生活增强解释','只向模型发送重点中的任务和物品数量及公开生活知识，不发送私人事项原文。'))return;const id=this.data.autoBriefing.id;const r=await app.request('/briefing/enhance',{family_id:this.data.familyId,id,confirmed:true});const b=await app.request('/briefing?family_id='+encodeURIComponent(this.data.familyId)+'&id='+encodeURIComponent(id));this.setData({autoBriefing:b});if(r.result.kind!=='ai')wx.showToast({title:'模型未配置，保留静态简报',icon:'none'})})},
 autoEnhance(){this.run(async()=>{const enabled=!this.data.state.ai_preferences.auto_briefing;if(enabled&&!await confirmation('开启每周自动增强','同意仅发送重点中的任务和物品数量及公开生活知识；使用独立简报额度，失败保留静态简报。'))return;await this.action('ai.briefing_preferences',{enabled,confirmed:enabled})})},

 like(e){this.run(async()=>{const item=this.data.state.feedback_feed.find(r=>r.id===e.currentTarget.dataset.id);await this.action('feedback.like',{event_id:item.id,enabled:!item.liked})})},
 comment(e){this.setData({modal:'feedback-comment',form:{event_id:e.currentTarget.dataset.id,text:''}})},
 saveComment(){this.run(async()=>{await this.action('feedback.comment',{event_id:this.data.form.event_id,text:this.data.form.text});this.close()})},
 deleteComment(e){this.run(async()=>{if(await confirmation('删除留言','确认删除这条留言？'))await this.action('feedback.delete',{id:e.currentTarget.dataset.id,event_id:e.currentTarget.dataset.event})})},
 security(){this.run(async()=>{if(app.hasPending())throw Error('请先核对上次家庭提交');const info=await app.request('/account/security');this.setData({modal:'security',securityInfo:info,form:{proof:0}})})},
 async securityProof(){
  if(app.hasPending())throw Error('请先核对上次家庭提交');
  if(this.data.form.proof!==1)return {password:this.data.form.password};
  const scope=app.globalData.baseUrl+'|'+app.globalData.token;
  const r=await new Promise((resolve,reject)=>wx.login({success:resolve,fail:()=>reject(Error('无法重新验证微信身份'))}));
  if(scope!==app.globalData.baseUrl+'|'+app.globalData.token)throw Object.assign(Error('账号已切换'),{stale:true});
  return {wechat_code:r.code};
 },
 changePassword(){this.run(async()=>{const f={...this.data.form};if(f.new_password!==f.repeat_password)throw Error('两次新密码不一致');if(!await confirmation('修改密码并退出','所有设备会退出，旧恢复码作废。请重新登录并保存新恢复码。'))return;const proof=await this.securityProof();await app.request('/account/password',{...proof,new_password:f.new_password});wx.showToast({title:'密码已更新，请重新登录',icon:'none'})})},
 recoveryCode(){this.run(async()=>{if(!await confirmation('轮换恢复码','新码仅显示一次，旧恢复码立即作废。请离线保管。'))return;const proof=await this.securityProof();const r=await app.request('/account/recovery-code',proof);this.setData({modal:'recovery-code',form:{code:r.code},securityInfo:null})})},
 recoverAccount(){this.setData({modal:'account-recover',form:{}})},
 submitRecovery(){this.run(async()=>{const f=this.data.form;if(f.new_password!==f.repeat_password)throw Error('两次新密码不一致');await app.request('/account/recover',{login:f.login,code:f.code,new_password:f.new_password});wx.showToast({title:'密码已更新，请重新登录',icon:'none'})})},
 deleteAccount(){this.run(async()=>{if(this.data.user.families.some(f=>f.role==='dad'))throw Error('请先转交所有家庭统筹权限');if(!await confirmation('确认注销账号','本人健康资料及关联记录会删除，订单账本保留。不可撤销，请先导出所需资料。'))return;const proof=await this.securityProof();if(proof.wechat_code)await app.request('/account/delete-wechat',{...proof,confirmed:true});else await app.request('/account/delete',proof);wx.showToast({title:'账号已注销',icon:'none'})})},
 openPoster(){this.cleanPoster();this.setData({modal:'poster',form:{kind:0},posterFile:''})},
 cleanPoster(){if(this.data.posterFile&&wx.getFileSystemManager)wx.getFileSystemManager().unlink({filePath:this.data.posterFile,fail:()=>{}});this.setData({posterFile:''})},
 makePoster(){this.run(async()=>{
  this.cleanPoster();const origin=app.globalData.baseUrl,token=app.globalData.token,kind=this.data.form.kind;
  const current=()=>origin===app.globalData.baseUrl&&token===app.globalData.token&&this.data.modal==='poster';
  const template=await app.request('/public/template');let code=null;
  if(kind===1)code=(await app.request('/poster/code')).data_url;
  if(!current())return;
  const node=await new Promise((resolve,reject)=>wx.createSelectorQuery().in(this).select('#poster-canvas').fields({node:true,size:true}).exec(r=>r[0]?.node?resolve(r[0].node):reject(Error('此微信版本不支持海报画布'))));
  node.width=Poster.width;node.height=Poster.height;let image=null;
  if(code){image=node.createImage();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('无法读取小程序码'));image.src=code})}
  if(!current())return;Poster.draw(node.getContext('2d'),template,origin+'/#template',image);
  const r=await new Promise((resolve,reject)=>wx.canvasToTempFilePath({canvas:node,fileType:'png',success:resolve,fail:()=>reject(Error('生成海报失败，请重试'))},this));
  if(!current()){wx.getFileSystemManager().unlink({filePath:r.tempFilePath,fail:()=>{}});return}this.setData({posterFile:r.tempFilePath});
 })},
 savePoster(){this.run(async()=>{if(!this.data.posterFile)throw Error('请先生成海报');await new Promise((resolve,reject)=>wx.saveImageToPhotosAlbum({filePath:this.data.posterFile,success:resolve,fail:()=>reject(Error('未保存海报，请检查相册授权后重试'))}));wx.showToast({title:'已保存通用海报',icon:'none'})})}
};
