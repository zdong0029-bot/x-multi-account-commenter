let connection, token='', executing=false, stopped=false;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const status=text=>document.getElementById('status').textContent=text;
async function api(path,body){
  const response=await fetch(connection.url+'/bridge/'+path,{method:'POST',headers:{'Content-Type':'application/json',...(token?{'X-Bridge-Token':token}:{})},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
  const result=await response.json();
  if(!response.ok){const error=Error(result.error||'本机连接失败');error.status=response.status;throw error;}
  return result;
}
function checkedURL(value){const url=new URL(value);if(url.origin!=='https://x.com')throw Error('只允许操作 x.com 页面');return url.href;}
async function waitTab(tabId,deadline){
  const end=Math.min(deadline,Date.now()+30000);
  while(Date.now()<end){
    const tab=await chrome.tabs.get(tabId);
    const destination=tab.pendingUrl||tab.url||'';
    if(destination&&destination!=='about:blank')checkedURL(destination);
    if(tab.status==='complete'&&tab.url?.startsWith('https://x.com/'))return;
    await sleep(250);
  }
  throw Error('X 页面加载超时，请检查该标签页。');
}
async function content(tabId,command,args,deadline){
  if(Date.now()>deadline)throw Error('操作已过期，请回面板重新操作。');
  const tab=await chrome.tabs.get(tabId);checkedURL(tab.url||'');
  await chrome.scripting.executeScript({target:{tabId},files:['content.js']});
  return await chrome.tabs.sendMessage(tabId,{channel:'x-comment-assistant',command,args,deadline});
}
async function ensureXTab(){
  try{const tab=await chrome.tabs.get(connection.tabId);if(tab?.url?.startsWith('https://x.com/'))return;}catch(error){}
  const tabs=await chrome.tabs.query({url:'https://x.com/*'});
  const tab=tabs[0]||await chrome.tabs.create({url:'https://x.com/home',active:false});
  connection.tabId=tab.id;await chrome.storage.session.set({connection});
  if(tab.status!=='complete')await sleep(4000);
}
async function execute(job){
  const {kind,args,deadline}=job;let operationTab=null;
  try{
    if(kind==='info'||kind==='scan')await ensureXTab();
    if(kind==='info')return await content(connection.tabId,'info',{},deadline);
    if(kind==='scan'){
      const destination=args.source==='search'?'https://x.com/search?q='+encodeURIComponent(args.query)+'&src=typed_query&f=live':'https://x.com/home';
      const tab=await chrome.tabs.get(connection.tabId);
      if(tab.url!==destination){await chrome.tabs.update(connection.tabId,{url:destination});await waitTab(connection.tabId,deadline);}
      return await content(connection.tabId,'scan',args,deadline);
    }
    if(kind==='read'||kind==='publish'||kind==='profile'){
      if(kind==='profile'&&!/^[A-Za-z0-9_]{1,15}$/.test(args.handle||''))throw Error('博主账号不正确');
      const url=checkedURL(kind==='profile'?'https://x.com/'+args.handle:args.url);
      if(kind!=='profile'&&!/^\/[A-Za-z0-9_]{1,15}\/status\/\d+$/.test(new URL(url).pathname))throw Error('帖子链接不正确');
      operationTab=await chrome.tabs.create({url:'about:blank',active:kind==='publish'});
      // Let the browser finish creating the tab before navigating and injecting.
      await sleep(250);
      await chrome.tabs.update(operationTab.id,{url});
      await waitTab(operationTab.id,deadline);
      const result=await content(operationTab.id,kind,args,deadline);
      if(kind==='read'||kind==='profile'||result.sent){await chrome.tabs.remove(operationTab.id);}
      return result;
    }
    throw Error('不支持的操作');
  }catch(error){return {ok:false,error:error.message,clicked:kind==='publish'?undefined:false};}
}
// 首次用配对码；之后用设备密钥自动重连（助手重启、浏览器重启都不用再输配对码）。
async function connect(){
  const {saved}=await chrome.storage.local.get('saved');
  let result;
  if(connection.code){
    result=await api('pair',{code:connection.code,extension_id:chrome.runtime.id,client_id:connection.client_id,label:connection.label||'',profile:connection.profile||''});
    await chrome.storage.local.set({saved:{url:connection.url,client_id:result.client_id,device_secret:result.device_secret,label:connection.label||''}});
    connection.code='';await chrome.storage.session.set({connection});
  }else if(saved?.device_secret&&saved.url===connection.url){
    result=await api('resume',{client_id:saved.client_id,device_secret:saved.device_secret,extension_id:chrome.runtime.id,label:connection.label||saved.label||''});
  }else throw Object.assign(Error('还没配对过，请在 X 标签页点击扩展图标输入面板配对码。'),{fatal:true});
  token=result.token;
}
async function tick(){
  if(stopped)return;
  try{
    if(!token){status('正在连接本机助手…');await connect();}
    const result=await api('poll',{});
    if(result.command){
      executing=true;status('正在处理：'+({info:'检查登录',scan:'浏览帖子',read:'读取原帖',profile:'核对博主主页标签和蓝 V',publish:'发布已确认或通过自动规则的评论'}[result.command.kind]||result.command.kind));
      // Heartbeat only; the server never redelivers a command that has been fetched.
      const heartbeat=setInterval(()=>api('poll',{}).catch(()=>{}),4000);
      let outcome;try{outcome=await execute(result.command);}finally{clearInterval(heartbeat);executing=false;}
      // Posting a result is safe to retry; executing a publication is never retried.
      let delivered=false;
      for(let attempt=0;attempt<3&&!delivered;attempt++){try{await api('result',{id:result.command.id,result:outcome});delivered=true;}catch(error){if(attempt===2)throw error;await sleep(800);}}
      status(outcome.ok?'已连接。回到助手面板继续操作。':'操作未完成：'+outcome.error);
    }else if(!executing){status('已连接（'+(connection.label||'未备注')+'）。可以回到助手面板操作。');}
  }catch(error){
    if(error.fatal||(error.status===400&&/凭据无效/.test(error.message))){stopped=true;status('未连接：'+error.message+'\n请在 X 标签页点击扩展图标，用面板配对码重新连接一次。');return;}
    if(error.status===403)token='';  // 助手重启过，令牌失效 → 下一轮自动重连
    status('暂时连不上助手，正在自动重试…\n（'+error.message+'）\n请确认助手程序在运行。');
  }
  if(!stopped)setTimeout(tick,token?1600:5000);
}
(async()=>{
  try{
    ({connection}=await chrome.storage.session.get('connection'));
    if(!connection){
      const {saved}=await chrome.storage.local.get('saved');
      if(!saved?.device_secret)throw Error('请先在已登录的 X 标签页点击扩展图标进行配对。');
      connection={url:saved.url,client_id:saved.client_id,label:saved.label||'',tabId:-1};
    }
    const url=new URL(connection.url);if(url.protocol!=='http:'||url.hostname!=='127.0.0.1')throw Error('本机助手地址不正确');
    document.getElementById('open').onclick=()=>chrome.tabs.create({url:connection.url});
    document.getElementById('details').textContent='本机助手：'+connection.url;
    if(!connection.client_id){const stored=await chrome.storage.local.get('client_id');connection.client_id=stored.client_id||crypto.randomUUID();await chrome.storage.local.set({client_id:connection.client_id});}
    await tick();
  }catch(error){status('未连接：'+error.message);}
})();
