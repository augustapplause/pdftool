import { TOOL_LABELS } from './config.js';
import { state } from './state.js';

export const main = document.getElementById('main-content');

export function toast(message, duration = 3600) {
  const node = document.createElement('div');
  node.className = 'toast'; node.textContent = message;
  document.getElementById('toast-region').append(node);
  setTimeout(() => node.remove(), duration);
}

export function status(message, type = 'info') {
  const node = document.createElement('div'); node.className = `status ${type}`; node.textContent = message; return node;
}

export function button(label, action, kind = '') {
  const node = document.createElement('button'); node.className = `button ${kind}`; node.type = 'button'; node.textContent = label;
  if (action) node.addEventListener('click', action); return node;
}

export function selectPage(onChange, value = state.pageIndex) {
  const select = document.createElement('select'); select.className = 'page-select';
  for (let i = 0; i < state.pageCount; i++) {
    const option = document.createElement('option'); option.value = String(i); option.textContent = `Page ${i + 1}`; option.selected = i === value; select.append(option);
  }
  select.addEventListener('change', () => onChange(Number(select.value))); return select;
}

export function makeToolShell(tool, hasUpload = true) {
  main.innerHTML = '';
  const header = document.createElement('div'); header.className = 'tool-header';
  const title = document.createElement('h2'); title.textContent = TOOL_LABELS[tool]; header.append(title);
  if (state.pdf) header.append(selectPage((page) => { state.pageIndex = page; window.dispatchEvent(new Event('pdf-page-change')); }));
  main.append(header);
  if (!state.pdf && hasUpload) {
    const panel = document.createElement('section'); panel.className = 'upload-panel';
    const label = document.createElement('label'); label.className = 'upload-label'; label.textContent = 'Upload PDF'; label.htmlFor = 'pdf-upload';
    const input = document.createElement('input'); input.id = 'pdf-upload'; input.className = 'file-input'; input.type = 'file'; input.accept = 'application/pdf,.pdf';
    panel.append(label, input, status(`Upload a PDF to begin: ${TOOL_LABELS[tool]}.`)); main.append(panel);
    return input;
  }
  return null;
}

export function makeWorkspace() {
  const workspace = document.createElement('div'); workspace.className = 'workspace';
  const left = document.createElement('section'); const right = document.createElement('aside');
  const heading = document.createElement('div'); heading.className = 'preview-heading'; heading.textContent = 'PDF Preview';
  const window = document.createElement('div'); window.className = 'preview-window';
  const inner = document.createElement('div'); inner.className = 'preview-inner'; window.append(inner); left.append(heading, window);
  right.className = 'tool-panel'; right.innerHTML = '<div class="panel-heading">Page Tools</div>';
  workspace.append(left, right); main.append(workspace);
  return { workspace, left, right, window, inner };
}

export async function showCanvas(inner, pdf, pageIndex, rotation = 0, zoom = 1.5) {
  const { renderPage } = await import('./pdf.js');
  const rendered = await renderPage(pdf, pageIndex, zoom, rotation);
  inner.replaceChildren(rendered.canvas);
  rendered.canvas.className = 'preview-canvas';
  return rendered;
}

export function createDownload(bytes, filename, mime) {
  const blob = bytes instanceof Blob ? bytes : new Blob([bytes], { type: mime });
  const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
