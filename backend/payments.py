"""Provider calls are isolated from SQLite transactions. Only verified results change entitlements."""
import os,json,subprocess,hashlib,secrets
from decimal import Decimal,InvalidOperation
from pathlib import Path
import domain as d
NODE=os.environ.get('NODE_BIN','node')
PRODUCTS={'core':{'price_fen':12800,'description':'爸爸在呢核心产品买断','policy_version':'1.0'},'ai30':{'price_fen':2500,'description':'爸爸在呢 AI 30 天包','policy_version':'draft-1.0'}}
def money_fen(value):
 try:
  amount=Decimal(str(value))*100
  if not amount.is_finite() or amount<0 or amount!=amount.to_integral_value():raise ValueError()
  return int(amount)
 except (InvalidOperation,ValueError,TypeError):raise d.Problem(400,'渠道金额格式不正确')
def configured(provider):
 common=['PAY_NOTIFY_BASE']
 names={'wechat':['WX_MCH_ID','WX_CERT_SERIAL','WX_PRIVATE_KEY_PATH','WX_PLATFORM_KEY_ID','WX_PLATFORM_PUBLIC_KEY_PATH','WX_API_V3_KEY'],'alipay':['ALIPAY_APP_ID','ALIPAY_SELLER_ID','ALIPAY_PRIVATE_KEY_PATH','ALIPAY_PUBLIC_KEY_PATH']}.get(provider)
 return bool(names) and all(os.environ.get(n) for n in names+common)
def adapter(data):
 try:p=subprocess.run([NODE,str(Path(__file__).with_name('payment-crypto.mjs'))],input=d.dumps(data),text=True,capture_output=True,timeout=30)
 except (OSError,subprocess.TimeoutExpired):raise d.Problem(503,'支付渠道响应不确定，请查询订单或退款状态后重试')
 try:r=json.loads(p.stdout)
 except ValueError:raise d.Problem(503,'支付服务暂时不可用')
 if not r.get('ok'):raise d.Problem(502,r.get('error','支付请求失败'))
 return r['result']
def create(user,data):
 provider=data.get('provider');platform=data.get('platform');sku=data.get('sku');fid=data.get('family_id');key=d.text(data.get('request_id'),80)
 if platform not in ('weapp','app') or (platform=='weapp' and provider!='wechat'):raise d.Problem(400,'小程序只支持微信支付，App 支持微信支付或支付宝')
 if not configured(provider) or os.environ.get('PAYMENTS_ENABLED')!='1':raise d.Problem(503,'支付未完成配置和验收，当前不会发起扣款')
 if sku not in PRODUCTS:raise d.Problem(400,'商品不存在')
 if sku=='ai30' and os.environ.get('AI_SALES_APPROVED')!='1':raise d.Problem(409,'AI 套餐价格与成本尚未完成开售验收')
 with d.transaction() as c:
  d.member(c,fid,user,('dad',));product=PRODUCTS[sku]
  if sku=='core' and any(e['benefit']=='core' and e['status']=='active' for e in d.rows(c,'entitlement',fid)):raise d.Problem(409,'该家庭已经拥有核心买断权益')
  if sku=='ai30' and not any(e['benefit']=='core' and e['status']=='active' for e in d.rows(c,'entitlement',fid)):raise d.Problem(409,'需要先拥有核心产品')
  app_id=os.environ.get(('WX_APP_ID' if platform=='weapp' else 'WX_NATIVE_APP_ID') if provider=='wechat' else 'ALIPAY_APP_ID')
  merchant_id=os.environ.get('WX_MCH_ID' if provider=='wechat' else 'ALIPAY_SELLER_ID')
  if not app_id or not merchant_id:raise d.Problem(503,'订单应用与商户绑定尚未配置')
  order=next((o for o in d.rows(c,'order',fid) if o['payer']==user and o.get('request_id')==key),None)
  if order and (order['sku']!=sku or order['provider']!=provider or order['platform']!=platform):raise d.Problem(409,'订单请求与原始内容不一致')
  if not order:
   if sku=='core' and any(o['sku']=='core' and o['status'] in ('created','pending','paid') and o['expires_at']>d.now() for o in d.rows(c,'order',fid)):raise d.Problem(409,'已有待支付买断订单，请先查询或等待过期')
   order={'id':'B'+secrets.token_hex(14),'request_id':key,'payer':user,'family_id':fid,'sku':sku,'amount_fen':product['price_fen'],'currency':'CNY','description':product['description'],'policy_version':product['policy_version'],'provider':provider,'platform':platform,'app_id':app_id,'merchant_id':merchant_id,'status':'created','created_at':d.now(),'expires_at':d.now()+1800};d.put(c,'order',order,fid)
  if order['status'] not in ('created','pending') or order['expires_at']<d.now():raise d.Problem(409,'订单不可继续支付，请查询最新状态')
  identity=next((i for i in d.rows(c,'wechat_identity') if i['user_id']==user and i['appid']==os.environ.get('WX_APP_ID')),None)
 params=adapter({'provider':provider,'platform':platform,'operation':'prepay','order':order,'openid':identity['openid'] if identity else None})
 with d.transaction() as c:
  current=d.get(c,'order',order['id'])
  if current['status']=='created':current['status']='pending';d.put(c,'order',current,fid)
 return {'order_id':order['id'],'provider':provider,'platform':platform,'payment':params}
def settle(c,provider,tx,body_hash):
 if not tx.get('event_id') or not tx.get('transaction_id'):raise d.Problem(400,'支付事件缺少必要标识')
 row=c.execute('SELECT body_hash FROM payment_events WHERE provider=? AND event_id=?',(provider,tx['event_id'])).fetchone()
 if row:
  if row['body_hash']!=body_hash:raise d.Problem(409,'同一支付事件内容不一致')
  return
 o=d.get(c,'order',tx['order_id']);fid=o['family_id']
 if o['provider']!=provider or type(tx.get('amount_fen')) is not int or o['amount_fen']!=tx.get('amount_fen') or tx.get('currency')!='CNY':raise d.Problem(400,'订单收款渠道或金额不匹配')
 if type(tx.get('success')) is not bool:raise d.Problem(400,'支付状态格式不正确')
 if o.get('app_id') and (tx.get('app_id')!=o['app_id'] or tx.get('merchant_id')!=o['merchant_id']):raise d.Problem(400,'订单应用或收款主体不匹配')
 if tx['success']:
  prior=c.execute('SELECT order_id FROM payment_transactions WHERE provider=? AND transaction_id=?',(provider,tx['transaction_id'])).fetchone()
  if (prior and prior['order_id']!=o['id']) or any(x['id']!=o['id'] and x.get('provider')==provider and x.get('transaction_id')==tx['transaction_id'] for x in d.rows(c,'order')):raise d.Problem(409,'同一渠道交易号不能用于多个订单')
  c.execute('INSERT OR IGNORE INTO payment_transactions VALUES(?,?,?)',(provider,tx['transaction_id'],o['id']))
  if o.get('transaction_id') and o['transaction_id']!=tx['transaction_id']:raise d.Problem(409,'订单交易号不一致')
  if o['status'] not in ('paid','refunded','refund_pending'):
   o.update(status='paid',paid_at=d.now(),transaction_id=tx['transaction_id'])
   core=next((e for e in sorted(d.rows(c,'entitlement',fid),key=lambda e:(e.get('starts_at') or 0,e['id'])) if e.get('benefit')=='core' and e.get('status')=='active' and e.get('source_order')!=o['id']),None) if o['sku']=='core' else None
   if core:
    # The transaction is real, but this family already owns the one-time product.
    # Keep its funds visible and queue review; never contact a refund provider here.
    refund_id='Rdup'+hashlib.sha256(o['id'].encode()).hexdigest()[:28]
    o.update(fulfillment_status='duplicate_core',duplicate_of_order_id=core['source_order'],refund_id=refund_id)
    d.put(c,'order',o,fid)
    refund={'id':refund_id,'order_id':o['id'],'requested_by':o['payer'],'amount_fen':o['amount_fen'],'reason':'重复买断收款：家庭已拥有核心产品，请核验后原路退款','source':'system_duplicate_core','status':'requested','created_at':d.now()}
    d.put(c,'refund',refund,fid)
    d.audit(c,fid,None,'payment.duplicate_core',{'order_id':o['id'],'original_order_id':core['source_order'],'refund_id':refund_id})
    d.notify(c,fid,o['payer'],'检测到重复买断收款，已创建售后处理单；尚未退款',o['id'])
   else:
    o['fulfillment_status']='granted';d.put(c,'order',o,fid)
    grant={'id':'grant-'+o['id'],'source_order':o['id'],'owner':o['payer'],'bound_family':fid,'benefit':'core' if o['sku']=='core' else 'ai','status':'active','starts_at':d.now(),'ends_at':None,'quota':300,'policy_version':o['policy_version']}
    if o['sku']=='ai30':
     previous=max([e.get('ends_at') or 0 for e in d.rows(c,'entitlement',fid) if e['benefit']=='ai' and e['status']=='active']+[d.now()]);grant['starts_at']=previous;grant['ends_at']=previous+2592000
    d.put(c,'entitlement',grant,fid)
    if o['sku']=='core':d.put(c,'entitlement',{'id':'gift-'+o['id'],'source_order':o['id'],'owner':o['payer'],'bound_family':fid,'benefit':'ai_gift','status':'unactivated','starts_at':None,'ends_at':None,'quota':300,'policy_version':o['policy_version']},fid)
   d.audit(c,fid,o['payer'],'payment.paid',{'order_id':o['id']})
 c.execute('INSERT INTO payment_events VALUES(?,?,?,?)',(provider,tx['event_id'],body_hash,d.now()))
def callback(provider,headers,raw):
 if not configured(provider):raise d.Problem(503,'支付渠道尚未配置')
 tx=adapter({'provider':provider,'operation':'verify','platform':'weapp' if provider=='wechat' else 'app','headers':headers,'raw':raw})
 with d.transaction() as c:settle(c,provider,tx,hashlib.sha256(raw.encode()).hexdigest())
 return tx

def query(user,order_id):
 with d.transaction() as c:
  o=d.get(c,'order',order_id)
  if o['payer']!=user:raise d.Problem(403,'无权查询此订单')
  if o['status'] not in ('pending','created'):return o
 r=adapter({'provider':o['provider'],'platform':o['platform'],'operation':'query','order':o})
 if o['provider']=='wechat':
  appid=os.environ.get('WX_APP_ID' if o.get('platform','weapp')=='weapp' else 'WX_NATIVE_APP_ID')
  if not appid or r.get('appid')!=appid or r.get('mchid')!=os.environ.get('WX_MCH_ID') or r.get('out_trade_no')!=o['id']:raise d.Problem(400,'查单主体不匹配')
  tx={'event_id':'query-'+str(r.get('transaction_id')),'transaction_id':r.get('transaction_id'),'order_id':o['id'],'app_id':r.get('appid'),'merchant_id':r.get('mchid'),'amount_fen':r.get('amount',{}).get('total'),'currency':r.get('amount',{}).get('currency'),'success':r.get('trade_state')=='SUCCESS'}
 else:
  if r.get('out_trade_no')!=o['id'] or (r.get('seller_id') is not None and r.get('seller_id')!=os.environ.get('ALIPAY_SELLER_ID')):raise d.Problem(400,'查单订单或收款主体不匹配')
  tx={'event_id':'query-'+str(r.get('trade_no')),'transaction_id':r.get('trade_no'),'order_id':o['id'],'app_id':o.get('app_id'),'merchant_id':os.environ.get('ALIPAY_SELLER_ID'),'amount_fen':money_fen(r.get('total_amount')),'currency':'CNY','success':r.get('trade_status') in ('TRADE_SUCCESS','TRADE_FINISHED')}
 if tx['success']:
  with d.transaction() as c:settle(c,o['provider'],tx,hashlib.sha256(d.dumps(tx).encode()).hexdigest())
 with d.transaction() as c:return d.get(c,'order',o['id'])

def request_refund(user,order_id,reason):
 with d.transaction() as c:
  o=d.get(c,'order',order_id)
  if o['payer']!=user:raise d.Problem(403,'只能为本人的订单申请售后')
  if o['status']!='paid':raise d.Problem(409,'仅已支付订单可申请退款')
  for r in d.rows(c,'refund',o['family_id']):
   if r['order_id']==order_id and r['status'] not in ('rejected','failed'):
    if r.get('source')=='system_duplicate_core':return r
    raise d.Problem(409,'已经有退款正在处理中')
  refund={'id':'R'+secrets.token_hex(14),'order_id':order_id,'requested_by':user,'amount_fen':o['amount_fen'],'reason':d.text(reason,500),'status':'requested','created_at':d.now()};d.put(c,'refund',refund,o['family_id']);d.audit(c,o['family_id'],user,'refund.requested',{'refund_id':refund['id']});return refund

def approve_refund(operator,refund_id):
 if operator!=os.environ.get('ADMIN_USER_ID'):raise d.Problem(403,'只有售后管理员可以执行退款')
 with d.transaction() as c:
  refund=d.get(c,'refund',refund_id);o=d.get(c,'order',refund['order_id'])
  if refund['status']=='succeeded':return {'refund_id':refund_id,'status':'succeeded'}
  if refund['status'] not in ('requested','processing'):raise d.Problem(409,'退款状态不允许提交')
  refund['status']='processing';d.put(c,'refund',refund,o['family_id']);o['status']='refund_pending';d.put(c,'order',o,o['family_id'])
 # Unknown network outcome stays processing; retry uses the identical provider refund identifier.
 r=adapter({'provider':o['provider'],'platform':o['platform'],'operation':'refund','order':o,'refund':refund})
 success=refund_success(o,refund,r)
 if success:
  with d.transaction() as c:finish_refund(c,refund['id'])
 return {'refund_id':refund_id,'status':'succeeded' if success else 'processing'}

def finish_refund(c,id):
 r=d.get(c,'refund',id);o=d.get(c,'order',r['order_id'])
 if r['status']=='succeeded':return
 if r['amount_fen']!=o['amount_fen'] or o['status'] not in ('paid','refund_pending'):raise d.Problem(409,'退款金额或订单状态不匹配')
 r['status']='succeeded';r['completed_at']=d.now();d.put(c,'refund',r,o['family_id']);o['status']='refunded';d.put(c,'order',o,o['family_id'])
 for e in d.rows(c,'entitlement',o['family_id']):
  if e.get('source_order')==o['id']:e['status']='revoked';e['revoked_at']=d.now();d.put(c,'entitlement',e,o['family_id'])
 d.audit(c,o['family_id'],r['requested_by'],'refund.completed',{'refund_id':r['id']})

def query_refund(operator,id):
 if operator!=os.environ.get('ADMIN_USER_ID'):raise d.Problem(403,'需要售后管理员权限')
 with d.transaction() as c:r=d.get(c,'refund',id);o=d.get(c,'order',r['order_id'])
 if r['status']=='succeeded':return {'refund_id':id,'status':'succeeded'}
 response=adapter({'provider':o['provider'],'platform':o['platform'],'operation':'query_refund','order':o,'refund':r})
 success=refund_success(o,r,response,query=True)
 if success:
  with d.transaction() as c:finish_refund(c,id)
 return {'refund_id':id,'status':'succeeded' if success else 'processing'}

def refund_success(order,refund,response,query=False):
 if order['provider']=='wechat':
  if response.get('out_refund_no')!=refund['id'] or response.get('out_trade_no')!=order['id']:raise d.Problem(400,'退款响应编号不匹配')
  amount=response.get('amount',{})
  if type(amount.get('refund')) is not int or type(amount.get('total')) is not int or amount.get('refund')!=refund['amount_fen'] or amount.get('total')!=order['amount_fen'] or amount.get('currency')!='CNY':raise d.Problem(400,'退款响应金额不匹配')
  return response.get('status')=='SUCCESS'
 if response.get('out_trade_no')!=order['id'] or response.get('trade_no')!=order.get('transaction_id'):raise d.Problem(400,'退款响应订单不匹配')
 if query and response.get('out_request_no')!=refund['id']:raise d.Problem(400,'退款查询编号不匹配')
 amount=response.get('refund_amount') if query else response.get('refund_fee')
 if money_fen(amount)!=refund['amount_fen']:raise d.Problem(400,'退款响应金额不匹配')
 return response.get('refund_status')=='REFUND_SUCCESS' if query else response.get('code')=='10000'
