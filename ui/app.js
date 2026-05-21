const state = {
  current: null,
  targets: [],
  modalSelectedKeys: new Set(),
  startupItems: [],
  schedule: null,
  activePage: 'home',
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  })[char]);
}

async function callApi(name, ...args) {
  const result = await window.pywebview.api[name](...args);
  if (result && result.ok === false) {
    alert(result.error || 'Operação não concluída.');
  }
  return result;
}

async function refresh() {
  state.current = await window.pywebview.api.get_state();
  renderHome();
}

function renderHome() {
  const data = state.current;
  if (!data) return;

  $('#foundSize').textContent = data.size;
  $('#mainStatus').textContent = data.mode;
  $('#progressRing').style.setProperty('--value', `${Math.max(data.progress, data.sizeBytes ? 100 : 0) * 3.6}deg`);
  $('#activeLocations').textContent = `${data.selectedCount} de ${data.targetCount} locais selecionados.`;
  $('#summaryFiles').textContent = data.cleanupSummary.files;
  $('#summarySize').textContent = data.cleanupSummary.size;
  $('#summaryWarnings').textContent = data.cleanupSummary.warnings;
  $('#summaryLast').textContent = data.cleanupSummary.lastCleanup;
  $('#historyPreview').textContent = data.cleanupSummary.lastCleanup === 'Ainda não realizada'
    ? 'Nenhuma limpeza registrada ainda.'
    : `Última limpeza: ${data.cleanupSummary.size} liberados.`;

  const shieldIcon = `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#248cff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><polyline points="9 12 11 14 15 10"></polyline></svg>`;
  const broomIcon = `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#ffb800" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>`;

  $('#cleanTop').disabled = !data.canClean;
  $('#scanTop').disabled = data.busy;
  
  if (data.busy) {
    $('#progressRing .ring-inner').innerHTML = `<span>${data.progress}%</span>`;
  } else {
    $('#progressRing .ring-inner').innerHTML = data.canClean ? broomIcon : shieldIcon;
  }

  renderBreakdown(data.categories);
  renderMonitor(data.monitor);
}

function renderBreakdown(categories) {
  const meaningful = [...categories].sort((a, b) => b.bytes - a.bytes || b.selected - a.selected).slice(0, 5);
  $('#breakdown').innerHTML = meaningful.map((cat) => `
    <div class="breakdown-row">
      <span class="breakdown-dot ${cat.enabled ? 'enabled' : 'disabled'}"></span>
      <span class="name">${escapeHtml(cat.group)}</span>
      <span class="value">${escapeHtml(cat.size)}</span>
      <span class="check">OK</span>
    </div>
  `).join('');
}

function renderMonitor(monitor) {
  if (!monitor) return;
  $('#memoryGaugeCard').style.setProperty('--gauge', `${Math.max(0, Math.min(100, monitor.memory.percent))}%`);
  $('#memoryValueCardPanel').textContent = `${Math.round(monitor.memory.percent)}%`;
  $('#memoryTextCardPanel').textContent = monitor.memory.text;
  $('#diskGaugeCard').style.setProperty('--gauge', `${Math.max(0, Math.min(100, monitor.disk.percent))}%`);
  $('#diskValueCard').textContent = `${Math.round(monitor.disk.percent)}%`;
  $('#diskTextCard').textContent = monitor.disk.text;
}

async function startScan() {
  const result = await callApi('start_scan');
  if (result && result.ok) await refresh();
}

function requestCleanup() {
  const data = state.current;
  if (!data || !data.canClean) return;
  $('#confirmText').textContent = `Excluir permanentemente ${data.files} arquivos encontrados (${data.size})? Arquivos em uso ou sem permissão serão ignorados.`;
  $('#confirmModal').classList.add('show');
}

async function confirmCleanup() {
  $('#confirmModal').classList.remove('show');
  const result = await callApi('start_cleanup');
  if (result && result.ok) await refresh();
}

async function openTargetsModal() {
  state.targets = await window.pywebview.api.get_targets();
  state.modalSelectedKeys = new Set(state.targets.filter((target) => target.enabled).map((target) => target.key));
  renderTargetsModal();
  $('#targetsModal').classList.add('show');
}

function renderTargetsModal() {
  const grouped = new Map();
  state.targets.forEach((target) => {
    if (!grouped.has(target.group)) grouped.set(target.group, []);
    grouped.get(target.group).push(target);
  });

  $('#modalTargetList').innerHTML = [...grouped.entries()].map(([group, targets]) => `
    <section class="modal-group">
      <h4>${escapeHtml(group)}</h4>
      ${targets.map((target) => {
        const enabled = state.modalSelectedKeys.has(target.key);
        return `
          <button class="target-option ${enabled ? 'selected' : ''}" data-key="${escapeHtml(target.key)}">
            <span class="fake-check">${enabled ? 'OK' : ''}</span>
            <span>
              <strong>${escapeHtml(target.name)}</strong>
              <small>${escapeHtml(target.path)}</small>
              <em>${escapeHtml(target.description)}</em>
            </span>
          </button>
        `;
      }).join('')}
    </section>
  `).join('');

  document.querySelectorAll('.target-option').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.dataset.key;
      if (state.modalSelectedKeys.has(key)) state.modalSelectedKeys.delete(key);
      else state.modalSelectedKeys.add(key);
      renderTargetsModal();
    });
  });
}

async function saveTargetsModal() {
  const result = await callApi('set_selected_targets', [...state.modalSelectedKeys]);
  if (result && result.ok) {
    $('#targetsModal').classList.remove('show');
    await refresh();
  }
}

async function openHistoryModal() {
  const history = await window.pywebview.api.get_history();
  $('#historyList').innerHTML = history.length
    ? history.map((entry) => `
      <article class="history-item">
        <div>
          <strong>${escapeHtml(entry.when)}</strong>
          <span>${entry.files} arquivos removidos | ${entry.folders} pastas vazias</span>
        </div>
        <em>${escapeHtml(entry.size)}</em>
      </article>
    `).join('')
    : `<div class="empty-history">Nenhuma limpeza foi registrada ainda.</div>`;
  $('#historyModal').classList.add('show');
}

async function loadSchedule() {
  const result = await callApi('get_schedule');
  if (!result || !result.ok) return;
  state.schedule = result.schedule;
  renderSchedule();
}

function renderSchedule() {
  const schedule = state.schedule;
  if (!schedule) return;

  $('#scheduleEnabled').checked = schedule.enabled;
  $('#scheduleFrequency').value = schedule.frequency;
  $('#scheduleTime').value = schedule.time;
  $('#scheduleWeekday').value = schedule.weekday;
  $('#scheduleMonthday').value = String(schedule.monthday);

  $('#scheduleStatus').textContent = schedule.enabled ? 'Ativado' : 'Desativado';
  $('#scheduleSummaryFrequency').textContent = schedule.frequency === 'daily'
    ? 'Diária'
    : schedule.frequency === 'weekly'
      ? 'Semanal'
      : 'Mensal';
  $('#scheduleSummaryTime').textContent = schedule.time;
  $('#scheduleSummaryNote').textContent = schedule.taskExists
    ? 'Tarefa registrada no Agendador do Windows.'
    : 'Sem tarefa registrada.';

  updateScheduleVisibility();
}

function updateScheduleVisibility() {
  const frequency = $('#scheduleFrequency').value;
  $('#weekdayField').classList.toggle('hidden', frequency !== 'weekly');
  $('#monthdayField').classList.toggle('hidden', frequency !== 'monthly');
}

async function openTaskScheduler() {
  await callApi('open_task_scheduler');
}

async function saveSchedule() {
  const enabled = $('#scheduleEnabled').checked;
  const frequency = $('#scheduleFrequency').value;
  const time = $('#scheduleTime').value || '09:00';
  const weekday = $('#scheduleWeekday').value;
  const monthday = Number.parseInt($('#scheduleMonthday').value, 10) || 1;

  const result = await callApi('save_schedule', enabled, frequency, time, weekday, monthday);
  if (result && result.ok) {
    await loadSchedule();
  }
}

function switchPage(pageName) {
  state.activePage = pageName;
  document.querySelectorAll('.page').forEach((page) => {
    page.classList.toggle('active', page.id === pageName);
  });
  document.querySelectorAll('.nav-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.page === pageName);
  });
}

function seedMonthdayOptions() {
  $('#scheduleMonthday').innerHTML = Array.from({ length: 31 }, (_, index) => {
    const day = index + 1;
    return `<option value="${day}">${day}</option>`;
  }).join('');
}

async function init() {
  seedMonthdayOptions();

  $('#scanTop').addEventListener('click', startScan);
  $('#cleanTop').addEventListener('click', requestCleanup);
  $('#confirmClean').addEventListener('click', confirmCleanup);
  $('#cancelClean').addEventListener('click', () => $('#confirmModal').classList.remove('show'));
  $('#locationsCard').addEventListener('click', openTargetsModal);
  $('#historyCard').addEventListener('click', openHistoryModal);
  $('#historyTop').addEventListener('click', openHistoryModal);
  $('#modalSelectAll').addEventListener('click', () => {
    state.modalSelectedKeys = new Set(state.targets.map((target) => target.key));
    renderTargetsModal();
  });
  $('#modalClearAll').addEventListener('click', () => {
    state.modalSelectedKeys.clear();
    renderTargetsModal();
  });
  $('#cancelTargets').addEventListener('click', () => $('#targetsModal').classList.remove('show'));
  $('#saveTargets').addEventListener('click', saveTargetsModal);
  $('#closeHistory').addEventListener('click', () => $('#historyModal').classList.remove('show'));
  $('#minimizeWindow').addEventListener('click', () => callApi('minimize_window'));
  $('#maximizeWindow').addEventListener('click', () => callApi('toggle_maximize_window'));
  $('#closeWindow').addEventListener('click', () => callApi('close_window'));
  $('#saveSchedule').addEventListener('click', saveSchedule);
  $('#openTaskScheduler').addEventListener('click', openTaskScheduler);
  $('#scheduleFrequency').addEventListener('change', updateScheduleVisibility);

  document.querySelectorAll('.nav-btn').forEach((button) => {
    button.addEventListener('click', async () => {
      const page = button.dataset.page;
      switchPage(page);
      if (page === 'schedule') await loadSchedule();
    });
  });

  await refresh();
  await loadSchedule();
  setInterval(refresh, 1000);
}

window.addEventListener('pywebviewready', init);
