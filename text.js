import { state } from './state.js';
import { makeToolShell, makeWorkspace, button, status, toast, createDownload, showCanvas } from './ui.js';

export async function renderText() {
  makeToolShell('text'); if (!state.pdf) return;
  const { left, right, inner } = makeWorkspace();
  left.querySelector('.preview-heading').textContent = 'Page Preview';
  await showCanvas(inner, state.pdf, state.pageIndex);
  const info = status('This extracts selectable text from every page and saves it as a plain .txt file. Scanned image-only PDFs may produce little or no text because OCR is not enabled.', 'info'); right.append(info);
  right.append(button('Generate Plain Text File', async () => {
    try {
      const parts = [];
      for (let i = 1; i <= state.pageCount; i++) {
        const page = await state.pdf.getPage(i); const content = await page.getTextContent();
        const text = content.items.map((item) => `${item.str || ''}${item.hasEOL ? '\n' : ' '}`).join('').replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').trim();
        parts.push(`===== Page ${i} =====\n${text}\n`);
      }
      createDownload(new Blob([parts.join('\n')], { type: 'text/plain;charset=utf-8' }), 'extracted_text.txt', 'text/plain'); toast('Plain text file generated.');
    } catch (error) { toast(`Text extraction failed: ${error.message}`); }
  }, 'primary'));
}
