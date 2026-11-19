import * as XLSX from 'https://esm.sh/xlsx@0.18.5?bundle';
import { renderPage, getNativeWords } from './pdf.js';
import { recognizeCanvas } from './ocr.js';

export function toggleColumn(columns, x, tolerance = 10) {
  const found = columns.findIndex((value) => Math.abs(value - x) <= tolerance);
  if (found >= 0) columns.splice(found, 1); else columns.push(x);
  return [...columns].sort((a, b) => a - b);
}


function prepareCellVariant(source, threshold = false) {
  const factor = 4;
  const border = 24;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.ceil(source.width * factor) + border * 2);
  canvas.height = Math.max(1, Math.ceil(source.height * factor) + border * 2);
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.filter = 'grayscale(1) contrast(1.25)';
  ctx.drawImage(source, border, border, source.width * factor, source.height * factor);
  ctx.filter = 'none';
  if (threshold) {
    const image = ctx.getImageData(border, border, canvas.width - border * 2, canvas.height - border * 2);
    for (let i = 0; i < image.data.length; i += 4) {
      const v = image.data[i] > 175 ? 255 : 0;
      image.data[i] = v; image.data[i + 1] = v; image.data[i + 2] = v;
    }
    ctx.putImageData(image, border, border);
  }
  return { canvas, factor, border };
}

async function recognizeCell(source, onProgress) {
  const first = prepareCellVariant(source, false);
  try {
    let found = await recognizeCanvas(first.canvas, onProgress);
    let best = found.sort((a, b) => b.confidence - a.confidence)[0];
    if (best && best.confidence >= 82) return best.text;
    const second = prepareCellVariant(source, true);
    try {
      found = await recognizeCanvas(second.canvas, onProgress);
      const thresholdBest = found.sort((a, b) => b.confidence - a.confidence)[0];
      if (!best || (thresholdBest && thresholdBest.confidence > best.confidence)) best = thresholdBest;
    } finally { second.canvas.width = 1; second.canvas.height = 1; }
    return best?.confidence >= 10 ? best.text : '';
  } finally { first.canvas.width = 1; first.canvas.height = 1; }
}

function groupWords(words, tolerance = 4) {
  const rows = [];
  for (const word of [...words].sort((a, b) => ((a.y0 + a.y1) / 2) - ((b.y0 + b.y1) / 2) || a.x0 - b.x0)) {
    const mid = (word.y0 + word.y1) / 2;
    let row = rows.find((r) => Math.abs(r.y - mid) <= tolerance);
    if (!row) { row = { y: mid, words: [] }; rows.push(row); }
    row.words.push(word);
    row.y = (row.y + mid) / 2;
  }
  return rows.sort((a, b) => a.y - b.y);
}

function assembleRows(words, boundaries, tolerance = 4) {
  const rows = groupWords(words, tolerance);
  const table = [];
  const centres = [];
  for (const row of rows) {
    const cells = Array(Math.max(0, boundaries.length - 1)).fill('');
    for (const word of row.words.sort((a, b) => a.x0 - b.x0)) {
      const mid = (word.x0 + word.x1) / 2;
      const col = boundaries.findIndex((left, i) => i < boundaries.length - 1 && left <= mid && mid < boundaries[i + 1]);
      if (col >= 0) cells[col] = `${cells[col]} ${word.text}`.trim();
    }
    if (cells.some((cell) => cell.trim())) { table.push(cells); centres.push(row.y); }
  }
  return { rows: table, centres };
}

function looksNumeric(value) {
  const text = String(value ?? '').trim().replace(/[ $€£]/g, '');
  return !!text && /^\(?-?\d[\d,]*(?:\.\d+)?%?\)?$/.test(text);
}

function numericColumns(rows) {
  const result = new Set();
  const width = Math.max(0, ...rows.map((row) => row.length));
  for (let col = 0; col < width; col++) {
    const values = rows.map((row) => row[col]?.trim()).filter(Boolean);
    if (values.length >= 2 && values.filter(looksNumeric).length / values.length >= 0.55) result.add(col);
  }
  return result;
}

function blankSurrounded(rows, r, c) {
  const row = rows[r];
  const left = c > 0 && !!row[c - 1]?.trim();
  const right = c + 1 < row.length && !!row[c + 1]?.trim();
  const above = r > 0 && !!rows[r - 1]?.[c]?.trim();
  const below = r + 1 < rows.length && !!rows[r + 1]?.[c]?.trim();
  return (left && right) || (above && below);
}

function cleanNumeric(value) {
  if (value == null || typeof value === 'number') return value;
  const text = String(value).trim();
  if (!text || /[A-Za-z]/.test(text) || /\d{1,4}[-/]\d{1,2}[-/]\d{1,4}/.test(text) || /\d+\s*-\s*\d+/.test(text)) return value;
  let cleaned = text, negative = false, percent = false;
  if (cleaned.startsWith('(') && cleaned.endsWith(')')) { negative = true; cleaned = cleaned.slice(1, -1).trim(); }
  cleaned = cleaned.replace(/[,$€£¥]/g, '').trim();
  if (cleaned.endsWith('%')) { percent = true; cleaned = cleaned.slice(0, -1).trim(); }
  if (cleaned.startsWith('-')) { negative = true; cleaned = cleaned.slice(1).trim(); }
  if (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(cleaned)) return value;
  let n = Number(cleaned) * (negative ? -1 : 1);
  if (percent) n /= 100;
  return Number.isInteger(n) && !percent && !cleaned.includes('.') ? n : n;
}

function normalizeNumericSpacing(rows) {
  return rows.map((row) => row.map((value) => {
    if (typeof value !== 'string' || /[A-Za-z]/.test(value)) return value;
    const compact = value.replace(/\s+/g, '');
    return looksNumeric(compact) ? compact : value;
  }));
}

function inferDecimalPlaces(rows) {
  const result = new Map();
  const width = Math.max(0, ...rows.map((row) => row.length));
  for (let col = 0; col < width; col++) {
    const counts = new Map();
    let total = 0;
    for (const row of rows) {
      const text = String(row[col] ?? '').replace(/,/g, '').trim();
      const match = text.match(/^\(?-?\d+\.(\d+)%?\)?$/);
      if (match) { const n = match[1].length; counts.set(n, (counts.get(n) || 0) + 1); total++; }
    }
    if (total >= 4) {
      const [places, count] = [...counts].sort((a, b) => b[1] - a[1])[0];
      if (count / total >= 0.7) result.set(col, places);
    }
  }
  return result;
}

function repairMissingDecimal(raw, places, references) {
  const text = String(raw ?? '').replace(/,/g, '').trim();
  if (!/^\d+$/.test(text) || text.length <= places || !references.length) return '';
  const proposed = `${text.slice(0, -places)}.${text.slice(-places)}`;
  const sorted = [...references].sort((a, b) => a - b);
  const median = sorted.length % 2 ? sorted[Math.floor(sorted.length / 2)] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  const max = Math.max(...sorted), min = Math.min(...sorted);
  const originalOutlier = Number(text) > Math.max(max * 5, median * 20);
  const plausible = Number(proposed) >= min / 100 && Number(proposed) <= Math.max(max * 5, median * 20);
  return originalOutlier && plausible ? proposed : '';
}

function numericColumnReferences(rows, col) {
  return rows.map((row) => {
    let text = String(row[col] ?? '').trim().replace(/,/g, '').replace(/%/g, '');
    if (!text.includes('.')) return null;
    if (text.startsWith('(') && text.endsWith(')')) text = `-${text.slice(1, -1)}`;
    const n = Number(text);
    return Number.isFinite(n) ? Math.abs(n) : null;
  }).filter((n) => n != null && n > 0);
}

async function recoverSuspiciousNumericCells(pdf, pageIndex, boundaries, rows, centres, pageWidth, pageHeight, onProgress) {
  const places = inferDecimalPlaces(rows);
  if (!places.size || !centres.length) return 0;
  const refs = new Map([...places.keys()].map((col) => [col, numericColumnReferences(rows, col)]));
  const rendered = await renderPage(pdf, pageIndex, 5, 0);
  const scaleX = rendered.canvas.width / pageWidth;
  const scaleY = rendered.canvas.height / pageHeight;
  let corrected = 0;
  for (let r = 0; r < rows.length; r++) {
    const centre = centres[r];
    const previous = centres[r - 1];
    const next = centres[r + 1];
    const half = previous == null && next == null ? 8 : previous == null ? Math.max(5, (next - centre) * .48) : next == null ? Math.max(5, (centre - previous) * .48) : Math.max(5, Math.min(centre - previous, next - centre) * .48);
    for (const [col, decimalPlaces] of places) {
      if (col >= rows[r].length) continue;
      const original = String(rows[r][col] ?? '').trim().replace(/,/g, '');
      if (!/^\d{5,}$/.test(original)) continue;
      const left = Math.max(0, Math.floor((boundaries[col] - 2) * scaleX));
      const right = Math.min(rendered.canvas.width, Math.ceil((boundaries[col + 1] + 2) * scaleX));
      const top = Math.max(0, Math.floor((centre - half) * scaleY));
      const bottom = Math.min(rendered.canvas.height, Math.ceil((centre + half) * scaleY));
      if (right - left < 4 || bottom - top < 4) continue;
      const crop = document.createElement('canvas'); crop.width = right - left; crop.height = bottom - top;
      crop.getContext('2d').drawImage(rendered.canvas, left, top, crop.width, crop.height, 0, 0, crop.width, crop.height);
      onProgress(`Validating numeric OCR on page ${pageIndex + 1}, row ${r + 1}, column ${col + 1}...`);
      const candidate = (await recognizeCell(crop, onProgress)).replace(/\s+/g, '');
      const exact = new RegExp(`^\\(?-?\\d+\\.\\d{${decimalPlaces}}%?\\)?$`).test(candidate);
      if (exact) { rows[r][col] = candidate; corrected++; continue; }
      const digits = /^\d+$/.test(candidate) ? candidate : original;
      const repaired = repairMissingDecimal(digits, decimalPlaces, refs.get(col) || []);
      if (repaired) { rows[r][col] = repaired; corrected++; }
    }
  }
  return corrected;
}

export async function generateWorkbook(pdf, columnsByPage, onProgress = () => {}) {
  const workbook = XLSX.utils.book_new();
  const usedOcrPages = [];
  let blankRecovered = 0;
  let numericCorrected = 0;
  let wroteSheet = false;
  for (let p = 0; p < pdf.numPages; p++) {
    const raw = columnsByPage.get(p) || [];
    if (!raw.length) continue;
    onProgress(`Extracting page ${p + 1} of ${pdf.numPages}...`);
    const page = await pdf.getPage(p + 1);
    const pageViewport = page.getViewport({ scale: 1 });
    const width = pageViewport.width;
    const height = pageViewport.height;
    const boundaries = [0, ...raw.map((x) => x / 1.5).sort((a, b) => a - b), width];
    let words = await getNativeWords(page);
    const usedOcr = words.length === 0;
    if (usedOcr) {
      usedOcrPages.push(p + 1);
      const rendered = await renderPage(pdf, p, 4, 0);
      const scaleX = rendered.canvas.width / width;
      const scaleY = rendered.canvas.height / height;
      const ocrWords = [];
      for (let c = 0; c < boundaries.length - 1; c++) {
        const left = Math.max(0, Math.floor(boundaries[c] * scaleX) - 12);
        const right = Math.min(rendered.canvas.width, Math.ceil(boundaries[c + 1] * scaleX) + 12);
        if (right <= left + 2) continue;
        const crop = document.createElement('canvas'); crop.width = right - left; crop.height = rendered.canvas.height;
        crop.getContext('2d').drawImage(rendered.canvas, left, 0, crop.width, crop.height, 0, 0, crop.width, crop.height);
        onProgress(`OCR page ${p + 1}, column ${c + 1} of ${boundaries.length - 1}...`);
        const enlarge = crop.width < 450 ? 1.75 : 1.5;
        const padded = document.createElement('canvas'); padded.width = Math.ceil(crop.width * enlarge) + 32; padded.height = Math.ceil(crop.height * enlarge) + 32;
        const paddedCtx = padded.getContext('2d'); paddedCtx.fillStyle = '#ffffff'; paddedCtx.fillRect(0, 0, padded.width, padded.height); paddedCtx.filter = 'grayscale(1) contrast(1.2)'; paddedCtx.drawImage(crop, 16, 16, crop.width * enlarge, crop.height * enlarge); paddedCtx.filter = 'none';
        const recognized = await recognizeCanvas(padded, onProgress);
        const trueLeft = boundaries[c] * scaleX;
        const trueRight = boundaries[c + 1] * scaleX;
        for (const item of recognized) {
          const localX0 = Math.max(0, (item.x0 - 16) / enlarge);
          const localX1 = Math.max(0, (item.x1 - 16) / enlarge);
          const centreX = left + (localX0 + localX1) / 2;
          // Keep the protective crop padding but reject text belonging to an adjacent column.
          if (!(trueLeft <= centreX && centreX < trueRight)) continue;
          const x0 = (left + localX0) / scaleX;
          const x1 = (left + localX1) / scaleX;
          const y0 = Math.max(0, (item.y0 - 16) / enlarge) / scaleY;
          const y1 = Math.max(0, (item.y1 - 16) / enlarge) / scaleY;
          ocrWords.push({ x0, x1, y0, y1, text: item.text, confidence: item.confidence, column: c });
        }
      }
      words = ocrWords;
    }
    let { rows, centres } = assembleRows(words, boundaries, usedOcr ? 6 : 4);
    if (usedOcr && rows.length) {
      // Targeted cell recovery: only inspect blank cells surrounded by values.
      const numericCols = numericColumns(rows);
      const rendered = await renderPage(pdf, p, 5, 0);
      const sx = rendered.canvas.width / width, sy = rendered.canvas.height / height;
      const rowCentres = centres;
      for (let r = 0; r < rows.length; r++) {
        const populated = rows[r].filter((v) => String(v).trim()).length;
        if (populated < 3) continue;
        for (let c = 0; c < rows[r].length; c++) {
          if (rows[r][c].trim() || !blankSurrounded(rows, r, c) || (c === 0 && !numericCols.has(c))) continue;
          const centre = rowCentres[r] ?? 0;
          const prev = rowCentres[r - 1], next = rowCentres[r + 1];
          const half = prev == null && next == null ? 8 : prev == null ? Math.max(5, (next - centre) * .48) : next == null ? Math.max(5, (centre - prev) * .48) : Math.max(5, Math.min(centre - prev, next - centre) * .48);
          const x0 = Math.max(0, Math.floor((boundaries[c] - 2) * sx));
          const x1 = Math.min(rendered.canvas.width, Math.ceil((boundaries[c + 1] + 2) * sx));
          const y0 = Math.max(0, Math.floor((centre - half) * sy));
          const y1 = Math.min(rendered.canvas.height, Math.ceil((centre + half) * sy));
          const crop = document.createElement('canvas'); crop.width = Math.max(1, x1 - x0); crop.height = Math.max(1, y1 - y0);
          crop.getContext('2d').drawImage(rendered.canvas, x0, y0, crop.width, crop.height, 0, 0, crop.width, crop.height);
          let candidate = await recognizeCell(crop, onProgress);
          if (numericCols.has(c)) { candidate = candidate.replace(/\s+/g, ''); if (!looksNumeric(candidate)) candidate = ''; }
          if (candidate) { rows[r][c] = candidate; blankRecovered++; }
        }
      }
      rows = normalizeNumericSpacing(rows);
      numericCorrected += await recoverSuspiciousNumericCells(pdf, p, boundaries, rows, centres, width, height, onProgress);
    }
    rows = normalizeNumericSpacing(rows);
    rows = rows.map((row) => row.map(cleanNumeric));
    const sheet = XLSX.utils.aoa_to_sheet(rows);
    XLSX.utils.book_append_sheet(workbook, sheet, `Page_${p + 1}`);
    wroteSheet = true;
  }
  if (!wroteSheet) XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([['No columns marked or no extractable text found.']]), 'Result');
  return { bytes: XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }), usedOcrPages, blankRecovered, numericCorrected };
}
