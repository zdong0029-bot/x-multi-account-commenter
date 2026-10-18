let connection, token='', executing=false, stopped=false;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const status=text=>document.getElementById('status').textContent=text;
async function api(path,body){
  const response=await fetch(connection.url+'/bridge/'+path,{method:'POST',headers:{'Content-Type':'application/json',...(token?{'X-Bridge-Token':token}:{})},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
  const result=await response.json();
  if(!response.ok) {if(response.status===403)stopped=true;throw Error(result.error||'本机连接失败');}
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
async function execute(job){
  const {kind,args,deadline}=job;let operationTab=null;
  try{
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
async function tick(){
  if(stopped)return;
  try{
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
    }else if(!executing){status('已连接。回到助手面板点击「检查连接」。');}
  }catch(error){status('连接中断：'+error.message+'\n如果助手重启过，请在 X 标签页点击扩展图标，使用新的配对码连接。');}
  if(!stopped)setTimeout(tick,1600);
}
(async()=>{
  try{
    ({connection}=await chrome.storage.session.get('connection'));
    if(!connection)throw Error('请先在已登录的 X 标签页点击扩展图标进行配对。');
    const url=new URL(connection.url);if(url.protocol!=='http:'||url.hostname!=='127.0.0.1')throw Error('本机助手地址不正确');
    document.getElementById('open').onclick=()=>chrome.tabs.create({url:connection.url});
    document.getElementById('details').textContent='本机助手：'+connection.url;
    if(!connection.client_id){const saved=await chrome.storage.local.get('client_id');connection.client_id=saved.client_id||crypto.randomUUID();await chrome.storage.local.set({client_id:connection.client_id});}
    const result=await api('pair',{code:connection.code,extension_id:chrome.runtime.id,client_id:connection.client_id,label:connection.label||''});token=result.token;
    connection.code='';await chrome.storage.session.set({connection});
    await tick();
  }catch(error){status('未连接：'+error.message);}
})();
