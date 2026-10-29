(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const roles = {browser:'网页采集', analysis:'报表分析'};
  $('browser-panel-title').textContent = '网页采集 Sandbox';
  $('analysis-panel-title').textContent = '报表分析 Sandbox';
  const signatures = {};
  let identity = {};
  let selected = {};
  let connected = true;
  let runtimeState = 'loading';
  let lastEvents = [];
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function ids(data) {
    identity = {...identity, ...data};
    for (const role of Object.keys(roles)) {
      const id = identity[role + '_sandbox_id'];
      $(role + '-sandbox-id').textContent = id || '等待实例 ID';
      document.querySelector('[data-copy-sandbox="' + role + '"]').disabled = !id;
    }
  }
  function render(role, events) {
    const list = events.filter(event => event.role === role);
    const active = connected && runtimeState === 'ready' && list.findLast(event => event.state === 'running');
    const chosen = list.find(event => event.id === selected[role]) || active || (role === 'analysis' && list.findLast(event => event.tool === 'run_python')) || list.at(-1);
    const signature = JSON.stringify([list, chosen && chosen.id, identity, connected, runtimeState]);
    if (signatures[role] === signature) return;
    signatures[role] = signature;
    const box = $(role + '-execution');
    const open = box.querySelector('details')?.open;
    box.replaceChildren();
    const heading = node('div', undefined, 'execution-heading');
    heading.append(node('strong', active ? active.title : chosen ? '最近：' + chosen.title : roles[role] + ' · 等待任务'));
    const state = !connected ? 'unknown' : runtimeState === 'expired' ? 'stopped' : active ? 'running' : chosen ? chosen.state === 'running' ? 'recorded' : chosen.state : 'idle';
    heading.append(node('span', {running:'执行中',completed:'已完成',error:'执行失败',idle:'待执行',unknown:'连接中断 · 保留记录',stopped:'本轮已结束',recorded:'最近记录 · 状态待确认'}[state], 'badge ' + (state === 'error' ? 'badge-warning' : state === 'running' ? 'badge-success' : 'badge-outline')));
    box.append(heading);
    if (role === 'browser') box.append(node('p', 'Chromium 筛选 → 翻页 → 下载 CSV → 传入分析沙箱；后续追问可复用文件。', 'execution-description'));
    else box.append(node('p', 'Python 在此沙箱执行；读取 CSV，计算统计结果并生成交付文件。', 'execution-description'));
    if (chosen) {
      box.append(node('p', '输入 · ' + (chosen.input_summary || chosen.input), 'execution-io'));
      box.append(node('p', '输出 · ' + (chosen.output_summary || '等待工具返回真实结果'), 'execution-io'));
    }
    const details = node('details');
    details.open = Boolean(open);
    details.append(node('summary', '查看' + (role === 'analysis' ? '代码、输入输出' : '输入输出') + '与模板'));
    const metadata = node('dl', undefined, 'execution-meta');
    for (const [label, value] of [
      ['模板名称', identity[role + '_template_name']],
      ['Template ID', identity[role + '_template']],
      ['镜像', identity[role + '_image']]
    ]) { metadata.append(node('dt', label), node('dd', value || '本轮未记录（旧模板）')); }
    if (list.length) {
      const history = node('select');
      history.setAttribute('aria-label', roles[role] + '动作历史');
      [...list].reverse().forEach(event => {
        const time = typeof event.started_at === 'number' ? new Date(event.started_at * 1000).toLocaleTimeString('zh-CN', {hour12:false}) : '';
        const option = node('option', time + ' · ' + event.title + (event.state === 'error' ? ' · 失败' : ''));
        option.value = event.id;
        option.selected = event.id === chosen.id;
        history.append(option);
      });
      history.addEventListener('change', () => { selected[role] = history.value; delete signatures[role]; render(role, events); });
      details.append(history);
      if (chosen) {
        details.append(node('h3', '输入'), node('pre', chosen.input));
        if (chosen.code) details.append(node('h3', '实际执行的 Python · analysis.py'), node('pre', chosen.code, 'execution-code'));
        details.append(node('h3', '输出' + (chosen.elapsed_ms != null ? ' · ' + (chosen.elapsed_ms / 1000).toFixed(1) + ' 秒' : '')), node('pre', chosen.output));
        if (role === 'browser') details.append(node('p', '采集由分析沙箱中的 Worker 发起，Chromium 实际在上方 Browser ID 对应的沙箱运行。', 'execution-description'));
      }
    } else details.append(node('p', '还没有此沙箱的真实工具执行记录。', 'execution-description'));
    details.append(metadata);
    box.append(details);
  }
  document.querySelectorAll('[data-copy-sandbox]').forEach(button => {
    button.addEventListener('click', async () => {
      const value = identity[button.dataset.copySandbox + '_sandbox_id'];
      if (!value) return;
      try { await navigator.clipboard.writeText(value); button.textContent = '已复制'; }
      catch (_) { button.textContent = '请选择并复制左侧 ID'; }
      setTimeout(() => { button.textContent = '复制 ID'; }, 2500);
    });
  });
  document.addEventListener('demo-status', event => {
    runtimeState = event.detail.state;
    const data = {};
    for (const role of Object.keys(roles)) if (event.detail[role + '_sandbox_id']) data[role + '_sandbox_id'] = event.detail[role + '_sandbox_id'];
    ids(data);
    for (const role of Object.keys(roles)) render(role, lastEvents);
  });
  async function refresh() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 7000);
    try {
      const response = await fetch('/api/execution', {cache:'no-store', signal:controller.signal});
      if (!response.ok) throw new Error('Execution unavailable');
      const data = await response.json();
      connected = true;
      ids(data.identity || {});
      lastEvents = data.events || [];
      for (const role of Object.keys(roles)) render(role, lastEvents);
      $('execution-connection').hidden = true;
    } catch (_) {
      connected = false;
      for (const role of Object.keys(roles)) render(role, lastEvents);
      $('execution-connection').hidden = false;
      // Older running images can still supply their real IDs.
      try { const response = await fetch('/api/identity', {cache:'no-store', signal:controller.signal}); if (response.ok) ids(await response.json()); } catch (_) {}
    } finally { clearTimeout(timeout); setTimeout(refresh, 1800); }
  }
  const notice = node('p', '执行记录暂不可用。旧运行需用新版模板重建后展示；已有结果与任务不会被重新执行。', 'execution-connection');
  notice.id = 'execution-connection'; notice.hidden = true;
  document.querySelector('#workspace').before(notice);
  for (const role of Object.keys(roles)) render(role, []);
  refresh();
})();
