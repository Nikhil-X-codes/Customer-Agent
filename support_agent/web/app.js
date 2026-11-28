const $ = (id) => document.getElementById(id);
const conversation = $('conversation');
const messages = $('messages');
const question = $('question');
const form = $('chatForm');
const sendButton = $('sendButton');
let sessionToken = '';
let busy = false;
let domain = 'support';

function scrollToLatest() {
  conversation.scrollTop = conversation.scrollHeight;
}

function showSessionPanel(open) {
  $('sessionPanel').hidden = !open;
  $('sessionToggle').setAttribute('aria-expanded', String(open));
  if (open) $('tokenInput').focus();
}

function setSession(token) {
  sessionToken = token;
  $('sessionToggle').textContent = token ? 'Session token set' : 'Connect session';
  $('sessionToggle').classList.toggle('connected', Boolean(token));
  $('sessionStatus').textContent = token ? 'Token saved in this tab. It will be checked on your next request.' : 'No session token set';
  $('clearToken').hidden = !token;
  $('tokenInput').value = '';
  showSessionPanel(false);
}

$('sessionToggle').addEventListener('click', () => showSessionPanel($('sessionPanel').hidden));
$('saveToken').addEventListener('click', () => {
  const token = $('tokenInput').value.trim();
  if (token) setSession(token);
  else $('sessionStatus').textContent = 'Paste a token first.';
});
$('clearToken').addEventListener('click', () => setSession(''));
$('tokenInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') { event.preventDefault(); $('saveToken').click(); }
  if (event.key === 'Escape') showSessionPanel(false);
});

function addMessage(role, initialText = '') {
  $('emptyState').hidden = true;
  const row = document.createElement('div');
  row.className = `message ${role}`;
  if (role === 'assistant') {
    const icon = document.createElement('span');
    icon.className = 'message-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = 'C';
    row.append(icon);
  }
  const content = document.createElement('div');
  content.className = 'message-content';
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'assistant' ? 'ASSISTANT' : 'YOU';
  const body = document.createElement('div');
  body.className = 'message-body';
  body.textContent = initialText;
  const meta = document.createElement('div');
  meta.className = 'message-meta';
  content.append(label, body, meta);
  row.append(content);
  messages.append(row);
  scrollToLatest();
  return { body, meta };
}

function applyMeta(meta, footer) {
  footer.replaceChildren();
  const outcome = document.createElement('span');
  outcome.className = `response-status ${meta.outcome || ''}`;
  outcome.textContent = meta.outcome === 'answered' ? 'Source checked' :
    meta.outcome === 'clarify' ? 'More detail needed' :
    meta.ticket_id ? 'Review ticket created' : 'Human review needed';
  footer.append(outcome);

  if (meta.ticket_id) {
    const ticket = document.createElement('div');
    ticket.className = 'ticket-reference';
    ticket.textContent = `Ticket ${meta.ticket_id}`;
    footer.append(ticket);
  }

  if (meta.evidence?.length) {
    for (const item of meta.evidence) {
      const card = document.createElement('div');
      card.className = 'evidence-card';
      const title = document.createElement('strong');
      title.textContent = item.title;
      const quote = document.createElement('span');
      quote.textContent = `“${item.quote}”`;
      card.append(title, quote);
      footer.append(card);
    }
  }

  if (meta.sources?.length) {
    const sources = document.createElement('div');
    sources.className = 'source-list';
    const label = document.createElement('strong');
    label.textContent = 'Sources';
    sources.append(label);
    for (const source of meta.sources) {
      const code = document.createElement('code');
      code.textContent = source;
      sources.append(code);
    }
    footer.append(sources);
  }

  if (meta.explanation) {
    const details = document.createElement('details');
    details.className = 'explanation';
    const heading = document.createElement('summary');
    heading.textContent = 'Why this response';
    const summary = document.createElement('p');
    summary.textContent = meta.explanation.summary;
    details.append(heading, summary);
    if (meta.explanation.checks?.length) {
      const checks = document.createElement('ul');
      for (const check of meta.explanation.checks) {
        const item = document.createElement('li');
        item.textContent = check;
        checks.append(item);
      }
      details.append(checks);
    }
    const reason = document.createElement('small');
    reason.textContent = `Decision code: ${meta.explanation.reason_code}`;
    details.append(reason);
    footer.append(details);
  }
}

function parseEvents(block, onEvent) {
  let event = 'message';
  const lines = [];
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) lines.push(line.slice(5).trimStart());
  }
  if (!lines.length) return;
  onEvent(event, JSON.parse(lines.join('\n')));
}

async function sendQuestion(text) {
  const prompt = text.trim();
  if (busy || !prompt) return;
  busy = true;
  sendButton.disabled = true;
  sendButton.textContent = 'Checking';
  conversation.setAttribute('aria-busy', 'true');
  addMessage('user', prompt);
  const reply = addMessage('assistant', 'Checking current sources…');
  reply.body.classList.add('pending');
  question.value = '';
  let finished = false;
  let receivedText = false;
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
    const response = await fetch('/chat/stream', {
      method: 'POST', headers, body: JSON.stringify({ question: prompt })
    });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      if (response.status === 401) setSession('');
      throw new Error(failure.error || 'The service is unavailable. Please try again.');
    }
    if (!response.body) throw new Error('Streaming is not available in this browser.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const onEvent = (event, data) => {
      if (event === 'progress') reply.body.textContent = data.message || 'Checking sources…';
      if (event === 'meta') applyMeta(data, reply.meta);
      if (event === 'chunk') {
        if (!receivedText) { reply.body.textContent = ''; reply.body.classList.remove('pending'); receivedText = true; }
        reply.body.textContent += data.text || '';
        scrollToLatest();
      }
      if (event === 'done') finished = true;
      if (event === 'error') throw new Error(data.message || 'The response could not be completed.');
    };
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        parseEvents(buffer.slice(0, boundary), onEvent);
        buffer = buffer.slice(boundary + 2);
      }
    }
    if (!finished) throw new Error('The response ended before it was complete.');
  } catch (error) {
    reply.body.classList.remove('pending');
    reply.body.textContent = error.message || 'The service is unavailable. Please try again.';
    reply.meta.replaceChildren();
  } finally {
    busy = false;
    conversation.setAttribute('aria-busy', 'false');
    sendButton.disabled = false;
    sendButton.textContent = 'Send';
    scrollToLatest();
    question.focus();
  }
}

form.addEventListener('submit', (event) => { event.preventDefault(); sendQuestion(question.value); });
question.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
});

function addPrompts(supply) {
  const prompts = supply ? [
    ['Check stock for a SKU', 'Stock for SKU-'],
    ['Check a purchase order', 'Status of PO-'],
    ['Track a shipment', 'Where is SHP-'],
    ['Find low stock', 'Which SKUs are low stock?']
  ] : [
    ['Ask about a policy', 'What is your shipping policy?'],
    ['Ask about a product', 'Tell me about '],
    ['Check an order', 'Where is order ORD-']
  ];
  $('promptList').replaceChildren();
  for (const [label, value] of prompts) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => { question.value = value; question.focus(); });
    $('promptList').append(button);
  }
}

function updateServiceStatus(ready) {
  $('sourceState').classList.toggle('ready', ready);
  $('sourceState').classList.toggle('unavailable', !ready);
  $('sourceText').textContent = ready ? 'Service ready' : 'Service unavailable';
}

fetch('/ready', { cache: 'no-store' }).then((response) => updateServiceStatus(response.ok)).catch(() => updateServiceStatus(false));
fetch('/ui/config', { cache: 'no-store' }).then((response) => {
  if (!response.ok) throw new Error('Config unavailable');
  return response.json();
}).then((config) => {
  const supply = config.domain === 'supply_chain';
  domain = supply ? 'supply_chain' : 'support';
  $('docsLink').hidden = !config.knowledge_enabled;
  $('headerDocsLink').hidden = !config.knowledge_enabled;
  $('workspaceLabel').textContent = supply ? 'OPERATIONS' : 'SUPPORT';
  $('modeLabel').textContent = supply ? 'Supply chain' : 'Store support';
  $('welcomeTitle').textContent = supply ? 'What do you need to check?' : 'What can I help with?';
  $('welcomeDescription').textContent = supply ?
    'Ask about stock, purchase orders, shipments, or approved procedures. Changes and exceptions go to your team.' :
    'Ask about a product, policy, or order. I’ll use current sources and send anything uncertain for review.';
  $('composerHint').textContent = supply ?
    'Supply details require a signed staff session. Changes and exceptions go to a person.' :
    'Order details require a signed session. Decisions are based on current sources.';
  question.placeholder = supply ? 'Ask about stock, a PO, or a shipment…' : 'Ask about a product, policy, or order…';
  document.title = supply ? 'Supply chain workspace' : 'Store support workspace';
  addPrompts(supply);
}).catch(() => { addPrompts(false); });
