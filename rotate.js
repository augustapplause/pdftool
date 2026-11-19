import { state } from './state.js';
import { makeToolShell, makeWorkspace, button, status, toast, createDownload } from './ui.js';
import { showCanvas } from './ui.js';
import { rotatePdf } from './pdf.js';

export async function renderRotate() {
  makeToolShell('rotate'); if (!state.pdf) return;
  const page = state.pageIndex; const rotations = state.toolState.rotate.angles;
  if (!rotations.has(page)) rotations.set(page, 0);
  const angle = rotations.get(page);
  const { inner, right } = makeWorkspace();
  await showCanvas(inner, state.pdf, page, angle);
  right.append(document.createElement('div'));
  right.lastChild.className = 'panel-heading'; right.lastChild.textContent = 'Rotation Controls';
  right.append(button('90 degrees left', () => { rotations.set(page, (rotations.get(page) - 90 + 360) % 360); renderRotate(); }));
  right.append(button('90 degrees right', () => { rotations.set(page, (rotations.get(page) + 90) % 360); renderRotate(); }));
  right.append(status(`Page ${page + 1} of ${state.pageCount} - current rotation: ${angle} degrees clockwise`));
  const changed = [...rotations].filter(([, v]) => v % 360 !== 0).map(([p, v]) => `Page ${p + 1}: ${v} degrees`).join(', ');
  right.append(status(changed ? `Saved rotations - ${changed}` : 'No page rotations have been applied yet.'));
  right.append(button('Generate Rotated PDF', async () => {
    try { const bytes = await rotatePdf(state.bytes, rotations); const base = (state.file?.name || 'document.pdf').replace(/\.pdf$/i, '') || 'document'; createDownload(bytes, `${base}_rotated.pdf`, 'application/pdf'); toast('Rotated PDF generated.'); }
    catch (error) { toast(`Could not rotate PDF: ${error.message}`); }
  }, 'primary'));
}
