'use strict';
const messages = document.querySelector('#messages');
const question = document.querySelector('#question');
const send = document.querySelector('#send');
const status = document.querySelector('#status');
const evidenceDialog = document.querySelector('#evidence-dialog');
const evidenceList = document.querySelector('#evidence-list');
const authPanel = document.querySelector('#auth-panel');
const chatUi = document.querySelector('#chat-ui');
const signIn = document.querySelector('#sign-in');
const continueGuest = document.querySelector('#continue-guest');
const signInFromGuest = document.querySelector('#sign-in-from-guest');
const signOut = document.querySelector('#sign-out');
const sessionLabel = document.querySelector('#session-label');
let sender = crypto.randomUUID();
let busy = false;
let microsoftToken = null;
let authConfig = null;
let msalClient = null;
let activeAccount = null;
let signedInWithTeams = false;

async function verifiedIdentity(token) {
  const response = await fetch('/auth/me', {headers: {Authorization: `Bearer ${token}`}});
  if (!response.ok) throw new Error('Microsoft sign-in could not be verified.');
  return response.json();
}

function enableChat(identity, inTeams) {
  microsoftToken = identity.token;
  signedInWithTeams = inTeams;
  sender = identity.aad_object_id || sender;
  authPanel.hidden = true;
  chatUi.hidden = false;
  signInFromGuest.hidden = true;
  signOut.hidden = false;
  sessionLabel.textContent = identity.email || identity.display_name || 'Signed in';
  const welcome = messages.querySelector('.assistant');
  if (identity.display_name && welcome) {
    welcome.textContent = `Hi ${identity.display_name}! I can answer HR policy questions with sources, show your live leave balance, and list upcoming or annual holidays.`;
  }
  status.textContent = `${inTeams ? 'Connected to Teams' : 'Signed in'}${identity.email ? ` as ${identity.email}` : ''}`;
}

function enableGuestChat() {
  microsoftToken = null;
  signedInWithTeams = false;
  activeAccount = null;
  authPanel.hidden = true;
  chatUi.hidden = false;
  signInFromGuest.hidden = false;
  signOut.hidden = true;
  sessionLabel.textContent = 'Guest session';
  status.textContent = 'Guest session — email identity is not available';
}

async function completeSignIn(token, inTeams) {
  const identity = await verifiedIdentity(token);
  enableChat({...identity, token}, inTeams);
}

function append(text, role) {
  const item = document.createElement('article');
  item.className = role;
  item.textContent = text;
  messages.append(item);
  item.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function cleanLeaveName(name) {
  return String(name || '').replace(/^ESOL_\s*/, '').replace(/^Emergys_/, '');
}

function appendTable(title, headers, rows) {
  if (!rows.length) return false;
  const card = document.createElement('article');
  card.className = 'assistant result-card';
  const heading = document.createElement('strong');
  heading.textContent = title;
  const table = document.createElement('table');
  const headerRow = table.createTHead().insertRow();
  headers.forEach(label => { const cell = document.createElement('th'); cell.textContent = label; headerRow.append(cell); });
  const tbody = table.createTBody();
  rows.forEach(values => { const row = tbody.insertRow(); values.forEach(value => { const cell = row.insertCell(); cell.textContent = value ?? ''; }); });
  card.append(heading, table);
  messages.append(card);
  card.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  return true;
}

function appendStructuredResult(result) {
  const leave = result.leave_balance;
  if (leave && leave.status === 'answered') {
    const rows = (leave.balances || []).filter(item => item.available_balance !== null && item.available_balance !== undefined)
      .map(item => [cleanLeaveName(item.leave_name), `${item.available_balance} ${item.unit || 'days'}`]);
    const rendered = appendTable('Your current leave balances', ['Leave type', 'Available balance'], rows);
    if (leave.employee_mapping_fallback) append('Your email was not found in the employee directory, so the configured default employee profile was used.', 'assistant');
    return rendered;
  }
  const holiday = result.holiday_list;
  if (holiday && holiday.status === 'answered') {
    const rows = (holiday.holidays || []).map(item => [item.date, item.day, item.name]);
    const title = holiday.mode === 'full_year'
      ? `Holiday list for ${holiday.year}`
      : (holiday.requested_count && rows.length < holiday.requested_count
        ? `Last ${rows.length} holidays of ${holiday.year}`
        : `Next ${rows.length} holidays`);
    const rendered = appendTable(title, ['Date', 'Day', 'Holiday'], rows);
    if (holiday.employee_mapping_fallback) append('Your email was not found in the employee directory, so the configured default employee profile was used.', 'assistant');
    return rendered;
  }
  return false;
}

function showEvidence(chunks) {
  evidenceList.replaceChildren();
  chunks.forEach((chunk, index) => {
    const card = document.createElement('section');
    card.className = 'excerpt-card';
    const heading = document.createElement('h3');
    heading.textContent = `${index + 1}. ${chunk.section || 'Policy excerpt'}`;
    const metadata = document.createElement('dl');
    [['Source', chunk.source], ['Page', chunk.page], ['Chunk ID', chunk.id]].forEach(([label, value]) => {
      const term = document.createElement('dt');
      term.textContent = label;
      const detail = document.createElement('dd');
      detail.textContent = value ?? 'Unspecified';
      metadata.append(term, detail);
    });
    const excerpt = document.createElement('blockquote');
    excerpt.textContent = chunk.text || 'No excerpt available.';
    card.append(heading, metadata, excerpt);
    if (chunk.pdf_url) {
      const link = document.createElement('a');
      link.className = 'pdf-link';
      link.href = chunk.pdf_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = `Open PDF${chunk.page ? ` at page ${chunk.page}` : ''}`;
      card.append(link);
    }
    evidenceList.append(card);
  });
  evidenceDialog.showModal();
}

function appendEvidenceButton(chunks) {
  if (!chunks.length) return;
  const wrapper = document.createElement('article');
  wrapper.className = 'evidence-launcher';
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = `View ${chunks.length} source excerpt${chunks.length === 1 ? '' : 's'}`;
  button.addEventListener('click', () => showEvidence(chunks));
  wrapper.append(button);
  messages.append(wrapper);
  wrapper.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
async function submit() {
  const message = question.value.trim();
  if (!message || busy) return;
  busy = true;
  send.disabled = true;
  append(message, 'user');
  question.value = '';
  status.textContent = 'Looking up your policy…';
  try {
    const headers = {'Content-Type': 'application/json'};
    if (microsoftToken) headers.Authorization = `Bearer ${microsoftToken}`;
    const response = await fetch('/chat', {
      method: 'POST', headers,
      body: JSON.stringify({sender, message}), signal: AbortSignal.timeout(70000)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Unable to answer. Please try again.');
    const botMessages = Array.isArray(result.messages)
      ? result.messages.filter(item => typeof item.text === 'string' && item.text.trim())
      : [];
    if (appendStructuredResult(result)) {
      // Structured results are rendered as accessible tables.
    } else if (botMessages.length) {
      botMessages.forEach(item => append(item.text, 'assistant'));
    } else {
      append(result.answer || 'No response was returned. Please rephrase your question.', 'assistant');
    }
    const chunks = Array.isArray(result.sources) ? result.sources : [];
    appendEvidenceButton(chunks);
    status.textContent = 'Ready';
  } catch (error) {
    append(error.name === 'TimeoutError' ? 'The request timed out. Please try again.' : error.message, 'error');
    status.textContent = 'Request failed — you can try again.';
  } finally {
    busy = false;
    send.disabled = false;
    question.focus();
  }
}
document.querySelector('#chat-form').addEventListener('submit', e => {e.preventDefault(); submit();});
document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => {
  if (!busy) {question.value = button.dataset.question; submit();}
}));
document.querySelector('#reset').addEventListener('click', () => {
  if (busy) return;
  sender = crypto.randomUUID();
  messages.replaceChildren();
  append('New conversation. How can I help with HR policies?', 'assistant');
});
(async function initializeAuthentication() {
  try {
    authConfig = await fetch('/auth/config').then(response => response.json());
    if (!authConfig.enabled) throw new Error('Microsoft authentication is not configured.');
    try {
      if (!window.microsoftTeams) throw new Error('SDK unavailable');
      await Promise.race([microsoftTeams.app.initialize(), new Promise((_, reject) => setTimeout(() => reject(new Error('Not in Teams')), 5000))]);
      const context = await microsoftTeams.app.getContext();
      document.documentElement.dataset.theme = context.app.theme;
      microsoftTeams.app.registerOnThemeChangeHandler(theme => {document.documentElement.dataset.theme = theme;});
      status.textContent = 'Signing in with Teams…';
      const token = await microsoftTeams.authentication.getAuthToken();
      await completeSignIn(token, true);
      return;
    } catch (_) {
      status.textContent = 'Sign in with your Microsoft account to continue';
    }
    if (!window.msal) throw new Error('Microsoft sign-in library is unavailable.');
    msalClient = new msal.PublicClientApplication({auth: {
      clientId: authConfig.client_id,
      authority: `https://login.microsoftonline.com/${authConfig.tenant_id}`,
      redirectUri: window.location.origin + window.location.pathname,
    }, cache: {cacheLocation: 'sessionStorage'}});
    const existing = msalClient.getAllAccounts()[0];
    if (existing) {
      try {
        const result = await msalClient.acquireTokenSilent({account: existing, scopes: [authConfig.scope]});
        activeAccount = result.account;
        await completeSignIn(result.accessToken, false);
      } catch (_) { /* Require an explicit user gesture for popup fallback. */ }
    }
  } catch (error) {
    status.textContent = error.message;
    signIn.disabled = true;
  }
})();

async function microsoftSignIn() {
  if (!msalClient || !authConfig) return;
  signIn.disabled = true;
  status.textContent = 'Opening Microsoft sign-in…';
  try {
    const result = await msalClient.loginPopup({scopes: [authConfig.scope, 'openid', 'profile', 'email']});
    const tokenResult = result.accessToken
      ? result
      : await msalClient.acquireTokenSilent({account: result.account, scopes: [authConfig.scope]});
    activeAccount = tokenResult.account || result.account;
    await completeSignIn(tokenResult.accessToken, false);
  } catch (error) {
    status.textContent = 'Microsoft sign-in failed. Please try again.';
    signIn.disabled = false;
  }
}

signIn.addEventListener('click', microsoftSignIn);
signInFromGuest.addEventListener('click', microsoftSignIn);

signOut.addEventListener('click', async () => {
  signOut.disabled = true;
  try {
    if (msalClient && activeAccount && !signedInWithTeams) {
      await msalClient.logoutPopup({account: activeAccount, mainWindowRedirectUri: window.location.href});
    }
  } catch (_) {
    // Local guest transition still clears EmployeeAssist's active token.
  } finally {
    signOut.disabled = false;
    enableGuestChat();
  }
});

continueGuest.addEventListener('click', enableGuestChat);

document.querySelector('#close-evidence').addEventListener('click', () => evidenceDialog.close());
evidenceDialog.addEventListener('click', event => { if (event.target === evidenceDialog) evidenceDialog.close(); });
