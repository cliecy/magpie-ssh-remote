const $ = id => document.getElementById(id);
let snapshot = { profiles: [], statuses: {}, hosts: [], models: [] };
let selected = null, busy = false, lastWrite = 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const identity = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), x => x.toString(16).padStart(2, '0')).join('');

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}
function updateButtons() {
  document.querySelectorAll('button').forEach(button => { button.disabled = busy; });
  for (const id of ['connect', 'disconnect', 'delete', 'install-codex', 'install-claude', 'uninstall-codex', 'uninstall-claude']) {
    $(id).disabled = busy || !selected;
  }
}
function fill(profile) {
  selected = profile?.id || null;
  $('editor-title').textContent = profile ? '服务器配置' : '添加服务器';
  for (const [id, key, fallback] of [['name', 'name', ''], ['host', 'host', ''], ['local-port', 'localPort', 3425], ['remote-port', 'remotePort', 13425], ['model', 'model', 'group/default']]) {
    $(id).value = profile?.[key] ?? fallback;
  }
  render();
}
function render() {
  $('profiles').replaceChildren();
  for (const profile of snapshot.profiles) {
    const status = snapshot.statuses[profile.id] || { state: 'disconnected', message: '未连接' };
    const button = document.createElement('button');
    button.className = 'profile' + (selected === profile.id ? ' selected' : '');
    const title = document.createElement('strong');
    title.textContent = profile.name;
    const subtitle = document.createElement('small');
    const dot = document.createElement('span');
    dot.className = 'dot ' + status.state;
    subtitle.append(dot, document.createTextNode(profile.host + ' · ' + ({ connected: '已连接', connecting: '连接中', retrying: '等待重连', disconnected: '已断开' }[status.state] || status.state)));
    button.append(title, subtitle);
    button.onclick = () => fill(profile);
    $('profiles').append(button);
  }
  if (!snapshot.profiles.length) {
    const hint = document.createElement('p');
    hint.className = 'muted small'; hint.textContent = '还没有服务器，先在右侧添加一个。';
    $('profiles').append(hint);
  }
  for (const [id, values] of [['hosts', snapshot.hosts], ['models', snapshot.models]]) {
    $(id).replaceChildren(...values.map(value => { const option = document.createElement('option'); option.value = value; return option; }));
  }
  const profile = snapshot.profiles.find(p => p.id === selected);
  const status = snapshot.statuses[selected];
  $('connection').textContent = profile ?
    `${status?.message || '尚未连接'}\n服务器 127.0.0.1:${profile.remotePort} → 本机 127.0.0.1:${profile.localPort}` : '保存后即可连接。';
  $('connection').style.whiteSpace = 'pre-wrap';
  $('sample-time').textContent = snapshot.at ? `状态采样于 ${new Date(snapshot.at * 1000).toLocaleTimeString()} · 点击刷新状态获取最新连接结果` : '';
  updateButtons();
}

async function request(action, data = {}) {
  if (busy) return;
  busy = true; updateButtons();
  notice(action === 'snapshot' ? '正在获取状态…' : '正在执行…');
  try {
    const wait = 1100 - (Date.now() - lastWrite);
    if (wait > 0) await sleep(wait);
    const options = await magpie.options.get();
    const id = identity();
    await magpie.options.set({ ...options, request: { id, action, ...data } });
    lastWrite = Date.now();
    const deadline = Date.now() + 65000;
    while (Date.now() < deadline) {
      await sleep(700);
      const current = await magpie.options.get();
      if (current.response?.id !== id) continue;
      snapshot = current.response.snapshot;
      render();
      if (current.response.error) throw new Error(current.response.error);
      notice(current.response.result || '已完成');
      return true;
    }
    throw new Error('后台助手未及时响应。请在 Magpie 的插件管理中关闭后重新启用 SSH 远端接入，再刷新状态。');
  } catch (error) {
    notice(error.message || String(error), true);
    return false;
  } finally { busy = false; updateButtons(); }
}

$('new').onclick = () => fill(null);
$('refresh').onclick = () => request('snapshot');
$('save').onclick = async () => {
  const id = selected || identity();
  const success = await request('save', { profile: { id, name: $('name').value.trim(), host: $('host').value.trim(), localPort: Number($('local-port').value), remotePort: Number($('remote-port').value), model: $('model').value.trim() } });
  if (success) fill(snapshot.profiles.find(p => p.id === id));
};
$('connect').onclick = async () => {
  if (await request('connect', { profileId: selected })) {
    // One delayed check; background reconnection itself does not rewrite plugin options.
    await sleep(2500); await request('snapshot');
  }
};
$('disconnect').onclick = () => request('disconnect', { profileId: selected });
$('delete').onclick = async () => { if (await request('delete', { profileId: selected })) fill(null); };
for (const agent of ['codex', 'claude']) {
  for (const action of ['install', 'uninstall']) {
    $(`${action}-${agent}`).onclick = () => request(action, { profileId: selected, agent });
  }
}

(async () => {
  await magpie.ready;
  await request('snapshot');
  if (snapshot.profiles.length) fill(snapshot.profiles[0]);
})().catch(error => notice(String(error), true));
