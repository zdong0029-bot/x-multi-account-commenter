// 只在助手本机的 /setup 页面运行：把页面上的一次性配对信息交给扩展后台。
(() => {
  const meta = document.querySelector('meta[name="x-assistant-pair"]');
  const state = document.getElementById('state');
  if (!meta || location.pathname !== '/setup') return;
  chrome.runtime.sendMessage({channel:'x-assistant-setup', code:meta.content, profile:meta.dataset.profile, label:meta.dataset.label}, result => {
    if (!state) return;
    state.textContent = result?.ok ? '扩展已就位，正在连接助手。登录 X 后回面板点「检查身份」。' : ('扩展自动连接失败：' + (result?.error || chrome.runtime.lastError?.message || '未知错误'));
  });
})();
