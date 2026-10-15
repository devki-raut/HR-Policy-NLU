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
let ticketConversation = false;
let ticketStep = null;
let microsoftToken = null;
let authConfig = null;
let msalClient = null;
let activeAccount = null;
let signedInWithTeams = false;
let teamsContextAvailable = false;

async function verifiedIdentity(token) {
  const response = await fetch('auth/me', {headers: {Authorization: `Bearer ${token}`}});
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Microsoft sign-in could not be verified.');
  }
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
    welcome.textContent = `Hi ${identity.display_name}! I can answer HR policy questions with sources, show your live leave balance, list upcoming or annual holidays, help with IT issues, and prepare IT requests.`;
  }
  status.textContent = `${inTeams ? 'Connected to Teams' : 'Signed in'}${identity.email ? ` as ${identity.email}` : ''}`;
}

function enableGuestChat() {
  if (authConfig?.required) {
    microsoftToken = null; activeAccount = null; signedInWithTeams = false;
    authPanel.hidden = false; chatUi.hidden = true; signIn.disabled = false;
    status.textContent = 'Sign in with Microsoft to continue.';
    return;
  }
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
  if (!token) {
    if (authConfig?.required) throw new Error('Microsoft sign-in is required.');
    enableGuestChat(); return;
  }
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
function ticketCommand(command) {
  if (!busy) { question.value = command; submit(); }
}
function appendTicket(result) {
  const draft = (result.messages || []).find(item => item.custom?.type === 'ticket_draft')?.custom;
  if (!draft) return false;
  ticketConversation = false;
  ticketStep = null;
  const card = document.createElement('article');
  card.className = 'assistant result-card';
  const form = document.createElement('form'); form.className = 'ticket-mail-form';
  const heading = document.createElement('strong'); heading.textContent = 'IT support request';
  const tip = document.createElement('p'); tip.textContent = 'Describe your issue here, including the application/device, error and start time if known.';
  form.append(heading, tip);
  const inputs = {};
  for (const [key, label, value, limit, placeholder] of [
    ['to', 'To', authConfig?.ticket_recipient || draft.to || 'helpdesk@emergys.com', 2000, 'helpdesk@example.com'],
    ['cc', 'CC (optional)', draft.cc || '', 2000, 'name@example.com; another@example.com'],
    ['subject', 'Subject', draft.subject || '', 180, 'Internet connectivity issue'],
    ['body', 'Body', draft.body || '', 6000, 'Describe the issue or access request, affected application/device, error and start time.'],
  ]) {
    const field = document.createElement('label'); field.textContent = label;
    const input = document.createElement(key === 'body' ? 'textarea' : 'input');
    if (key !== 'body') input.type = 'text'; else input.rows = 8;
    input.name = key; input.value = value; input.maxLength = limit;
    input.placeholder = placeholder; input.required = key !== 'cc';
    inputs[key] = input; field.append(input); form.append(field);
  }
  const note = document.createElement('p'); note.className = 'ticket-mail-status'; note.setAttribute('aria-live', 'polite');
  note.textContent = 'Raise a request sends these fields from your Microsoft mailbox. Enter submits from a single-line field; Ctrl+Enter submits from the body. Enter in the body adds a new line.';
  const raise = document.createElement('button'); raise.type = 'submit'; raise.textContent = 'Raise a request';
  const open = document.createElement('a'); open.textContent = 'Open in Outlook'; open.target = '_blank'; open.rel = 'noopener noreferrer';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
  let requestId = null; let sending = false; let finished = false;
  function values() { return Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value.trim()])); }
  function refreshOutlook() {
    open.href = 'https://outlook.office.com/mail/deeplink/compose?' + Object.entries(values()).map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join('&');
  }
  form.addEventListener('input', refreshOutlook); refreshOutlook();
  open.addEventListener('click', event => {
    if (sending || finished || !form.reportValidity()) { event.preventDefault(); return; }
    refreshOutlook();
  });
  function lock(value) {
    sending = value;
    raise.disabled = value || finished; cancel.disabled = value || finished;
    Object.values(inputs).forEach(input => {input.disabled = value || finished;});
    open.setAttribute('aria-disabled', String(value || finished));
  }
  inputs.body.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {event.preventDefault(); form.requestSubmit(raise);}
  });
  cancel.onclick = async () => {
    if (sending || finished) return;
    lock(true);
    try {
      if (requestId) {
        const response = await fetch(`tickets/drafts/${requestId}`, {method: 'DELETE', headers: {Authorization: `Bearer ${microsoftToken}`}});
        if (!response.ok) {const payload = await response.json(); throw new Error(payload.detail || 'Could not cancel the draft.');}
      }
      finished = true; note.textContent = 'IT request cancelled.'; open.removeAttribute('href');
    } catch (error) {note.textContent = error.message;}
    finally {lock(false);}
  };
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || finished || !form.reportValidity()) return;
    if (!microsoftToken) {note.textContent = 'Sign in with Microsoft to send, or use Open in Outlook for manual sending.'; return;}
    const edited = values(); lock(true);
    try {
      let authorization;
      if (signedInWithTeams) {
        note.textContent = 'Using Teams single sign-on…';
        microsoftToken = await microsoftTeams.authentication.getAuthToken();
        authorization = {use_teams_sso: true};
      } else {
        await initializeMsalClient();
        const scopes = ['https://graph.microsoft.com/User.Read', 'https://graph.microsoft.com/Mail.Send'];
        let mailToken;
        if (activeAccount) {
          try {mailToken = await msalClient.acquireTokenSilent({account: activeAccount, scopes});}
          catch (_) {mailToken = await msalClient.acquireTokenPopup({account: activeAccount, scopes});}
        } else {mailToken = await msalClient.acquireTokenPopup({scopes, loginHint: sessionLabel.textContent});}
        authorization = {graph_token: mailToken.accessToken};
      }
      const headers = {'Content-Type': 'application/json', Authorization: `Bearer ${microsoftToken}`};
      const response = await fetch(requestId ? `tickets/drafts/${requestId}` : 'tickets/drafts', {
        method: requestId ? 'PUT' : 'POST', headers, body: JSON.stringify(edited),
      });
      const prepared = await response.json();
      if (!response.ok) throw new Error(typeof prepared.detail === 'string' ? prepared.detail : 'Could not prepare the request.');
      requestId = prepared.request_id;
      note.textContent = 'Submitting your request…';
      const sent = await fetch(`tickets/drafts/${requestId}/send`, {method: 'POST', headers, body: JSON.stringify(authorization)});
      const payload = await sent.json();
      if (!sent.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not send the request.');
      finished = true; note.textContent = payload.message; raise.textContent = 'Request submitted'; open.removeAttribute('href');
    } catch (error) {
      note.textContent = `${error.message} If sending may have started, check Sent Items before retrying.`;
    } finally {lock(false);}
  });
  form.append(note, raise, open, cancel); card.append(form); messages.append(card);
  card.scrollIntoView({behavior: 'smooth', block: 'nearest'}); inputs.subject.focus();
  return true;
}
function appendReplyButtons(result) {
  for (const item of result.messages || []) {
    for (const choice of item.buttons || []) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = choice.title;
      button.onclick = () => {ticketCommand(choice.payload); button.disabled = true;}; messages.append(button);
    }
  }
}
async function submit() {
  const message = question.value.trim();
  if (!message || busy) return;
  busy = true;
  send.disabled = true;
  append(message, 'user');
  question.value = '';
  status.textContent = 'Preparing a response…';
  try {
    const headers = {'Content-Type': 'application/json'};
    if (microsoftToken) headers.Authorization = `Bearer ${microsoftToken}`;
    const response = await fetch('chat', {
      method: 'POST', headers,
      body: JSON.stringify({sender, message}), signal: AbortSignal.timeout(70000)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Unable to answer. Please try again.');
    for (const item of result.messages || []) {
      if (item.custom?.type === 'ticket_state') {
        ticketConversation = item.custom.active;
        ticketStep = item.custom.requested_slot || null;
      }
    }
    const botMessages = Array.isArray(result.messages)
      ? result.messages.filter(item => typeof item.text === 'string' && item.text.trim())
      : [];
    if (appendTicket(result) || appendStructuredResult(result)) {
      // Structured results are rendered as accessible tables.
    } else if (botMessages.length) {
      botMessages.forEach(item => append(item.text, 'assistant'));
    } else if (!(result.messages || []).some(item => item.custom?.type === 'ticket_state')) {
      append(result.answer || 'No response was returned. Please rephrase your question.', 'assistant');
    }
    appendReplyButtons(result);
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
function authRedirectUri() {
  if (authConfig?.redirect_uri) return authConfig.redirect_uri;
  const base = new URL(document.baseURI);
  return (window.location.origin + base.pathname).replace(/\/+$/, '');
}

async function initializeMsalClient() {
  if (!window.msal) throw new Error('Microsoft sign-in library is unavailable.');
  if (!msalClient) {
    msalClient = new msal.PublicClientApplication({auth: {
      clientId: authConfig.client_id,
      authority: `https://login.microsoftonline.com/${authConfig.tenant_id}`,
      redirectUri: authRedirectUri(),
    }, cache: {cacheLocation: 'sessionStorage'}});
    if (msalClient.initialize) await msalClient.initialize();
  }
  return msalClient;
}

(async function initializeAuthentication() {
  // The opener processes mailbox popup responses; do not consume their hash here.
  if (window.opener && /(?:code|error)=/.test(window.location.hash)) return;
  try {
    const configResponse = await fetch('auth/config');
    if (!configResponse.ok) throw new Error('Microsoft authentication configuration is unavailable.');
    authConfig = await configResponse.json();
    continueGuest.hidden = Boolean(authConfig.required);
    continueGuest.disabled = Boolean(authConfig.required);
    if (!authConfig.enabled) throw new Error('Microsoft authentication is not configured.');
    try {
      if (!window.microsoftTeams) throw new Error('SDK unavailable');
      await Promise.race([microsoftTeams.app.initialize(), new Promise((_, reject) => setTimeout(() => reject(new Error('Not in Teams')), 5000))]);
      const context = await microsoftTeams.app.getContext();
      teamsContextAvailable = true;
      document.documentElement.dataset.theme = context.app.theme;
      microsoftTeams.app.registerOnThemeChangeHandler(theme => {document.documentElement.dataset.theme = theme;});
      status.textContent = 'Signing in with Teams…';
      await completeSignIn(await microsoftTeams.authentication.getAuthToken(), true);
      return;
    } catch (_) {
      status.textContent = 'Sign in with your Microsoft account to continue';
    }
    await initializeMsalClient();
    const redirected = await msalClient.handleRedirectPromise();
    if (redirected) {
      activeAccount = redirected.account;
      msalClient.setActiveAccount(activeAccount);
      await completeSignIn(redirected.idToken, false);
      return;
    }
    const existing = msalClient.getAllAccounts()[0];
    if (existing) {
      try {
        const result = await msalClient.acquireTokenSilent({account: existing, scopes: ['openid', 'profile', 'email']});
        activeAccount = result.account;
        msalClient.setActiveAccount(activeAccount);
        await completeSignIn(result.idToken, false);
      } catch (_) { /* Wait for an explicit sign-in action. */ }
    }
  } catch (error) {
    status.textContent = error.message;
    signIn.disabled = true;
  }
})();

async function microsoftSignIn() {
  if (!authConfig) return;
  signIn.disabled = true;
  status.textContent = 'Opening Microsoft sign-in…';
  try {
    if (teamsContextAvailable) {
      await completeSignIn(await microsoftTeams.authentication.getAuthToken(), true);
      signIn.disabled = false;
      return;
    }
    await initializeMsalClient();
    await msalClient.loginRedirect({scopes: ['openid', 'profile', 'email']});
  } catch (error) {
    status.textContent = `Microsoft sign-in failed: ${error.errorCode || error.message || 'Please try again.'}`;
    signIn.disabled = false;
  }
}

signIn.addEventListener('click', microsoftSignIn);
signInFromGuest.addEventListener('click', microsoftSignIn);

signOut.addEventListener('click', async () => {
  signOut.disabled = true;
  try {
    if (msalClient && activeAccount && !signedInWithTeams) {
      await msalClient.logoutRedirect({account: activeAccount, postLogoutRedirectUri: authRedirectUri()});
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
