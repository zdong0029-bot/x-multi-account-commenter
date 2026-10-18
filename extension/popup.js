document.getElementById('connect').onclick = async () => {
  const button = document.getElementById('connect');
  button.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({active:true, currentWindow:true});
    if (!tab?.id || !/^https:\/\/x\.com\//.test(tab.url || '')) throw Error('请先切到已经登录的 x.com 标签页，再点击扩展图标。');
    const url = new URL(document.getElementById('url').value.trim());
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password) throw Error('地址应为 http://127.0.0.1:18865 或面板显示的本机地址。');
    const code = document.getElementById('code').value.trim().toUpperCase();
    if (!/^[A-F0-9]{12}$/.test(code)) throw Error('请复制面板显示的完整 12 位配对码。');
    const stored=await chrome.storage.local.get('client_id');
    const client_id=stored.client_id||crypto.randomUUID();
    await chrome.storage.local.set({client_id});
    await chrome.storage.session.set({connection:{url:url.origin, code, tabId:tab.id, client_id,label:document.getElementById('label').value.trim()}});
    const connector = chrome.runtime.getURL('connector.html');
    const tabs = await chrome.tabs.query({url:connector});
    if (tabs.length) {await chrome.tabs.remove(tabs.map(t=>t.id));}
    await chrome.tabs.create({url:connector, active:true});
    window.close();
  } catch(error) {document.getElementById('error').textContent=error.message; button.disabled=false;}
};
