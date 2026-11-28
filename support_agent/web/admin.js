const el = (id) => document.getElementById(id);
let adminKey = '';

fetch('/ui/config', { cache: 'no-store' }).then((response) => response.json()).then((config) => {
  if (config.domain !== 'supply_chain') return;
  document.title = 'Supply documents · Customer Agent';
  el('adminDescription').textContent = 'Add approved procurement and logistics guidance. Inventory, purchase orders, and shipment status continue to come from current operational records.';
}).catch(() => {});

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}), 'X-Admin-Key': adminKey };
  const response = await fetch(path, { ...options, headers, cache: 'no-store' });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Request failed.');
  return payload;
}

function renderDocuments(documents) {
  const container = el('documents');
  container.replaceChildren();
  if (!documents.length) {
    const empty = document.createElement('p');
    empty.textContent = 'No documents yet. Add approved guidance to begin.';
    container.append(empty);
    return;
  }
  for (const documentInfo of documents) {
    const row = document.createElement('div');
    row.className = 'document-row';
    const details = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = documentInfo.name;
    const meta = document.createElement('span');
    meta.textContent = `${documentInfo.chunk_count} passages · Updated ${new Date(documentInfo.uploaded_at).toLocaleString()}`;
    details.append(title, meta);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'secondary-button';
    remove.textContent = 'Remove';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`Remove ${documentInfo.name} from the knowledge base?`)) return;
      try {
        await request(`/admin/documents/${documentInfo.id}`, { method: 'DELETE' });
        await refresh();
      } catch (error) { el('accessStatus').textContent = error.message; }
    });
    row.append(details, remove);
    container.append(row);
  }
}

async function refresh() {
  try {
    const data = await request('/admin/documents');
    renderDocuments(data.documents);
    el('accessStatus').textContent = 'Admin access active.';
  } catch (error) { el('accessStatus').textContent = error.message; }
}

el('unlock').addEventListener('click', () => {
  adminKey = el('adminKey').value.trim();
  el('adminKey').value = '';
  if (adminKey) refresh();
});
el('refresh').addEventListener('click', refresh);
el('uploadForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = el('documentFile').files[0];
  if (!adminKey) { el('uploadStatus').textContent = 'Unlock admin access first.'; return; }
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) { el('uploadStatus').textContent = 'File must be 5 MB or smaller.'; return; }
  el('uploadStatus').textContent = 'Uploading and indexing…';
  try {
    const documentInfo = await request('/admin/documents', {
      method: 'POST', body: file,
      headers: { 'X-Document-Name': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' }
    });
    el('uploadStatus').textContent = `${documentInfo.name} is ready for questions.`;
    el('uploadForm').reset();
    await refresh();
  } catch (error) { el('uploadStatus').textContent = error.message; }
});
