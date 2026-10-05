const money = (value) => `₹${Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const $ = (selector) => document.querySelector(selector);
let isLoading = false;
let committeeItems = [];
const committeePhones = new Map();
let allCommitteeMembers = [];

function showNotice(message, tone = 'info') {
  const notice = $('#notice');
  notice.hidden = !message;
  notice.className = `notice ${tone}`;
  notice.textContent = message;
}

function renderMetrics(data) {
  const totals = data.totals || {};
  $('#total-donations').textContent = money(totals.donations);
  $('#total-expenses').textContent = money(totals.expenses);
  $('#balance').textContent = money(totals.balance);
  const committee = data.committee || {};
  $('#committee-total').textContent = money(committee.total_fixed);
  $('#donation-count').textContent = `${totals.donation_count || 0} entries`;
  $('#expense-count').textContent = `${totals.expense_count || 0} entries`;
  $('#committee-count').textContent = `${committee.listed_count || 0} / ${committee.target_count || 22} members`;
  $('#balance').classList.toggle('negative', Number(totals.balance || 0) < 0);
}

function renderExpenseChart(items) {
  const chart = $('#expense-chart');
  if (!items?.length) { chart.innerHTML = '<div class="empty-chart">No expense data yet</div>'; return; }
  const max = Math.max(...items.map((item) => Number(item.amount)), 1);
  chart.innerHTML = items.slice(-10).map((item) => `<div class="bar-column"><span>${money(item.amount)}</span><div class="bar-track"><i style="height:${Math.max(7, (item.amount / max) * 100)}%"></i></div><small>${item.label}</small></div>`).join('');
}

function renderPaymentPies(data) {
  const colors = { Cash: '#f8b84e', Online: '#79d49c', Other: '#c49cff' };
  ['received', 'spent'].forEach((group) => {
    const items = data?.[group] || [];
    const total = items.reduce((sum, item) => sum + Number(item.amount || 0), 0);
    const pie = $(`#${group}-pie`);
    const legend = $(`#${group}-legend`);
    if (!total) {
      pie.style.background = 'conic-gradient(rgba(255,255,255,.08) 0 100%)';
      pie.innerHTML = '<span>₹0</span>';
      legend.innerHTML = '<small>No payment data yet</small>';
      return;
    }
    let start = 0;
    const stops = items.map((item) => {
      const end = start + (Number(item.amount) / total) * 100;
      const stop = `${colors[item.method] || colors.Other} ${start}% ${end}%`;
      start = end;
      return stop;
    });
    pie.style.background = `conic-gradient(${stops.join(', ')})`;
    pie.innerHTML = `<span>${money(total)}</span>`;
    legend.innerHTML = items.map((item) => `<div><i style="background:${colors[item.method] || colors.Other}"></i><span>${escapeHtml(item.method)}</span><b>${money(item.amount)}</b></div>`).join('');
  });
}

function renderVolunteers(items) {
  const list = $('#volunteer-list');
  if (!items?.length) { list.innerHTML = '<div class="empty-list">No volunteer donations yet</div>'; return; }
  const max = Math.max(...items.map((item) => Number(item.amount)), 1);
  list.innerHTML = items.slice(0, 8).map((item, index) => `<div class="volunteer-row"><div class="avatar">${index + 1}</div><div class="volunteer-info"><strong>${escapeHtml(item.name)}</strong><div class="progress"><i style="width:${Math.max(5, (item.amount / max) * 100)}%"></i></div></div><b>${money(item.amount)}</b></div>`).join('');
}

function renderCommitteeMembers(data) {
  const list = $('#committee-list');
  const items = data?.members || [];
  committeeItems = items;
  $('#committee-target').textContent = data?.target_count || 22;
  populateCommitteeSelects(items);
  if (!items.length) { list.innerHTML = '<div class="empty-list">No committee members yet</div>'; return; }
  list.innerHTML = items.map((item, index) => `<div class="volunteer-row"><div class="avatar">${index + 1}</div><div class="volunteer-info"><strong>${escapeHtml(item.name)}</strong><div class="progress"><i style="width:${item.fixed_amount ? Math.min(100, Math.max(5, (item.paid_amount / item.fixed_amount) * 100)) : 5}%"></i></div><small class="committee-breakdown">Paid ${money(item.paid_amount)} · Remaining ${money(item.remaining_amount)}</small></div><b>${money(item.fixed_amount)}</b></div>`).join('');
}

function populateCommitteeSelects(items) {
  allCommitteeMembers = items.map((item) => item.name).filter(Boolean);
  setCommitteeMemberOptions();
  const askedByOptions = items.map((item) => item.name).filter(Boolean);
  setSelectOptions('amount_asked_by', askedByOptions);
  setSelectOptions('added_by', ['Hemanth Lodda', 'Dinesh']);
}

function setSelectOptions(field, names) {
  const select = $(`#entry-form [name="${field}"]`);
  if (!select) return;
  const selected = select.value;
  select.innerHTML = '<option value="">Select committee member</option>' + names.map((name) => `<option value="${escapeHtml(name)}" data-phone="${escapeHtml(committeePhones.get(name.toLowerCase()) || '')}">${escapeHtml(name)}</option>`).join('');
  if (names.includes(selected)) select.value = selected;
}

function setCommitteeMemberOptions() {
  const isCommittee = $('#entry-mode').value === 'committee';
  const names = isCommittee ? allCommitteeMembers : ['Hemanth Lodda', 'Dinesh'];
  setSelectOptions('committee_member_name', names);
}

async function loadCommitteePhones() {
  try {
    const response = await fetch('/api/committee-phones', { cache: 'no-store' });
    if (!response.ok) return;
    const data = await response.json();
    committeePhones.clear();
    (data.members || []).forEach((item) => committeePhones.set(item.name.toLowerCase(), item.phone));
    populateCommitteeSelects(committeeItems);
    updatePhoneFromSelection();
  } catch (error) {
    // Phone auto-fill becomes available after phone numbers are added to the committee tab.
  }
}

function updatePhoneFromSelection() {
  const askedBy = $('#entry-form [name="amount_asked_by"]');
  const committeeMember = $('#entry-form [name="committee_member_name"]');
  const source = committeeMember;
  const option = source?.options[source.selectedIndex];
  const phoneInput = $('#phone-number-input');
  const phone = option?.dataset.phone || '';
  phoneInput.value = phone;
  phoneInput.readOnly = Boolean(phone);
}

function renderCommitteeBalance(data) {
  const summary = data || {};
  committeeItems = summary.members || [];
  populateCommitteeSelects(committeeItems);
  $('#committee-fixed-total').textContent = money(summary.total_fixed);
  $('#committee-paid-total').textContent = money(summary.total_paid);
  $('#committee-remaining-total').textContent = money(summary.total_remaining);
  const list = $('#committee-balance-list');
  const items = summary.members || [];
  if (!items.length) { list.innerHTML = '<div class="empty-list">No committee balances yet</div>'; return; }
  list.innerHTML = items.map((item) => {
    const fixedAmount = Number(item.fixed_amount || 0);
    const paidAmount = Number(item.paid_amount || 0);
    const paidPercent = fixedAmount > 0 ? Math.min(100, Math.max(0, (paidAmount / fixedAmount) * 100)) : 0;
    return `<div class="committee-balance-row"><div class="committee-member-progress"><div class="committee-member-heading"><strong>${escapeHtml(item.name)}</strong><small>${money(item.paid_amount)} paid of ${money(item.fixed_amount)}</small></div><div class="committee-progress-track" role="progressbar" aria-label="${escapeHtml(item.name)} paid amount" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${paidPercent.toFixed(0)}"><i style="width:${paidPercent}%"></i></div></div><b>${money(item.remaining_amount)} remaining</b></div>`;
  }).join('');
}

function renderExpenses(items) {
  const body = $('#expenses-body');
  if (!items?.length) { body.innerHTML = '<tr><td colspan="8" class="empty-table">No expenses yet. Add expense rows to the Google Sheet.</td></tr>'; return; }
  body.innerHTML = items.map((item) => {
    const receipt = /^https?:\/\//i.test(String(item.receipt_url || '')) ? `<a class="receipt-link" href="${escapeHtml(item.receipt_url)}" target="_blank" rel="noopener">View receipt</a>` : '—';
    return `<tr><td>${escapeHtml(item.date)}</td><td>${escapeHtml(item.added_by || '—')}</td><td>${escapeHtml(item.amount_asked_by || '—')}</td><td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.description)}</td><td>${escapeHtml(item.payment_method)}</td><td>${receipt}</td><td class="amount">${money(item.amount)}</td></tr>`;
  }).join('');
}

function renderDonations(items) {
  const body = $('#donations-body');
  if (!items?.length) { body.innerHTML = '<tr><td colspan="7" class="empty-table">No amounts yet. Add contribution rows to the Google Sheet.</td></tr>'; return; }
  body.innerHTML = items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.date || '—')}</td><td>${escapeHtml(item.added_by || '—')}</td><td>${escapeHtml(item.phone || '—')}</td><td><span class="type-pill ${item.type.toLowerCase().replaceAll(' ', '-')}">${escapeHtml(item.type)}</span></td><td>${item.entry_count || 1}</td><td class="amount">${money(item.amount)}</td></tr>`).join('');
}

function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character])); }

async function loadDashboard() {
  if (isLoading) return;
  isLoading = true;
  $('#updated-label').textContent = 'Updating…';
  try {
    const response = await fetch(`/api/dashboard?now=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Dashboard request failed');
    const data = await response.json();
    renderMetrics(data); renderExpenseChart(data.daily_expenses); renderPaymentPies(data.payment_breakdown); renderCommitteeBalance(data.committee); renderDonations(data.donor_entries); renderExpenses(data.expense_entries);
    const updated = data.updated_at ? new Date(data.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';
    $('#updated-label').textContent = `Updated ${updated}`;
    if (data.setup_needed) showNotice('Your Google Sheet is connected but empty. Add the header row and festival entries to see them here.', 'info');
    else if (data.error) showNotice(data.error, 'warning');
    else showNotice('');
  } catch (error) {
    $('#updated-label').textContent = 'Offline';
    showNotice('Could not refresh the Google Sheet right now. The dashboard will try again automatically.', 'warning');
  } finally {
    isLoading = false;
  }
}

function setLoginError(message) {
  const error = $('#login-error');
  error.hidden = !message;
  error.textContent = message || '';
}

function setAuthMode(mode) {
  const register = mode === 'register';
  $('#login-form').hidden = register;
  $('#register-form').hidden = !register;
  $('#signin-tab').classList.toggle('active', !register);
  $('#register-tab').classList.toggle('active', register);
  setLoginError('');
}

function showApp(role) {
  $('#login-view').hidden = true;
  $('#app-view').hidden = false;
  $('#role-label').textContent = role === 'admin' ? 'Admin' : 'Viewer';
  $('#admin-entry-panel').hidden = role !== 'admin';
  $('#change-password-button').hidden = role !== 'admin';
  closePasswordPanel();
  if (role === 'admin') { updateEntryFields(); loadCommitteePhones(); }
  loadDashboard();
}

function closePasswordPanel() {
  const panel = $('#password-panel');
  panel.hidden = true;
  panel.setAttribute('aria-hidden', 'true');
  $('#password-form').reset();
  $('#password-status').textContent = '';
  document.body.classList.remove('modal-open');
}

function openPasswordPanel() {
  const panel = $('#password-panel');
  panel.hidden = false;
  panel.setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');
  panel.querySelector('input').focus();
  loadRegisteredUsers();
}

async function loadRegisteredUsers() {
  const list = $('#registered-users-list');
  list.textContent = 'Loading registered viewers…';
  try {
    const response = await fetch('/api/admin/users', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not load registered viewers.');
    if (!data.users?.length) {
      list.innerHTML = '<div class="empty-list">No registered viewers yet.</div>';
      return;
    }
    list.innerHTML = data.users.map((user) => `<div class="viewer-row" data-username="${escapeHtml(user.username)}"><div class="viewer-details"><strong>${escapeHtml(user.username)}</strong><small>Registered ${escapeHtml(new Date(user.created_at).toLocaleString())}</small></div><button class="viewer-password-toggle secondary-button" type="button" data-action="show-viewer-password">Change password</button><form class="viewer-password-form" hidden><label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="8" required /></label><label>Confirm password<input name="confirm_password" type="password" autocomplete="new-password" minlength="8" required /></label><div class="viewer-password-actions"><button type="submit" class="primary-button">Update password</button><button type="button" class="secondary-button" data-action="cancel-viewer-password">Cancel</button><span class="entry-status viewer-password-status"></span></div></form></div>`).join('');
  } catch (error) {
    list.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

async function handleViewerPasswordSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const row = form.closest('.viewer-row');
  const username = row.dataset.username;
  const status = form.querySelector('.viewer-password-status');
  const button = form.querySelector('button[type="submit"]');
  status.textContent = 'Updating…';
  button.disabled = true;
  try {
    const response = await fetch(`/api/admin/users/${encodeURIComponent(username)}/password`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to update viewer password.');
    status.textContent = data.message;
    form.reset();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function updateEntryFields() {
  const isExpense = $('#entry-type').value === 'Expense';
  const isCommittee = $('#entry-mode').value === 'committee';
  const showCommitteeMember = isExpense || isCommittee;
  $('#amount-label').querySelector('input').placeholder = isExpense ? 'Amount spent' : 'Committee member amount';
  $('#amount-label').firstChild.textContent = isExpense ? 'Amount spent' : 'Committee member amount';
  $('#committee-member-name-label').hidden = !showCommitteeMember;
  $('#committee-member-name-label').firstChild.textContent = isCommittee ? 'Committee member name' : 'Added by';
  $('#committee-member-name-label select').required = showCommitteeMember;
  $('#added-by-label').hidden = !isCommittee;
  $('#added-by-label select').required = isCommittee;
  $('#amount-asked-by-label').hidden = !isExpense;
  $('#amount-asked-by-label select').required = isExpense;
  $('#category-label').hidden = isCommittee;
  $('#category-label input').required = isExpense;
  $('#receipt-label').hidden = !isExpense;
  $('#expense-mode-button').classList.toggle('active', isExpense);
  $('#committee-mode-button').classList.toggle('active', isCommittee);
  setCommitteeMemberOptions();
  updatePhoneFromSelection();
}

function setEntryMode(mode) {
  const isCommittee = mode === 'committee';
  $('#entry-mode').value = mode;
  $('#entry-type').value = isCommittee ? 'Donation' : 'Expense';
  if (isCommittee) $('#receipt-label input').value = '';
  updateEntryFields();
}

async function handleLogin(event) {
  event.preventDefault();
  setLoginError('');
  const form = new FormData(event.currentTarget);
  try {
    const response = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(form)) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to sign in.');
    showApp(data.role);
  } catch (error) {
    setLoginError(error.message);
  }
}

async function handleRegister(event) {
  event.preventDefault();
  setLoginError('');
  try {
    const form = new FormData(event.currentTarget);
    const response = await fetch('/api/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(form)) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to create account.');
    showApp(data.role);
  } catch (error) {
    setLoginError(error.message);
  }
}

async function handleChangePassword(event) {
  event.preventDefault();
  const status = $('#password-status');
  const button = event.currentTarget.querySelector('button[type="submit"]');
  status.textContent = 'Updating…';
  button.disabled = true;
  try {
    const form = new FormData(event.currentTarget);
    const response = await fetch('/api/admin/change-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(form)) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to update password.');
    status.textContent = data.message;
    event.currentTarget.reset();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function handleLogout() {
  await fetch('/api/logout', { method: 'POST' });
  $('#app-view').hidden = true;
  $('#login-view').hidden = false;
  $('#login-form').reset();
  $('#register-form').reset();
  $('#password-form').reset();
  setAuthMode('signin');
  setLoginError('');
}

async function handleEntry(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const status = $('#entry-status');
  const button = formElement.querySelector('button[type="submit"]');
  status.textContent = 'Saving…';
  button.disabled = true;
  try {
    const form = new FormData(formElement);
    const image = form.get('receipt_image');
    const payload = Object.fromEntries(form);
    delete payload.receipt_image;
    if (payload.mode === 'committee') {
      payload.type = 'Donation';
      payload.name = payload.committee_member_name;
      payload.committee_member_amount = payload.amount;
      payload.amount = '';
    } else {
      payload.name = payload.amount_asked_by;
    }
    delete payload.mode;
    if (image instanceof File && image.size) {
      payload.receipt_name = image.name;
      payload.receipt_mime_type = image.type;
      payload.receipt_data = await fileAsDataUrl(image);
    }
    const response = await fetch('/api/entries', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not save this entry.');
    status.textContent = 'Saved to Google Sheets';
    formElement.reset();
    $('#entry-date').value = new Date().toISOString().slice(0, 10);
    setEntryMode('expense');
    await loadDashboard();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('Could not read the receipt image.'));
    reader.readAsDataURL(file);
  });
}

$('#login-form').addEventListener('submit', handleLogin);
$('#register-form').addEventListener('submit', handleRegister);
$('#signin-tab').addEventListener('click', () => setAuthMode('signin'));
$('#register-tab').addEventListener('click', () => setAuthMode('register'));
$('#logout-button').addEventListener('click', handleLogout);
$('#change-password-button').addEventListener('click', openPasswordPanel);
$('#close-password-button').addEventListener('click', closePasswordPanel);
$('#cancel-password-button').addEventListener('click', closePasswordPanel);
$('#password-panel').addEventListener('click', (event) => {
  if (event.target === event.currentTarget) closePasswordPanel();
});
$('#registered-users-list').addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]');
  if (!action) return;
  const row = action.closest('.viewer-row');
  const form = row.querySelector('.viewer-password-form');
  if (action.dataset.action === 'show-viewer-password') {
    form.hidden = false;
    action.hidden = true;
    form.querySelector('input').focus();
  } else if (action.dataset.action === 'cancel-viewer-password') {
    form.hidden = true;
    form.reset();
    row.querySelector('[data-action="show-viewer-password"]').hidden = false;
    form.querySelector('.viewer-password-status').textContent = '';
  }
});
$('#registered-users-list').addEventListener('submit', (event) => {
  if (event.target.matches('.viewer-password-form')) handleViewerPasswordSubmit(event);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('#password-panel').hidden) closePasswordPanel();
});
$('#password-form').addEventListener('submit', handleChangePassword);
$('#entry-form').addEventListener('submit', handleEntry);
$('#expense-mode-button').addEventListener('click', () => setEntryMode('expense'));
$('#committee-mode-button').addEventListener('click', () => setEntryMode('committee'));
$('#entry-form [name="amount_asked_by"]').addEventListener('change', updatePhoneFromSelection);
$('#entry-form [name="committee_member_name"]').addEventListener('change', updatePhoneFromSelection);
$('#entry-date').value = new Date().toISOString().slice(0, 10);

async function restoreSession() {
  try {
    const response = await fetch('/api/session', { cache: 'no-store' });
    const data = await response.json();
    if (data.authenticated) showApp(data.role);
  } catch (error) {
    setLoginError('The server is not reachable right now.');
  }
}

restoreSession();
setInterval(() => {
  if (!$('#app-view').hidden) loadDashboard();
}, 5000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !$('#app-view').hidden) loadDashboard();
});
