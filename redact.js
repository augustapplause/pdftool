import { state } from './state.js';
import { makeToolShell, makeWorkspace, showCanvas, button, status, toast, createDownload } from './ui.js';
import { renderPage, makeRasterPdf } from './pdf.js';

export async function renderRedact() {
  makeToolShell('redact');
  if (!state.pdf) return;
  const { inner, right } = makeWorkspace();
  const page = state.pageIndex;
  const rendered = await showCanvas(inner, state.pdf, page);
  const data = state.toolState.redact;
  if (!data.boxes.has(page)) data.boxes.set(page, []);
  const layer = document.createElement('div'); layer.className = 'preview-overlay';
  layer.style.width = `${rendered.canvas.width}px`; layer.style.height = `${rendered.canvas.height}px`;
  inner.append(layer);
  const draw = () => {
    layer.replaceChildren();
    for (const box of data.boxes.get(page)) {
      const el = document.createElement('div'); el.className = 'redaction-box';
      Object.assign(el.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` }); layer.append(el);
    }
    const first = data.first.get(page);
    if (first) { const dot = document.createElement('div'); dot.className = 'first-corner'; dot.style.left = `${first.x}px`; dot.style.top = `${first.y}px`; layer.append(dot); }
  };
  layer.addEventListener('click', (event) => {
    const rect = layer.getBoundingClientRect();
    const p = { x: Math.round(event.clientX - rect.left), y: Math.round(event.clientY - rect.top) };
    const first = data.first.get(page);
    if (!first) { data.first.set(page, p); }
    else {
      const x = Math.min(first.x, p.x), y = Math.min(first.y, p.y);
      const w = Math.abs(p.x - first.x), h = Math.abs(p.y - first.y);
      if (w >= 5 && h >= 5) data.boxes.get(page).push({ x, y, w, h });
      data.first.delete(page);
    }
    draw(); updateStatus();
  });
  const docHeading = document.createElement('div'); docHeading.className = 'panel-heading'; docHeading.textContent = 'Document Status'; right.append(docHeading);
  const docRows = [];
  for (let p = 0; p < state.pageCount; p++) { const row = document.createElement('div'); row.className = 'page-status'; row.textContent = `Page ${p + 1}: ${(data.boxes.get(p) || []).length} redaction${(data.boxes.get(p) || []).length === 1 ? '' : 's'}`; right.append(row); docRows.push(row); }
  const note = document.createElement('p'); note.textContent = 'Click two opposite corners to create each redaction rectangle. Saved boxes stay visible.'; note.className = 'muted'; right.append(note);
  const count = document.createElement('p'); count.className = 'page-status'; right.append(count);
  const updateStatus = () => { count.textContent = `Saved redaction rectangles: ${data.boxes.get(page).length}`; for (let p = 0; p < docRows.length; p++) { const n = (data.boxes.get(p) || []).length; docRows[p].textContent = `Page ${p + 1}: ${n} redaction${n === 1 ? '' : 's'}`; } };
  right.append(button('Clear Current Page', () => { data.boxes.set(page, []); data.first.delete(page); draw(); updateStatus(); }));
  right.append(button('Undo Last Redaction', () => { data.boxes.get(page).pop(); draw(); updateStatus(); }));
  right.append(button('Cancel Current Rectangle', () => { data.first.delete(page); draw(); updateStatus(); }));
  right.append(status(data.first.has(page) ? 'First corner selected. Click the opposite corner.' : 'Ready for redaction markup.'));
  right.append(button('Generate Redacted PDF', async () => {
    try {
      toast('Applying permanent redactions page by page...');
      const bytes = await makeRasterPdf(state.pdf, data.boxes, 1.5, 2);
      createDownload(bytes, 'redacted.pdf', 'application/pdf');
      toast('Redacted PDF generated. Pages are rasterized so covered content is no longer recoverable.');
    } catch (error) { console.error(error); toast(`Could not generate redacted PDF: ${error.message}`); }
  }, 'primary'));
  draw(); updateStatus();
}
