import { loadGlobalScript } from './vendor.js';

const TESSERACT_VERSION = '7.0.0';
const TESSERACT_CDN = `https://cdn.jsdelivr.net/npm/tesseract.js@${TESSERACT_VERSION}/dist`;
const CORE_CDN = 'https://cdn.jsdelivr.net/npm/tesseract.js-core@7.0.0';
let workerPromise;
let workerInstance;

function describeProgress(message) {
  if (!message || !message.status) return '';
  const percent = Number.isFinite(message.progress) ? ` ${Math.round(message.progress * 100)}%` : '';
  return `${message.status}${percent}`;
}

export async function getOcrEngine(onStatus = () => {}) {
  if (!workerPromise) {
    workerPromise = (async () => {
      onStatus('Loading Tesseract.js 7 and English/French OCR data. First use may take a little while...');
      const Tesseract = await loadGlobalScript(
        'Tesseract',
        `${TESSERACT_CDN}/tesseract.min.js`
      );
      const localRoot = new URL('.', import.meta.url).href.replace(/\/$/, '');

      // Tesseract.js uses a Blob URL worker by default. The worker script is
      // imported from the pinned CDN inside that same-origin Blob worker,
      // avoiding a direct cross-origin Worker constructor call.
      workerInstance = await Tesseract.createWorker(
        ['eng', 'fra'],
        Tesseract.OEM.LSTM_ONLY,
        {
          workerPath: `${TESSERACT_CDN}/worker.min.js`,
          corePath: CORE_CDN,
          langPath: localRoot,
          workerBlobURL: true,
          gzip: true,
          cacheMethod: 'write',
          cachePath: 'pdf-tools-tesseract-v7',
          logger: (message) => {
            const progress = describeProgress(message);
            if (progress) onStatus(`OCR: ${progress}`);
          },
          errorHandler: (error) => console.error('Tesseract worker error:', error)
        },
        {}
      );

      await workerInstance.setParameters({
        tessedit_pageseg_mode: '6',
        preserve_interword_spaces: '1',
        user_defined_dpi: '300'
      });
      onStatus('Tesseract.js 7 is ready (English + French).');
      return workerInstance;
    })().catch((error) => {
      workerPromise = null;
      workerInstance = null;
      throw new Error(`Tesseract initialization failed: ${error.message}`);
    });
  }
  return workerPromise;
}

function parseTsv(tsv) {
  if (typeof tsv !== 'string' || !tsv.trim()) return [];
  const lines = tsv.trim().split(/\r?\n/);
  const words = [];
  for (let i = 1; i < lines.length; i += 1) {
    const columns = lines[i].split('\t');
    if (columns.length < 12 || Number(columns[0]) !== 5) continue;
    const text = columns.slice(11).join('\t').trim();
    const left = Number(columns[6]);
    const top = Number(columns[7]);
    const width = Number(columns[8]);
    const height = Number(columns[9]);
    const confidence = Number(columns[10]);
    if (!text || ![left, top, width, height, confidence].every(Number.isFinite) || confidence < 0) continue;
    words.push({
      text,
      confidence,
      x0: left,
      y0: top,
      x1: left + width,
      y1: top + height
    });
  }
  return words;
}

/**
 * Recognize a browser canvas. psm is the Tesseract page segmentation mode;
 * whitelist is useful for targeted numeric-cell recovery.
 */
export async function recognizeCanvas(canvas, onStatus = () => {}, options = {}) {
  const worker = await getOcrEngine(onStatus);
  const psm = String(options.psm ?? 6);
  const whitelist = options.whitelist ?? '';
  await worker.setParameters({
    tessedit_pageseg_mode: psm,
    tessedit_char_whitelist: whitelist,
    preserve_interword_spaces: '1',
    user_defined_dpi: '300'
  });
  const { data } = await worker.recognize(canvas, {}, { tsv: true });
  return parseTsv(data?.tsv);
}

export async function terminateOcrEngine() {
  const worker = workerInstance;
  workerInstance = null;
  workerPromise = null;
  if (worker) {
    try { await worker.terminate(); } catch (error) { console.warn('Could not terminate OCR worker cleanly:', error); }
  }
}

window.addEventListener('pdf-tools-tool-change', () => {
  // Release the OCR worker when the user leaves the XLSX tool.
  // Any in-flight recognition is intentionally cancelled with the old tool state.
  terminateOcrEngine();
});
