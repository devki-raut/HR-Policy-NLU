'use strict';
const messages = document.querySelector('#messages');
const question = document.querySelector('#question');
const send = document.querySelector('#send');
const status = document.querySelector('#status');
const evidenceDialog = document.querySelector('#evidence-dialog');
const evidenceList = document.querySelector('#evidence-list');
let sender = crypto.randomUUID();
let busy = false;

function append(text, role) {
  const item = document.createElement('article');
  item.className = role;
  item.textContent = text;
  messages.append(item);
  item.scrollIntoView({behavior: 'smooth', block: 'nearest'});
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
    const response = await fetch('/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json', ...(document.querySelector('#api-key').value ? {'X-API-Key': document.querySelector('#api-key').value} : {})},
      body: JSON.stringify({sender, message}), signal: AbortSignal.timeout(70000)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Unable to answer. Please try again.');
    const botMessages = Array.isArray(result.messages)
      ? result.messages.filter(item => typeof item.text === 'string' && item.text.trim())
      : [];
    if (botMessages.length) {
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
(async () => {
  try {
    if (!window.microsoftTeams) throw new Error('SDK unavailable');
    await Promise.race([microsoftTeams.app.initialize(), new Promise((_, reject) => setTimeout(() => reject(new Error('Not in Teams')), 5000))]);
    const context = await microsoftTeams.app.getContext();
    document.documentElement.dataset.theme = context.app.theme;
    microsoftTeams.app.registerOnThemeChangeHandler(theme => {document.documentElement.dataset.theme = theme;});
    // Teams context is used only for appearance, never as proof of identity.
    status.textContent = 'Connected to Teams';
  } catch (_) {status.textContent = 'Browser preview — ready to chat';}
})();

document.querySelector('#close-evidence').addEventListener('click', () => evidenceDialog.close());
evidenceDialog.addEventListener('click', event => { if (event.target === evidenceDialog) evidenceDialog.close(); });
