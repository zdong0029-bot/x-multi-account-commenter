// 后台：浏览器启动时自动打开连接页并用设备密钥重连；助手打开的新窗口里的设置页会触发自动配对。
// 不读取密码、不导出 Cookie，只负责把连接页和 X 标签页准备好。
const CONNECTOR = () => chrome.runtime.getURL('connector.html');
async function xTab(windowId){
  const query = windowId ? {url:'https://x.com/*', windowId} : {url:'https://x.com/*'};
  const tabs = await chrome.tabs.query(query);
  if (tabs.length) return tabs[0];
  return await chrome.tabs.create({url:'https://x.com/home', active:false, ...(windowId ? {windowId} : {})});
}
async function openConnector(connection, windowId){
  await chrome.storage.session.set({connection});
  const tabs = await chrome.tabs.query({url:CONNECTOR()});
  if (tabs.length) await chrome.tabs.remove(tabs.map(t => t.id));
  await chrome.tabs.create({url:CONNECTOR(), active:false, ...(windowId ? {windowId} : {})});
}
async function resumeIfSaved(){
  const {saved} = await chrome.storage.local.get('saved');
  if (!saved?.device_secret || !saved?.url) return;
  const tab = await xTab();
  await openConnector({url:saved.url, tabId:tab.id, client_id:saved.client_id, label:saved.label || ''}, tab.windowId);
}
chrome.runtime.onStartup.addListener(() => { setTimeout(() => resumeIfSaved().catch(() => {}), 1500); });
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.channel !== 'x-assistant-setup' || !sender.tab) return false;
  (async () => {
    const url = new URL(sender.tab.url || sender.url || '');
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.pathname !== '/setup') throw Error('来源不正确');
    const code = String(message.code || '').toUpperCase();
    if (!/^[A-F0-9]{12}$/.test(code)) throw Error('配对码无效');
    const tab = await xTab(sender.tab.windowId);
    const stored = await chrome.storage.local.get(['client_id', 'saved']);
    const client_id = stored.client_id || crypto.randomUUID();
    await chrome.storage.local.set({client_id});
    // 已经配对过的配置用设备密钥重连；新配置用设置页里的配对码。
    const resume = stored.saved?.device_secret && stored.saved?.url === url.origin;
    await openConnector({url:url.origin, code: resume ? '' : code, tabId:tab.id, client_id,
                         label:String(message.label || '').slice(0, 60), profile:String(message.profile || '')}, sender.tab.windowId);
    respond({ok:true});
  })().catch(error => respond({ok:false, error:error.message}));
  return true;
});
