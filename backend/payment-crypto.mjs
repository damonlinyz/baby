// Provider protocol adapter. Secrets are read from environment and key files only.
import {readFileSync} from 'node:fs';
import {sign,verify,randomBytes,createDecipheriv} from 'node:crypto';
const env=n=>{if(!process.env[n])throw Error('支付缺少配置：'+n);return process.env[n]};
const pem=n=>readFileSync(env(n),'utf8');
const rsa=(s,key)=>sign('RSA-SHA256',Buffer.from(s),key).toString('base64');
const stamp=()=>String(Math.floor(Date.now()/1000));
const canonical=p=>Object.keys(p).filter(k=>k!=='sign'&&k!=='sign_type'&&p[k]!==''&&p[k]!=null).sort().map(k=>`${k}=${p[k]}`).join('&');
function wxVerify(headers,body){const ts=headers['wechatpay-timestamp'],nonce=headers['wechatpay-nonce'],signature=headers['wechatpay-signature'];if(!ts||Math.abs(Number(ts)-Date.now()/1000)>300)throw Error('支付签名时间无效');if(headers['wechatpay-serial']!==env('WX_PLATFORM_KEY_ID'))throw Error('支付公钥标识不匹配');if(!verify('RSA-SHA256',Buffer.from(`${ts}\n${nonce}\n${body}\n`),pem('WX_PLATFORM_PUBLIC_KEY_PATH'),Buffer.from(signature||'','base64')))throw Error('微信支付验签失败')}
async function wxRequest(method,path,data){const raw=data?JSON.stringify(data):'',nonce=randomBytes(16).toString('hex'),ts=stamp(),sig=rsa(`${method}\n${path}\n${ts}\n${nonce}\n${raw}\n`,pem('WX_PRIVATE_KEY_PATH'));const auth=`WECHATPAY2-SHA256-RSA2048 mchid="${env('WX_MCH_ID')}",nonce_str="${nonce}",timestamp="${ts}",serial_no="${env('WX_CERT_SERIAL')}",signature="${sig}"`;const r=await fetch('https://api.mch.weixin.qq.com'+path,{method,body:data?raw:undefined,headers:{Authorization:auth,'Content-Type':'application/json',Accept:'application/json'},signal:AbortSignal.timeout(20000)});const b=await r.text();wxVerify(Object.fromEntries(r.headers),b);if(!r.ok)throw Error('微信支付接口拒绝了请求');return JSON.parse(b)}
function alipayParams(method,biz){const p={app_id:env('ALIPAY_APP_ID'),method,format:'JSON',charset:'utf-8',sign_type:'RSA2',timestamp:new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}),version:'1.0',biz_content:JSON.stringify(biz)};return p}
function alipaySign(p){const s=Object.keys(p).filter(k=>k!=='sign'&&p[k]!==''&&p[k]!=null).sort().map(k=>`${k}=${p[k]}`).join('&');return rsa(s,pem('ALIPAY_PRIVATE_KEY_PATH'))}
function signedObject(raw,name){const needle=JSON.stringify(name);let i=raw.indexOf(needle);if(i<0)throw Error('支付响应结构不正确');i=raw.indexOf(':',i+needle.length)+1;while(/\s/.test(raw[i]||''))i++;const start=i;let depth=0,quoted=false,escaped=false;for(;i<raw.length;i++){const c=raw[i];if(quoted){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false;continue}if(c==='"')quoted=true;else if(c==='{')depth++;else if(c==='}'&&--depth===0)return raw.slice(start,i+1)}throw Error('支付响应不完整')}
async function alipayRequest(method,biz){const p=alipayParams(method,biz);p.sign=alipaySign(p);const r=await fetch('https://openapi.alipay.com/gateway.do',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded;charset=utf-8'},body:new URLSearchParams(p).toString(),signal:AbortSignal.timeout(20000)});const raw=await r.text(),root=JSON.parse(raw),name=method.replaceAll('.','_')+'_response',signed=signedObject(raw,name);if(!root.sign||!verify('RSA-SHA256',Buffer.from(signed),pem('ALIPAY_PUBLIC_KEY_PATH'),Buffer.from(root.sign,'base64')))throw Error('支付宝响应验签失败');const result=root[name];if(!r.ok||result.code!=='10000')throw Error('支付宝拒绝了请求');return result}
async function main(input){const {provider,operation,order}=input;
 if(provider==='wechat'){
  if(operation==='prepay'){
   const appid=env(input.platform==='weapp'?'WX_APP_ID':'WX_NATIVE_APP_ID');
   if(!['weapp','app'].includes(input.platform))throw Error('微信支付平台不支持');
   const kind=input.platform==='weapp'?'jsapi':'app';const body={appid,mchid:env('WX_MCH_ID'),description:order.description,out_trade_no:order.id,notify_url:env('PAY_NOTIFY_BASE')+'/api/payments/wechat/notify',amount:{total:order.amount_fen,currency:'CNY'},time_expire:new Date(order.expires_at*1000).toISOString()};if(kind==='jsapi'){if(!input.openid)throw Error('请先绑定微信身份');body.payer={openid:input.openid}}
   const r=await wxRequest('POST','/v3/pay/transactions/'+kind,body),ts=stamp(),nonce=randomBytes(16).toString('hex');
   if(kind==='jsapi'){const pkg='prepay_id='+r.prepay_id;return {appId:appid,timeStamp:ts,nonceStr:nonce,package:pkg,signType:'RSA',paySign:rsa(`${appid}\n${ts}\n${nonce}\n${pkg}\n`,pem('WX_PRIVATE_KEY_PATH'))}}
   return {appid,partnerid:env('WX_MCH_ID'),prepayid:r.prepay_id,package:'Sign=WXPay',noncestr:nonce,timestamp:ts,sign:rsa(`${appid}\n${ts}\n${nonce}\n${r.prepay_id}\n`,pem('WX_PRIVATE_KEY_PATH'))};
  }
  if(operation==='verify'){
   wxVerify(input.headers,input.raw);const data=JSON.parse(input.raw),resource=data.resource;if(resource.algorithm!=='AEAD_AES_256_GCM')throw Error('不支持的支付加密算法');const cipher=Buffer.from(resource.ciphertext,'base64'),key=Buffer.from(env('WX_API_V3_KEY'));if(key.length!==32)throw Error('API v3 密钥长度无效');const dec=createDecipheriv('aes-256-gcm',key,Buffer.from(resource.nonce));dec.setAAD(Buffer.from(resource.associated_data||''));dec.setAuthTag(cipher.subarray(-16));const tx=JSON.parse(Buffer.concat([dec.update(cipher.subarray(0,-16)),dec.final()]).toString());if(tx.mchid!==env('WX_MCH_ID')||![process.env.WX_APP_ID,process.env.WX_NATIVE_APP_ID].includes(tx.appid))throw Error('收款主体不匹配');return {app_id:tx.appid,merchant_id:tx.mchid,event_id:data.id,order_id:tx.out_trade_no,transaction_id:tx.transaction_id,amount_fen:tx.amount?.total,currency:tx.amount?.currency,success:tx.trade_state==='SUCCESS'};
  }
  if(operation==='query')return await wxRequest('GET','/v3/pay/transactions/out-trade-no/'+encodeURIComponent(order.id)+'?mchid='+encodeURIComponent(env('WX_MCH_ID')));
  if(operation==='query_refund')return await wxRequest('GET','/v3/refund/domestic/refunds/'+encodeURIComponent(input.refund.id));
  if(operation==='refund')return await wxRequest('POST','/v3/refund/domestic/refunds',{out_trade_no:order.id,out_refund_no:input.refund.id,reason:'用户售后退款',amount:{refund:input.refund.amount_fen,total:order.amount_fen,currency:'CNY'}});
 }
 if(provider==='alipay'){
  if(operation==='prepay'){
   if(input.platform!=='app')throw Error('支付宝仅在 App 开放');const p=alipayParams('alipay.trade.app.pay',{out_trade_no:order.id,total_amount:(order.amount_fen/100).toFixed(2),subject:order.description,product_code:'QUICK_MSECURITY_PAY',timeout_express:'30m'});p.notify_url=env('PAY_NOTIFY_BASE')+'/api/payments/alipay/notify';p.sign=alipaySign(p);return {orderInfo:new URLSearchParams(p).toString()};
  }
  if(operation==='query')return await alipayRequest('alipay.trade.query',{out_trade_no:order.id});
  if(operation==='refund')return await alipayRequest('alipay.trade.refund',{out_trade_no:order.id,out_request_no:input.refund.id,refund_amount:(input.refund.amount_fen/100).toFixed(2)});
  if(operation==='query_refund')return await alipayRequest('alipay.trade.fastpay.refund.query',{out_trade_no:order.id,out_request_no:input.refund.id});
  if(operation==='verify'){
   const p=Object.fromEntries(new URLSearchParams(input.raw));if(p.sign_type!=='RSA2')throw Error('不支持的签名算法');if(p.app_id!==env('ALIPAY_APP_ID')||p.seller_id!==env('ALIPAY_SELLER_ID'))throw Error('支付宝收款主体不匹配');if(!verify('RSA-SHA256',Buffer.from(canonical(p)),pem('ALIPAY_PUBLIC_KEY_PATH'),Buffer.from(p.sign||'','base64')))throw Error('支付宝验签失败');if(!/^\d+(\.\d{1,2})?$/.test(p.total_amount))throw Error('支付金额无效');return {app_id:p.app_id,merchant_id:p.seller_id,event_id:p.notify_id,order_id:p.out_trade_no,transaction_id:p.trade_no,amount_fen:Math.round(Number(p.total_amount)*100),currency:'CNY',success:['TRADE_SUCCESS','TRADE_FINISHED'].includes(p.trade_status)};
  }
 }
 throw Error('未实现该渠道操作');
}
let raw='';for await(const chunk of process.stdin)raw+=chunk;
try{process.stdout.write(JSON.stringify({ok:true,result:await main(JSON.parse(raw))}))}catch(e){process.stdout.write(JSON.stringify({ok:false,error:e.message}));process.exitCode=1}
