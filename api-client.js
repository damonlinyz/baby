let csrf='';
const apiRecovery=RequestRecovery.create({
 scope:()=>location.origin+'|'+csrf,
 onChange:()=>document.dispatchEvent(new CustomEvent('recovery-change')),
 onUnauthorized:()=>{csrf='';apiRecovery.reset();document.dispatchEvent(new CustomEvent('signed-out'))},
 async send(path,data){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==='/ai'?90000:15000);
  try{
   const response=await fetch('/api'+path,{method:data===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
   let body;try{body=await response.json()}catch{body=null}
   return {status:response.status,body};
  }finally{clearTimeout(timer)}
 }
});
async function api(path,data){const result=await apiRecovery.request(path,data);if(result.csrf&&csrf!==result.csrf){csrf=result.csrf;apiRecovery.reset()}if(['/logout','/account/delete','/account/password','/account/delete-wechat','/account/recover'].includes(path)){csrf='';apiRecovery.reset()}return result}
function apiCommand(data,confirm){return apiRecovery.command(data,confirm)}
