import { state } from './state.js';
import { makeToolShell, makeWorkspace, button, status, toast, createDownload } from './ui.js';
import { renderPage } from './pdf.js';
import { toggleColumn, generateWorkbook } from './xlsx.js';

export async function renderXlsx() {
  makeToolShell('xlsx');
  if (!state.pdf) return;
  const { workspace, left, right, window } = makeWorkspace();
  left.querySelector('.preview-heading').textContent = 'Column Markup';
  window.className = 'ruler-window';
  const page = state.pageIndex;
  const columns = state.toolState.xlsx.columns;
  if (!columns.has(page)) columns.set(page, []);
  const rendered = await renderPage(state.pdf, page, 1.5, 0);
  const content = document.createElement('div'); content.className = 'ruler-content';
  content.style.width = `${rendered.canvas.width}px`;
  content.style.minHeight = `${rendered.canvas.height + 70}px`;
  const ruler = document.createElement('div'); ruler.className = 'ruler'; ruler.style.width = `${rendered.canvas.width}px`;
  for (let x = 0; x < rendered.canvas.width; x += 10) {
    const tick = document.createElement('div'); tick.className = 'ruler-tick'; tick.style.left = `${x}px`; tick.style.height = `${x % 50 === 0 ? 25 : 10}px`; ruler.append(tick);
    if (x % 50 === 0) { const label = document.createElement('div'); label.className = 'ruler-label'; label.style.left = `${x + 3}px`; label.textContent = String(x); ruler.append(label); }
  }
  const imageWrap = document.createElement('div'); imageWrap.style.position = 'relative'; imageWrap.style.width = `${rendered.canvas.width}px`;
  rendered.canvas.className = 'ruler-image'; imageWrap.append(rendered.canvas);
  const lines = document.createElement('div'); lines.style.cssText = `position:absolute;inset:0;width:${rendered.canvas.width}px;height:${rendered.canvas.height}px;pointer-events:none`;
  imageWrap.append(lines); content.append(ruler, imageWrap); window.append(content);
  let suppressRulerClick = false;
  const beginDrag = (event, startX) => {
    event.preventDefault(); event.stopPropagation();
    suppressRulerClick = true;
    window.setPointerCapture(event.pointerId);
    let currentX = startX;
    const onMove = (move) => {
      const rect = ruler.getBoundingClientRect();
      const next = Math.max(0, Math.min(rendered.canvas.width - 1, Math.round(move.clientX - rect.left)));
      const list = columns.get(page); const idx = list.indexOf(currentX);
      if (idx >= 0) {
        const minX = idx > 0 ? list[idx - 1] + 5 : 0;
        const maxX = idx < list.length - 1 ? list[idx + 1] - 5 : rendered.canvas.width - 1;
        const bounded = Math.max(minX, Math.min(maxX, next));
        if (bounded !== currentX) { list[idx] = bounded; currentX = bounded; redrawLines(); updateList(); }
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); window.removeEventListener('pointercancel', onUp);
      setTimeout(() => { suppressRulerClick = false; }, 0);
    };
    window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp); window.addEventListener('pointercancel', onUp);
  };
  const redrawLines = () => {
    ruler.querySelectorAll('.column-line').forEach((n) => n.remove()); lines.replaceChildren();
    for (const x of columns.get(page)) {
      const marker = document.createElement('div'); marker.className = 'column-line'; marker.style.left = `${x - 2}px`; marker.title = `Drag to move boundary at ${x}px`;
      marker.addEventListener('pointerdown', (event) => beginDrag(event, x));
      ruler.append(marker);
      const imageLine = document.createElement('div'); imageLine.style.cssText = `position:absolute;left:${x - 2}px;top:0;height:100%;width:4px;background:blue;cursor:ew-resize;pointer-events:auto`;
      imageLine.title = `Drag to move boundary at ${x}px`; imageLine.addEventListener('pointerdown', (event) => beginDrag(event, x)); lines.append(imageLine);
    }
  };
  ruler.addEventListener('click', (event) => {
    if (suppressRulerClick || event.target.closest('.column-line')) return;
    const rect = ruler.getBoundingClientRect(); const x = Math.max(0, Math.min(rendered.canvas.width - 1, Math.round(event.clientX - rect.left)));
    columns.set(page, toggleColumn(columns.get(page), x)); redrawLines(); updateList();
  });
  const note = document.createElement('p'); note.className = 'xlsx-note'; note.textContent = `Page ${page + 1}: click the ruler to add/remove boundaries, or drag a blue boundary to reposition it. Pages without selectable text will use Tesseract.js automatically.`; left.append(note);
  const docHeading = document.createElement('div'); docHeading.className = 'panel-heading'; docHeading.textContent = 'Document Status'; right.append(docHeading);
  const docRows = [];
  for (let p = 0; p < state.pageCount; p++) { const row = document.createElement('div'); row.className = 'page-status'; row.textContent = `Page ${p + 1}: ${(columns.get(p) || []).length} column marker${(columns.get(p) || []).length === 1 ? '' : 's'}`; right.append(row); docRows.push(row); }
  const points = document.createElement('div'); points.className = 'page-status'; right.append(points);
  const updateList = () => { points.textContent = `Saved column points: ${columns.get(page).join(', ') || 'None'}`; for (let p = 0; p < docRows.length; p++) { const n = (columns.get(p) || []).length; docRows[p].textContent = `Page ${p + 1}: ${n} column marker${n === 1 ? '' : 's'}`; } };
  right.append(status('Click the ruler to add/remove column boundaries. Drag blue lines to reposition them.'));
  right.append(button('Clear Current Page', () => { columns.set(page, []); redrawLines(); updateList(); }));
  right.append(button('Generate XLSX', async () => {
    if (![...columns.values()].some((v) => v.length)) { toast('Mark at least one column boundary before generating the XLSX.'); return; }
    try {
      const progress = status('Starting extraction...'); right.append(progress);
      const output = await generateWorkbook(state.pdf, columns, (message) => { progress.textContent = message; });
      createDownload(output.bytes, 'extracted.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      const details = [];
      if (output.usedOcrPages.length) details.push(`Tesseract.js was used on page(s): ${output.usedOcrPages.join(', ')}.`);
      else details.push('The XLSX was generated from the PDF text layer; OCR was not needed.');
      if (output.blankRecovered) details.push(`Targeted OCR recovered ${output.blankRecovered} previously blank cell(s).`);
      if (output.numericCorrected) details.push(`Numeric validation corrected ${output.numericCorrected} suspicious cell(s).`);
      progress.textContent = details.join(' '); progress.className = 'status success';
      toast('XLSX generated.');
    } catch (error) { console.error(error); toast(`XLSX generation failed: ${error.message}`, 7000); }
  }, 'primary'));
  redrawLines(); updateList();
}
