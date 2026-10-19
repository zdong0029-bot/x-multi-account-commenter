(() => {
  if(globalThis.__xCommentAssistantInstalled)return;
  globalThis.__xCommentAssistantInstalled=true;
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const visible=el=>!!el&&el.getClientRects().length>0;
  async function until(getter,deadline,ms=15000,what=''){const end=Math.min(deadline,Date.now()+ms);while(Date.now()<end){const value=getter();if(value)return value;await sleep(180);}throw Error('未找到页面控件'+(what?'（'+what+'）':'')+'，请确认 X 已加载且当前账号有权操作。');}
  function account(){return (document.querySelector('a[data-testid="AppTabBar_Profile_Link"]')?.getAttribute('href')||'').replace(/^\/+|\/+$/g,'');}
  function loginError(){const text=document.body.innerText;if(/(temporarily limited your login|临时限制你的登录|臨時限制你的登入)/i.test(text))throw Error('X 暂时限制了这次登录。请停止重复尝试，继续使用原本已登录的标签页。');}
  // X risk-control signals. Seen in toasts, alerts, dialogs or the account-access pages.
  const WARN=/(rate limit|over the daily limit|looks like it might be automated|unusual activity|account (is|has been) (temporarily )?(locked|restricted|suspended)|verify (you are|you're) human|complete a challenge|异常活动|看起来像是自动|已被锁定|已被冻结|已被限制|暂时受限|超出.{0,6}限制|完成验证)/i;
  function xWarning(){
    if(/^\/account\/(access|suspended|locked)/.test(location.pathname)||document.querySelector('iframe[src*="arkoselabs"],iframe[src*="captcha"]'))return 'X 要求验证或账号受限（'+location.pathname+'）';
    const text=Array.from(document.querySelectorAll('[data-testid="toast"],[role="alert"],[role="dialog"]')).map(el=>el.innerText).join(' ');
    const m=text.match(WARN);return m?('X 提示：'+text.slice(Math.max(0,m.index-40),m.index+80).replace(/\s+/g,' ')):'';
  }
  function badge(root){
    if(!root)return {badge:'unknown',badge_evidence:'未找到作者标记区域'};
    const icon=root.querySelector('svg[data-testid="icon-verified"]');
    if(!icon)return {badge:'none',badge_evidence:'未显示认证标记'};
    const colors=[icon,...icon.querySelectorAll('path')].flatMap(el=>{const s=getComputedStyle(el);return [s.color,s.fill];});
    const rgb=colors.map(c=>c.match(/^rgba?\((\d+)[, ]+(\d+)[, ]+(\d+)/)).filter(Boolean).map(m=>m.slice(1,4).map(Number));
    if(rgb.some(([r,g,b])=>r>140&&g>100&&b<120))return {badge:'gold',badge_evidence:colors.join(';')};
    if(rgb.some(([r,g,b])=>r<80&&g>90&&b>170&&b>g*1.1))return {badge:'blue',badge_evidence:colors.join(';')};
    if(rgb.some(([r,g,b])=>Math.abs(r-g)<20&&Math.abs(g-b)<20))return {badge:'grey',badge_evidence:colors.join(';')};
    return {badge:'unknown',badge_evidence:colors.join(';')};
  }
  function extract(){return Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(el=>({text:(el.querySelector('[data-testid="tweetText"]')?.innerText||'').slice(0,6000),author:el.querySelector('[data-testid="User-Name"]')?.innerText||'',url:el.querySelector('a:has(time)')?.href||'',...badge(el.querySelector('[data-testid="User-Name"]')),promoted:!!el.querySelector('[data-testid="placementTracking"]')||Array.from(el.querySelectorAll('span')).some(s=>/^(Ad|Promoted|广告|推广)$/.test(s.textContent.trim()))}));}
  async function run(command,args,deadline){
    let clicked=false;
    try{
      {const w=xWarning();if(w)throw Error('X_WARNING '+w);}
      if(!account())loginError();
      await until(()=>account(),deadline,12000);
      if(command==='info')return {ok:true,account:account()};
      if(command==='profile'){
        if(location.pathname.replace(/^\/+|\/+$/g,'').toLowerCase()!==String(args.handle).toLowerCase())throw Error('主页地址与待核对的博主不一致');
        const name=await until(()=>document.querySelector('[data-testid="primaryColumn"] [data-testid="UserName"]')||document.querySelector('[data-testid="UserName"]'),deadline,10000);
        // Wait for profile content to hydrate; never infer a biography from tweets.
        await sleep(700);
        return {ok:true,handle:args.handle,bio:document.querySelector('[data-testid="UserDescription"]')?.innerText||'',category:document.querySelector('[data-testid="UserProfessionalCategory"]')?.innerText||'',...badge(name)};
      }
      if(command==='scan'){
        if(args.source!=='search'){
          const pattern=args.source==='following'?/^(Following|正在关注|关注)$/i:/^(For you|为你推荐|推荐)$/i;
          const tab=await until(()=>Array.from(document.querySelectorAll('[role="tab"]')).find(el=>pattern.test(el.innerText.trim())),deadline);
          if(tab.getAttribute('aria-selected')!=='true'){tab.click();await sleep(900);}
        }
        await until(()=>document.querySelector('article[data-testid="tweet"]'),deadline);
        const posts=new Map();
        for(let i=0;i<4&&Date.now()<deadline-2000;i++){
          for(const post of extract())if(post.url&&posts.size<60)posts.set(post.url,post);
          window.scrollBy(0,800);await sleep(1100);
        }
        return {ok:true,posts:[...posts.values()]};
      }
      if(command==='read'){
        await until(()=>document.querySelector('article[data-testid="tweet"] a[href*="/status/'+args.id+'"] time'),deadline);
        return {ok:true,posts:extract()};
      }
      if(command!=='publish')throw Error('不支持的操作');
      if(account().toLowerCase()!==args.account.toLowerCase())throw Error('X 当前账号与确认发布时不同，已停止。');
      const target=await until(()=>Array.from(document.querySelectorAll('article[data-testid="tweet"]')).find(el=>Array.from(el.querySelectorAll('a:has(time)')).some(link=>new URL(link.href).pathname.endsWith('/status/'+args.id))),deadline,20000,'原帖');
      {const w=xWarning();if(w)throw Error('X_WARNING '+w);}
      const editable='[data-testid="tweetTextarea_0"][contenteditable="true"]';
      // 优先用原帖下方的内联回复框；没有时再点回复按钮打开弹窗。X 有时会叠加其它 role=dialog 的提示层，所以只认含编辑框的弹窗。
      const inlineBox=()=>{const b=Array.from(document.querySelectorAll('[data-testid="primaryColumn"] '+editable)).find(el=>visible(el)&&!el.closest('[role="dialog"]'));return b?{box:b,root:b.closest('[data-testid="primaryColumn"]'),button:'tweetButtonInline'}:null;};
      const dialogBox=()=>{for(const d of Array.from(document.querySelectorAll('[role="dialog"]')).filter(visible)){const b=d.querySelector(editable);if(b&&visible(b))return {box:b,root:d,button:'tweetButton'};}return null;};
      let composer=await until(()=>inlineBox()||dialogBox(),deadline,4000,'').catch(()=>null);
      if(!composer){
        const reply=target.querySelector('[data-testid="reply"]');if(!reply)throw Error('未找到回复按钮（可能该帖限制了回复）');
        if(reply.getAttribute('aria-disabled')==='true'||reply.disabled)throw Error('该帖作者限制了谁可以回复，已跳过。');
        reply.click();
        composer=await until(()=>dialogBox()||inlineBox(),deadline,12000,'回复编辑框');
      }
      const box=composer.box,dialog=composer.root;
      box.click();box.focus();await sleep(300);
      const norm=t=>(t||'').replace(/\u00a0/g,' ').trim();
      const selectAll=()=>{box.focus();const selection=window.getSelection(),range=document.createRange();range.selectNodeContents(box);selection.removeAllRanges();selection.addRange(range);};
      const clear=async()=>{for(let i=0;i<3&&norm(box.innerText);i++){selectAll();document.execCommand('delete');await sleep(250);}};
      // X 的编辑器（Draft.js）对 execCommand insertText 有时会插入两遍，先用粘贴事件，失败再退回并清空重试。
      const attempts=[
        ()=>{selectAll();const data=new DataTransfer();data.setData('text/plain',args.text);box.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));},
        ()=>{selectAll();document.execCommand('insertText',false,args.text);}
      ];
      let typed=false;
      for(const attempt of attempts){
        await clear();attempt();await sleep(600);
        if(norm(box.innerText)===norm(args.text)){typed=true;break;}
      }
      if(!typed){await clear();throw Error('输入内容与确认文字不一致，已清空草稿并停止发送，未发出。');}
      const send=await until(()=>{const b=dialog.querySelector('[data-testid="'+composer.button+'"]')||dialog.querySelector('[data-testid="tweetButton"],[data-testid="tweetButtonInline"]');return b&&!b.disabled&&b.getAttribute('aria-disabled')!=='true'?b:null;},deadline,15000,'发送按钮');
      if(account().toLowerCase()!==args.account.toLowerCase())throw Error('发送前账号发生变化，已停止。');
      if(Date.now()>deadline-5000)throw Error('确认已过期，请回助手重新操作。');
      if(norm(box.innerText)!==norm(args.text))throw Error('草稿被修改，已停止发送。');
      clicked=true;send.click();
      const end=Math.min(deadline,Date.now()+12000);
      while(Date.now()<end){
        const toast=Array.from(document.querySelectorAll('[data-testid="toast"]')).find(el=>/(Your (post|reply|Tweet) was sent|你的(帖子|回复|推文)已发送|已发[送布])/i.test(el.innerText));
        if(toast){const link=Array.from(toast.querySelectorAll('a')).find(el=>/^https:\/\/x\.com\/[A-Za-z0-9_]+\/status\/\d+/.test(el.href));return {ok:true,clicked:true,sent:true,reply_url:link?.href?.split('?')[0]||''};}
        if(!visible(dialog)||(composer.button==='tweetButtonInline'&&!norm(box.innerText))){
          const sent=extract().find(post=>norm(post.text)===norm(args.text)&&new URL(post.url||'https://x.com').pathname.startsWith('/'+args.account+'/status/')&&!post.url.endsWith('/status/'+args.id));
          if(sent)return {ok:true,clicked:true,sent:true,reply_url:sent.url};
        }
        {const w=xWarning();if(w)throw Error('X_WARNING 已点击发送后出现风控提示：'+w);}
        await sleep(400);
      }
      throw Error('已点击发送，但结果未能确认。请在 X 检查，助手不会自动重发。');
    }catch(error){return {ok:false,error:error.message,clicked};}
  }
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if(sender.id!==chrome.runtime.id||message?.channel!=='x-comment-assistant')return false;
    if(!Number.isFinite(message.deadline)||Date.now()>message.deadline){respond({ok:false,error:'操作已过期',clicked:false});return false;}
    run(message.command,message.args||{},message.deadline).then(respond).catch(error=>respond({ok:false,error:error.message,clicked:false}));return true;
  });
})();
