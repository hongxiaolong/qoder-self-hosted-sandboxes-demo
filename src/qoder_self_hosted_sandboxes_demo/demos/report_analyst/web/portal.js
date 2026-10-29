(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const phaseOrder = ['collect', 'revise', 'append'];
  const phaseLabels = {new:'待开始', submitted:'进行中', done:'待验收', verified:'已通过'};
  let current = {state:'loading', phases:{}};
  let timer;
  let busy = false;
  let toastTimer;
  const phaseNames = {collect:'下载并分析', revise:'连续追问', append:'现场追加'};
  const phaseDescriptions = {collect:'从网页筛选并下载报表，在分析环境生成看板。', revise:'复用已下载的 CSV，调整统计范围并重新计算。', append:'追加一条现场记录，更新看板和全部交付文件。'};
  function resizeAnalysis() {
    const frame = document.querySelector('#analysis-panel iframe');
    if (frame && frame.contentWindow) frame.contentWindow.postMessage({type:'demo-resize'}, location.origin);
  }
  function selectPanel(panel, focus = false) {
    document.body.dataset.activePanel = panel;
    document.querySelectorAll('[data-panel-target]').forEach(button => {
      const selected = button.dataset.panelTarget === panel;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (focus && selected) button.focus();
    });
    if (panel === 'analysis') requestAnimationFrame(resizeAnalysis);
  }
  function updateTaskContext(status) {
    const phases = status.phases || {};
    const running = phaseOrder.find(phase => ['submitted','done'].includes((phases[phase] || {}).state));
    const next = phaseOrder.find((phase, index) => (phases[phase] || {}).state !== 'verified' && (!index || (phases[phaseOrder[index - 1]] || {}).state === 'verified'));
    const allDone = phaseOrder.every(phase => (phases[phase] || {}).state === 'verified');
    const phase = running || next;
    if (status.state !== 'ready') {
      $('current-task-label').textContent = '运行状态';
      $('current-task-title').textContent = status.state === 'expired' ? '本轮运行已结束' : status.state === 'loading' ? '正在准备演示' : '请检查执行环境';
      $('current-task-description').textContent = status.message || '请从向导打开本轮演示。';
    } else if (allDone) {
      $('current-task-label').textContent = '本轮完成';
      $('current-task-title').textContent = '三轮任务已通过';
      $('current-task-description').textContent = '结果和执行证据已核对，可以下载交付文件。';
    } else if (phase) {
      const info = phases[phase] || {};
      $('current-task-label').textContent = running ? (info.state === 'done' ? '等待验收' : '正在执行') : '接下来';
      $('current-task-title').textContent = phaseNames[phase];
      $('current-task-description').textContent = running ? phaseDescriptions[phase] : '复制该轮提示词，在 Qoder 会话发送，再回到向导验收。';
    }
    ['browser','analysis'].forEach(panel => {
      const active = running && (running === 'collect' ? panel === 'browser' : panel === 'analysis');
      const element = $(panel + '-activity');
      element.hidden = !active || status.state !== 'ready';
      element.textContent = active ? ((phases[running] || {}).state === 'done' ? '待验收' : panel === 'browser' ? '采集中' : '分析中') : '';
    });
  }
  async function updateOverview() {
    try {
      const data = await requestJson('/api/dashboard');
      if (data.waiting) {
        $('overview-rows').textContent = '—';
        $('overview-amount').textContent = '—';
        $('overview-version').textContent = '待发布';
        $('overview-note').textContent = '等待分析结果';
        return;
      }
      if (!Number.isFinite(data.row_count) || !Number.isFinite(data.total_amount)) throw new Error('Invalid overview');
      $('overview-rows').textContent = data.row_count.toLocaleString('zh-CN') + ' 条';
      $('overview-amount').textContent = data.total_amount.toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
      $('overview-version').textContent = 'v' + data.revision;
      $('overview-note').textContent = '本轮实际结果';
    } catch (_) { $('overview-note').textContent = '结果连接待恢复'; }
  }
  function safeSessionUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && ['qoder.com', 'www.qoder.com'].includes(url.hostname) && url.pathname.startsWith('/cloud/sessions/') ? url.href : '';
    } catch (_) { return ''; }
  }
  function showToast(message) {
    $('toast').textContent = message;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
  }
  function expiryValue(value) {
    if (!value) return null;
    const time = typeof value === 'number' ? (value < 100000000000 ? value * 1000 : value) : Date.parse(value);
    return Number.isFinite(time) ? time : null;
  }
  function applyStatus(data) {
    const status = {...data, phases:data.phases || {}};
    const expiry = expiryValue(status.expires_at);
    if (expiry && expiry <= Date.now()) {
      status.state = 'expired';
      status.message = '本轮执行环境已到期。请重新运行向导创建新一轮。';
    }
    const sessionUrl = safeSessionUrl(status.session_url);
    if (status.state === 'ready' && !sessionUrl) {
      status.state = 'unavailable';
      status.message = '没有取得有效的 Qoder Session 地址，请检查本轮资源。';
    }
    current = status;
    document.dispatchEvent(new CustomEvent('demo-status', {detail:status}));
    const ready = status.state === 'ready';
    const legacy = status.state === 'legacy';
    const stateLabels = {ready:'执行环境已连接', loading:'正在检查执行环境', expired:'本轮运行已结束', unavailable:'执行环境不可用', legacy:'会话状态待确认'};
    $('runtime-label').textContent = stateLabels[status.state] || '连接状态待确认';
    $('runtime-message').textContent = status.message || (ready ? 'Qoder 负责决策，Sandbox 承接实际工具执行。' : '请从启动向导检查本轮资源。');
    $('runtime-message').title = $('runtime-message').textContent;
    $('runtime-dot').className = 'status-dot ' + (ready ? 'is-ready' : status.state === 'loading' ? 'is-loading' : legacy ? '' : 'is-error');
    const canOpen = (ready || legacy) && Boolean(sessionUrl);
    if (canOpen) {
      $('session').href = sessionUrl;
      $('session').setAttribute('aria-disabled', 'false');
      $('session').removeAttribute('tabindex');
      $('session').title = '在新标签打开本次 Qoder Session；请使用创建会话的 Qoder 账号登录。';
      $('session-label').textContent = '打开 Qoder 会话';
    } else {
      $('session').removeAttribute('href');
      $('session').setAttribute('aria-disabled', 'true');
      $('session').tabIndex = -1;
      $('session').title = status.message || '本轮会话暂不可打开。';
      $('session-label').textContent = status.state === 'loading' ? '检查会话中' : status.state === 'expired' ? '会话已结束' : '会话暂不可用';
    }
    $('run-id').textContent = status.run_id ? 'Run ' + status.run_id : '';
    $('expiry').textContent = expiry && expiry > Date.now() ? new Date(expiry).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) + ' 到期' : '';
    phaseOrder.forEach((phase, index) => {
      const button = document.querySelector('[data-phase="' + phase + '"]');
      const info = status.phases[phase] || {state:'new'};
      const previous = index ? status.phases[phaseOrder[index - 1]] : null;
      const blockedByPrevious = previous && previous.state !== 'verified';
      const final = info.state === 'verified';
      const inProgress = info.state === 'submitted' || info.state === 'done';
      button.classList.toggle('is-verified', final);
      button.classList.toggle('is-running', inProgress);
      const nextAction = ready && !final && !inProgress && !blockedByPrevious && Boolean(info.prompt);
      button.classList.toggle('is-current', nextAction);
      if (nextAction) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
      button.querySelector('.step-state').textContent = phaseLabels[info.state] || '待开始';
      button.querySelector('.step-index').textContent = final ? '✓' : String(index + 1);
      button.disabled = !ready || !info.prompt || final || inProgress || Boolean(blockedByPrevious);
      button.title = !ready ? '本轮执行环境尚不可用' : final ? '本轮已验收，无需重复发送' : inProgress ? '本轮已提交，请等待完成并在终端验收' : blockedByPrevious ? '请先完成上一轮任务及验收' : '复制本轮提示词，再在 Qoder 会话发送';
      button.setAttribute('aria-label', button.querySelector('strong').textContent + '，' + button.title);
    });
    document.querySelectorAll('.pane iframe').forEach(frame => {
      if (frame.contentWindow) frame.contentWindow.postMessage({type:'demo-runtime', state:status.state, message:status.message}, location.origin);
    });
    updateTaskContext(status);
  }
  async function requestJson(path) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(path, {cache:'no-store', signal:controller.signal});
      if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  async function refreshStatus() {
    if (busy) return;
    busy = true;
    clearTimeout(timer);
    $('refresh-status').classList.add('is-checking');
    $('refresh-status').disabled = true;
    try {
      let data;
      try { data = await requestJson('/api/demo-status'); }
      catch (error) {
        if (![404, 405, 501].includes(error.status)) throw error;
        const identity = await requestJson('/api/identity');
        data = {...identity, state:'legacy', session_url:identity.qoder_session_url, phases:{}, message:'当前入口未提供实时会话校验。可打开 Qoder 确认，完整预览请从启动向导进入。'};
      }
      applyStatus(data);
      updateOverview();
    } catch (_) {
      applyStatus({...current, state:'unavailable', session_url:'', message:'无法连接本轮预览服务。请确认向导仍在运行，或点击右侧刷新。'});
    } finally {
      busy = false;
      $('refresh-status').classList.remove('is-checking');
      $('refresh-status').disabled = false;
      timer = setTimeout(refreshStatus, 5000);
    }
  }
  document.querySelectorAll('[data-phase]').forEach(button => {
    button.addEventListener('click', async () => {
      const expiry = expiryValue(current.expires_at);
      if (expiry && expiry <= Date.now()) {
        applyStatus({...current, state:'expired', message:'本轮执行环境已到期。请重新运行向导创建新一轮。'});
        showToast('本轮已到期，请先创建新的执行环境。');
        return;
      }
      const prompt = current.phases[button.dataset.phase] && current.phases[button.dataset.phase].prompt;
      if (button.disabled || !prompt) return;
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(prompt);
        showToast('提示词已复制。请打开 Qoder 会话，粘贴并发送。');
      } catch (_) {
        $('prompt-text').value = prompt;
        $('copy-fallback').hidden = false;
        $('prompt-text').focus();
        $('prompt-text').select();
        showToast('浏览器未允许自动复制，已展开可手动复制的提示词。');
      }
    });
  });
  $('close-fallback').addEventListener('click', () => { $('copy-fallback').hidden = true; });
  $('session').addEventListener('click', event => {
    const expiry = expiryValue(current.expires_at);
    if (!['ready','legacy'].includes(current.state) || (expiry && expiry <= Date.now())) {
      event.preventDefault();
      applyStatus({...current, state:'expired', message:'本轮执行环境已结束。请重新运行向导创建新一轮。'});
      showToast('当前会话暂不可用于发起新任务，请先检查运行状态。');
    }
  });
  $('refresh-status').addEventListener('click', refreshStatus);
  document.querySelectorAll('[data-panel-target]').forEach(button => {
    button.addEventListener('click', () => selectPanel(button.dataset.panelTarget));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === 'Home' ? 'browser' : event.key === 'End' ? 'analysis' : button.dataset.panelTarget === 'browser' ? 'analysis' : 'browser';
      selectPanel(target, true);
    });
  });
  document.querySelector('#analysis-panel iframe').addEventListener('load', resizeAnalysis);
  document.querySelectorAll('.pane iframe').forEach(frame => {
    frame.addEventListener('load', () => {
      if (frame.contentWindow) frame.contentWindow.postMessage({type:'demo-runtime', state:current.state, message:current.message}, location.origin);
    });
  });
  selectPanel('analysis');
  refreshStatus();
})();
