// Loaded only from the trusted application service origin; browser never simulates native success.
async function startNativePurchase(sku,provider){
 if(!window.plus)throw Error('请在已签名的原生 App 内使用此支付方式');
 if(plus.os.name==='iOS')throw Error('iOS 数字商品支付路径尚未完成商店核验，当前不开放外部支付');
 const channels=await new Promise((resolve,reject)=>plus.payment.getChannels(resolve,reject));const channel=channels.find(c=>c.id===(provider==='wechat'?'wxpay':'alipay'));
 if(!channel)throw Error('安装包未包含该支付模块，请重新构建');
 const result=await api('/orders',{family_id:fid,sku,provider,platform:'app',request_id:crypto.randomUUID()});
 const info=provider==='alipay'?result.payment.orderInfo:JSON.stringify(result.payment);
 try{await new Promise((resolve,reject)=>plus.payment.request(channel,info,resolve,()=>reject(Error('支付未完成，请以订单查询结果为准'))))}finally{await api('/orders/query',{order_id:result.order_id});await refresh()}
}
