/* Shared Canvas 2D renderer: deliberately accepts only generic template data. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./qrcode'));else root.BabaPoster=factory(root.qrcode)})(typeof globalThis!=='undefined'?globalThis:this,function(qrcode){
 const width=720,height=1100;
 function draw(ctx,template,url,miniCode){
  // All text is fixed product copy or public template content, never a family snapshot.
  ctx.fillStyle='#faf6ee';ctx.fillRect(0,0,width,height);
  ctx.fillStyle='#e8ecde';ctx.fillRect(28,28,664,1044);
  ctx.fillStyle='#fffdf8';ctx.fillRect(44,44,632,1012);
  const text=(value,x,y,size,color='#4e604b')=>{ctx.font=size+'px sans-serif';ctx.fillStyle=color;ctx.fillText(value,x,y)};
  text('爸爸在呢',80,115,38);text('小小行囊，慢慢准备',80,175,32);text('通用生活整理模板 · 不含家庭资料',80,215,21,'#7d8374');
  let y=266,pack='';
  for(const item of template.items.slice(0,9)){
   if(item.pack!==pack){pack=item.pack;text(pack,80,y,23);y+=38}
   text('□  '+item.name,92,y,24,'#626b5a');y+=41;
  }
  ctx.fillStyle='#f1eee6';ctx.fillRect(74,813,572,197);
  if(miniCode)ctx.drawImage(miniCode,92,829,152,152);
  else{
   const qr=qrcode(0,'M');qr.addData(url);qr.make();const n=qr.getModuleCount(),cell=Math.max(1,Math.floor(152/(n+8))),size=(n+8)*cell,x=92+(152-size)/2,top=829+(152-size)/2;
   ctx.fillStyle='#fff';ctx.fillRect(92,829,152,152);ctx.fillStyle='#25342a';
   for(let row=0;row<n;row++)for(let col=0;col<n;col++)if(qr.isDark(row,col))ctx.fillRect(x+(col+4)*cell,top+(row+4)*cell,cell,cell);
  }
  text(miniCode?'扫码查看小程序模板':'扫码查看网页模板',265,865,23);text('按实际需要与机构要求核对',265,904,20);text('不包含私人清单或加入权限',265,942,20);text('把在乎，变成行动',80,1033,20,'#7d8374');
 }
 return {width,height,draw};
});
