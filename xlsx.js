import { loadGlobalScript } from './vendor.js';
import { renderPage, getNativeWords } from './pdf.js';
import { recognizeCanvas } from './ocr.js';

const OCR_ZOOM = 5.5;
const FIRST_PASS_MIN_CONFIDENCE = 10; // Matches the supplied Python implementation.
const LOW_CONFIDENCE_REVIEW = 45;
const MIN_RECHECK_CONFIDENCE = 35;

export function toggleColumn(columns, x, tolerance = 10) {
  const found = columns.findIndex((value) => Math.abs(value - x) <= tolerance);
  if (found >= 0) columns.splice(found, 1); else columns.push(x);
  return [...columns].sort((a, b) => a - b);
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** Grayscale histogram autocontrast with a tiny 0.1% trim to ignore isolated scan speckles. */
function makeAutocontrastCanvas(source, enlargement = 1, border = 16, thresholdMode = 'none') {
  const scaledWidth = Math.max(1, Math.ceil(source.width * enlargement));
  const scaledHeight = Math.max(1, Math.ceil(source.height * enlargement));
  const work = document.createElement('canvas');
  work.width = scaledWidth;
  work.height = scaledHeight;
  const workCtx = work.getContext('2d', { willReadFrequently: true });
  workCtx.imageSmoothingEnabled = true;
  workCtx.imageSmoothingQuality = 'high';
  workCtx.drawImage(source, 0, 0, scaledWidth, scaledHeight);

  const image = workCtx.getImageData(0, 0, scaledWidth, scaledHeight);
  const data = image.data;
  const histogram = new Uint32Array(256);
  let min = 255, max = 0;
  for (let i = 0; i < data.length; i += 4) {
    const gray = Math.round(data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114);
    data[i] = gray; data[i + 1] = gray; data[i + 2] = gray;
    histogram[gray] += 1;
    if (gray < min) min = gray;
    if (gray > max) max = gray;
  }
  // Ignore only extreme isolated speckles while retaining the original content range.
  const total = scaledWidth * scaledHeight;
  const clip = Math.floor(total * 0.001);
  let cumulative = 0, low = 0, high = 255;
  for (let i = 0; i < 256; i += 1) { cumulative += histogram[i]; if (cumulative > clip) { low = i; break; } }
  cumulative = 0;
  for (let i = 255; i >= 0; i -= 1) { cumulative += histogram[i]; if (cumulative > clip) { high = i; break; } }
  if (high <= low) { low = min; high = max; }
  const span = Math.max(1, high - low);
  for (let i = 0; i < data.length; i += 4) {
    let gray = Math.max(0, Math.min(255, Math.round((data[i] - low) * 255 / span)));
    if (thresholdMode === 'global') gray = gray > 175 ? 255 : 0;
    data[i] = gray; data[i + 1] = gray; data[i + 2] = gray; data[i + 3] = 255;
  }
  workCtx.putImageData(image, 0, 0);

  if (thresholdMode === 'adaptive') applyAdaptiveThreshold(workCtx, scaledWidth, scaledHeight);

  const output = document.createElement('canvas');
  output.width = scaledWidth + border * 2;
  output.height = scaledHeight + border * 2;
  const out = output.getContext('2d');
  out.fillStyle = '#ffffff'; out.fillRect(0, 0, output.width, output.height);
  out.drawImage(work, border, border);
  work.width = 1; work.height = 1;
  return { canvas: output, enlargement, border };
}

/** Bradley-style local thresholding for difficult cell crops; never used as the sole first pass. */
function applyAdaptiveThreshold(ctx, width, height) {
  const image = ctx.getImageData(0, 0, width, height);
  const src = image.data;
  const stride = width + 1;
  const integral = new Float64Array((width + 1) * (height + 1));
  for (let y = 1; y <= height; y += 1) {
    let rowSum = 0;
    for (let x = 1; x <= width; x += 1) {
      rowSum += src[((y - 1) * width + x - 1) * 4];
      integral[y * stride + x] = integral[(y - 1) * stride + x] + rowSum;
    }
  }
  const radius = Math.max(8, Math.round(Math.min(width, height) * 0.09));
  for (let y = 0; y < height; y += 1) {
    const y0 = Math.max(0, y - radius), y1 = Math.min(height, y + radius + 1);
    for (let x = 0; x < width; x += 1) {
      const x0 = Math.max(0, x - radius), x1 = Math.min(width, x + radius + 1);
      const sum = integral[y1 * stride + x1] - integral[y0 * stride + x1] - integral[y1 * stride + x0] + integral[y0 * stride + x0];
      const mean = sum / ((x1 - x0) * (y1 - y0));
      const index = (y * width + x) * 4;
      const value = src[index] < mean * 0.92 ? 0 : 255;
      src[index] = value; src[index + 1] = value; src[index + 2] = value; src[index + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
}

function parseNumeric(value) {
  let text = String(value ?? '').trim().replace(/\s+/g, '').replace(/,/g, '');
  if (!text || /[A-Za-z]/.test(text)) return null;
  if (text.startsWith('=') || text.startsWith('|')) text = text.slice(1);
  let negative = false;
  if (text.startsWith('(') && text.endsWith(')')) { negative = true; text = text.slice(1, -1); }
  const percent = text.endsWith('%');
  if (percent) text = text.slice(0, -1);
  text = text.replace(/^[$€£¥]/, '');
  if (!/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)) return null;
  let number = Number(text) * (negative ? -1 : 1);
  if (percent) number /= 100;
  return Number.isFinite(number) ? number : null;
}

function looksNumeric(value) { return parseNumeric(value) != null; }

function cleanOcrArtifact(value) {
  if (value == null || typeof value !== 'string') return value;
  const text = value.trim();
  if (!text) return value;
  // Remove a leading OCR punctuation artefact only when the remainder is a valid numeric token.
  if (/^[=|][\s]*/.test(text)) {
    const candidate = text.replace(/^[=|][\s]*/, '');
    if (looksNumeric(candidate)) return candidate;
  }
  return text;
}

function cleanNumeric(value) {
  if (value == null || typeof value === 'number') return value;
  const text = cleanOcrArtifact(value);
  if (typeof text !== 'string' || !text.trim()) return value;
  if (/[A-Za-z]/.test(text) || /\d{1,4}[-/]\d{1,2}[-/]\d{1,4}/.test(text) || /\d+\s*-\s*\d+/.test(text)) return text;
  let cleaned = text.trim(), negative = false, percent = false;
  if (cleaned.startsWith('(') && cleaned.endsWith(')')) { negative = true; cleaned = cleaned.slice(1, -1).trim(); }
  cleaned = cleaned.replace(/[,$€£¥]/g, '').trim();
  if (cleaned.endsWith('%')) { percent = true; cleaned = cleaned.slice(0, -1).trim(); }
  if (cleaned.startsWith('-')) { negative = true; cleaned = cleaned.slice(1).trim(); }
  if (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(cleaned)) return text;
  let number = Number(cleaned) * (negative ? -1 : 1);
  if (percent) number /= 100;
  return Number.isInteger(number) && !percent && !cleaned.includes('.') ? number : number;
}

function makeColumnCrop(pageCanvas, pixelLeft, pixelRight) {
  const width = Math.max(1, pixelRight - pixelLeft);
  const crop = document.createElement('canvas');
  crop.width = width;
  crop.height = pageCanvas.height;
  crop.getContext('2d').drawImage(pageCanvas, pixelLeft, 0, width, pageCanvas.height, 0, 0, width, pageCanvas.height);
  return crop;
}

function groupWords(words, tolerance = 4.0) {
  const rows = [];
  const sorted = [...words].sort((a, b) => ((a.y0 + a.y1) / 2) - ((b.y0 + b.y1) / 2) || a.x0 - b.x0);
  for (const word of sorted) {
    const mid = (word.y0 + word.y1) / 2;
    let best = null, bestDistance = Infinity;
    for (const row of rows) {
      const distance = Math.abs(row.anchor - mid);
      // A fixed anchor/median avoids cumulative drift as words from many columns are added.
      if (distance <= tolerance && distance < bestDistance) { best = row; bestDistance = distance; }
    }
    if (!best) { best = { ys: [], words: [], anchor: mid }; rows.push(best); }
    best.ys.push(mid); best.words.push(word); best.anchor = median(best.ys);
  }
  return rows.sort((a, b) => a.anchor - b.anchor).map((row) => ({ y: row.anchor, words: row.words }));
}

function assembleRows(words, boundaries, tolerance = 4.0) {
  const grouped = groupWords(words, tolerance);
  const table = [], centres = [], confidences = [];
  for (const row of grouped) {
    const cells = Array(Math.max(0, boundaries.length - 1)).fill('');
    const cellScores = cells.map(() => []);
    for (const word of [...row.words].sort((a, b) => a.x0 - b.x0)) {
      const mid = (word.x0 + word.x1) / 2;
      const col = boundaries.findIndex((left, i) => i < boundaries.length - 1 && left <= mid && mid < boundaries[i + 1]);
      if (col >= 0) {
        cells[col] = `${cells[col]} ${word.text}`.trim();
        if (Number.isFinite(word.confidence)) cellScores[col].push(word.confidence);
        else cellScores[col].push(100);
      }
    }
    if (cells.some((cell) => cell.trim())) {
      table.push(cells);
      centres.push(row.y);
      confidences.push(cellScores.map((scores) => scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null));
    }
  }
  return { rows: table, centres, confidences };
}

function numericColumns(rows) {
  const result = new Set();
  const width = Math.max(0, ...rows.map((row) => row.length));
  for (let col = 0; col < width; col += 1) {
    const values = rows.map((row) => String(row[col] ?? '').trim()).filter(Boolean);
    if (values.length >= 2 && values.filter(looksNumeric).length / values.length >= 0.55) result.add(col);
  }
  return result;
}

function inferDecimalPlaces(rows) {
  const result = new Map();
  const width = Math.max(0, ...rows.map((row) => row.length));
  for (let col = 0; col < width; col += 1) {
    const counts = new Map(); let total = 0;
    for (const row of rows) {
      const text = String(row[col] ?? '').replace(/,/g, '').trim();
      const match = text.match(/^\(?-?\d+\.(\d+)%?\)?$/);
      if (match) { const places = match[1].length; counts.set(places, (counts.get(places) || 0) + 1); total += 1; }
    }
    if (total >= 4) {
      const [places, count] = [...counts].sort((a, b) => b[1] - a[1])[0];
      if (count / total >= 0.7) result.set(col, places);
    }
  }
  return result;
}

function decimalReferences(rows, col) {
  return rows.map((row) => parseNumeric(row[col])).filter((value) => value != null && Math.abs(value) > 0).map(Math.abs);
}

function repairMissingDecimal(rawValue, decimalPlaces, references) {
  const text = String(rawValue ?? '').replace(/,/g, '').trim();
  if (!/^\d+$/.test(text) || text.length <= decimalPlaces || text.length < decimalPlaces + 1 || references.length < 4) return '';
  const proposed = `${text.slice(0, -decimalPlaces)}.${text.slice(-decimalPlaces)}`;
  const sorted = [...references].sort((a, b) => a - b);
  const med = median(sorted), max = Math.max(...sorted), min = Math.min(...sorted);
  const upper = Math.max(max * 5, med * 20);
  const originalOutlier = Number(text) > upper;
  const proposedPlausible = Number(proposed) >= min / 100 && Number(proposed) <= upper;
  return originalOutlier && proposedPlausible ? proposed : '';
}

function rowBounds(centres, index) {
  const current = centres[index];
  const prev = centres[index - 1];
  const next = centres[index + 1];
  if (prev == null && next == null) return [Math.max(0, current - 8), current + 8];
  const gapBefore = prev == null ? Infinity : current - prev;
  const gapAfter = next == null ? Infinity : next - current;
  const half = Math.max(4.5, Math.min(gapBefore, gapAfter) * 0.46);
  return [Math.max(0, current - half), current + half];
}

function extractCandidate(words, numericOnly) {
  let candidate = words.map((word) => word.text).join(' ').trim();
  candidate = cleanOcrArtifact(candidate);
  if (numericOnly) {
    candidate = candidate.replace(/\s+/g, '');
    if (!looksNumeric(candidate)) return { text: '', confidence: 0 };
  }
  const confidence = words.length ? words.reduce((sum, word) => sum + word.confidence, 0) / words.length : 0;
  return { text: candidate, confidence };
}

async function recognizeCell(source, onProgress, numericOnly = false, expectedDecimalPlaces = null) {
  const whitelist = numericOnly ? '0123456789.,()%-$' : '';
  const variants = [];
  const candidates = [];
  try {
    variants.push(makeAutocontrastCanvas(source, 4, 24, 'none'));
    variants.push(makeAutocontrastCanvas(source, 4, 24, 'adaptive'));
    for (const variant of variants) {
      for (const psm of [7, 8, 13]) {
        const words = await recognizeCanvas(variant.canvas, onProgress, { psm, whitelist, minConfidence: 0 });
        const found = extractCandidate(words, numericOnly);
        if (!found.text) continue;
        let score = found.confidence;
        let exactDecimal = false;
        if (numericOnly && expectedDecimalPlaces != null) {
          exactDecimal = new RegExp(`^\\(?-?\\d[\\d,]*\\.\\d{${expectedDecimalPlaces}}%?\\)?$`).test(found.text);
          if (exactDecimal) score += 25;
        }
        candidates.push({ ...found, score, exactDecimal });
      }
    }
    candidates.sort((a, b) => b.score - a.score || b.confidence - a.confidence);
    return candidates[0] || { text: '', confidence: 0, score: 0, exactDecimal: false };
  } finally {
    for (const variant of variants) { variant.canvas.width = 1; variant.canvas.height = 1; }
  }
}

function identifyPlanSummaryProfile(rows) {
  const allText = rows.flat().join(' ').toLowerCase().replace(/[^a-z]/g, '');
  const reportTitle = allText.includes('plan') && allText.includes('report') && /summ|sumn|mary/.test(allText);
  return reportTitle && rows.some((row) => String(row[0] ?? '').trim().toLowerCase() === 'code');
}

function collectOcrReview(rows, confidences, numericCols, decimalPlaces, pageNumber) {
  const review = [];
  for (let r = 0; r < rows.length; r += 1) {
    for (let c = 0; c < rows[r].length; c += 1) {
      const value = rows[r][c];
      if (!String(value ?? '').trim()) continue;
      const confidence = confidences[r]?.[c];
      const reasons = [];
      if (confidence != null && confidence < LOW_CONFIDENCE_REVIEW) reasons.push('Low OCR confidence');
      if (numericCols.has(c)) {
        const cleaned = cleanOcrArtifact(String(value));
        if (!looksNumeric(cleaned)) reasons.push('Non-numeric OCR result in a predominantly numeric column');
        const places = decimalPlaces.get(c);
        const compact = String(cleaned).replace(/,/g, '').replace(/%$/, '');
        if (places != null && /^\d{3,}$/.test(compact)) reasons.push(`Possible missing decimal punctuation (column pattern: ${places} decimal places)`);
      }
      if (reasons.length) review.push({ page: pageNumber, row: r + 1, column: c + 1, value: String(value), confidence: confidence == null ? '' : Number(confidence.toFixed(1)), reason: reasons.join('; ') });
    }
  }
  return review;
}

function planSummaryArithmeticDiagnostics(rows, pageNumber) {
  // This optional profile only emits warnings. It never edits or replaces source OCR values.
  if (!identifyPlanSummaryProfile(rows)) return [];
  const warnings = [];
  const excludedLabels = /^(prior|net|grand|reversed|unadjudicated|plan summary|code|)$/i;
  for (let r = 0; r < rows.length; r += 1) {
    const row = rows[r];
    const code = String(row[0] ?? '').trim();
    if (!code || excludedLabels.test(code) || !/^[a-z]{2,8}$/i.test(code)) continue;
    const aac = parseNumeric(row[2]);
    const costMarkup = parseNumeric(row[3]);
    const effectiveMarkup = parseNumeric(row[4]);
    const fees = parseNumeric(row[6]);
    const totalDollars = parseNumeric(row[7]);
    const gp = parseNumeric(row[8]);
    const newCount = parseNumeric(row[11]);
    const repeatCount = parseNumeric(row[12]);
    const totalCount = parseNumeric(row[13]);
    const check = (label, actual, expected, tolerance) => {
      if (actual == null || expected == null || !Number.isFinite(actual) || !Number.isFinite(expected)) return;
      if (Math.abs(actual - expected) > tolerance) warnings.push({ page: pageNumber, row: r + 1, column: '', value: code, confidence: '', reason: `Arithmetic diagnostic only: ${label}; observed difference ${Number((actual - expected).toFixed(3))}. No cell values changed.` });
    };
    if ([aac, costMarkup, effectiveMarkup].every((value) => value != null)) check('A.A.C. + effective markup vs. Cost + MkUp', costMarkup, aac + effectiveMarkup, Math.max(0.03, Math.abs(costMarkup) * 0.0001));
    if ([costMarkup, fees, totalDollars].every((value) => value != null)) check('Cost + MkUp + Fees vs. Total', totalDollars, costMarkup + fees, Math.max(0.03, Math.abs(totalDollars) * 0.0001));
    if ([effectiveMarkup, fees, totalDollars, gp].every((value) => value != null) && totalDollars !== 0) check('Derived margin vs. G.P.%', gp, ((effectiveMarkup + fees) / totalDollars) * 100, 0.15);
    if ([newCount, repeatCount, totalCount].every((value) => value != null)) check('New + Repeat vs. Total prescription count', totalCount, newCount + repeatCount, 0);
  }
  return warnings;
}

async function recheckCells(pdf, pageIndex, boundaries, rows, centres, confidences, pageWidth, pageHeight, onProgress) {
  const numericCols = numericColumns(rows);
  const decimalPlaces = inferDecimalPlaces(rows);
  const refs = new Map([...decimalPlaces.keys()].map((col) => [col, decimalReferences(rows, col)]));
  const rendered = await renderPage(pdf, pageIndex, OCR_ZOOM, 0);
  const scaleX = rendered.canvas.width / pageWidth;
  const scaleY = rendered.canvas.height / pageHeight;
  let recoveredBlank = 0;
  let rechecked = 0;
  try {
    for (let r = 0; r < rows.length; r += 1) {
      const populated = rows[r].filter((value) => String(value).trim()).length;
      if (populated < 3) continue;
      const [topPdf, bottomPdf] = rowBounds(centres, r);
      for (let c = 0; c < rows[r].length; c += 1) {
        const original = String(rows[r][c] ?? '').trim();
        const confidence = confidences[r]?.[c] ?? null;
        const numeric = numericCols.has(c);
        const places = decimalPlaces.get(c);
        const compact = original.replace(/,/g, '').replace(/%$/, '');
        const isPotentialMissingDecimal = numeric && places != null && /^\d{3,}$/.test(compact) && repairMissingDecimal(compact, places, refs.get(c) || []);
        const isSuspiciousNumeric = numeric && (!looksNumeric(original) || (places != null && /^\d{3,}$/.test(compact)));
        const hasHorizontalSupport = c > 0 && !!rows[r][c - 1]?.trim() && c + 1 < rows[r].length && !!rows[r][c + 1]?.trim();
        const hasVerticalSupport = r > 0 && !!rows[r - 1]?.[c]?.trim() && r + 1 < rows.length && !!rows[r + 1]?.[c]?.trim();
        const isSurroundedBlank = !original && (hasHorizontalSupport || hasVerticalSupport);
        const isLowConfidence = !!original && confidence != null && confidence < LOW_CONFIDENCE_REVIEW;
        if (!(isSurroundedBlank || isLowConfidence || isSuspiciousNumeric)) continue;
        if (isSurroundedBlank && c === 0 && !numeric) continue;

        const left = Math.max(0, Math.floor((boundaries[c] - 2.5) * scaleX));
        const right = Math.min(rendered.canvas.width, Math.ceil((boundaries[c + 1] + 2.5) * scaleX));
        const top = Math.max(0, Math.floor(topPdf * scaleY));
        const bottom = Math.min(rendered.canvas.height, Math.ceil(bottomPdf * scaleY));
        if (right - left < 4 || bottom - top < 4) continue;
        const crop = document.createElement('canvas');
        crop.width = right - left; crop.height = bottom - top;
        crop.getContext('2d').drawImage(rendered.canvas, left, top, crop.width, crop.height, 0, 0, crop.width, crop.height);
        onProgress(`Targeted OCR recheck: page ${pageIndex + 1}, row ${r + 1}, column ${c + 1}...`);
        let result;
        try { result = await recognizeCell(crop, onProgress, numeric, places ?? null); }
        finally { crop.width = 1; crop.height = 1; }

        if (!result.text || result.confidence < MIN_RECHECK_CONFIDENCE) continue;
        let candidate = result.text;
        let candidateConfidence = result.confidence;
        let accept = false;
        if (!original) {
          accept = true;
          recoveredBlank += 1;
        } else {
          const originalNumeric = cleanOcrArtifact(original);
          const originalExact = places != null && new RegExp(`^\\(?-?\\d[\\d,]*\\.\\d{${places}}%?\\)?$`).test(originalNumeric);
          if (result.exactDecimal && !originalExact && result.confidence >= Math.max(MIN_RECHECK_CONFIDENCE, (confidence ?? 0) - 2)) accept = true;
          else if (result.confidence >= (confidence ?? 0) + 5 && candidate !== original) accept = true;
        }

        // Decimal punctuation recovery is permitted only after an independent high-confidence
        // image re-read confirms the digit sequence; column statistics never act alone.
        if (numeric && places != null && /^\d+$/.test(candidate.replace(/,/g, '')) && result.confidence >= 60) {
          const digits = candidate.replace(/,/g, '');
          const repaired = repairMissingDecimal(digits, places, refs.get(c) || []);
          if (repaired && (digits === original.replace(/,/g, '') || !original)) {
            candidate = repaired;
            accept = true;
          }
        }
        if (accept) {
          rows[r][c] = candidate;
          confidences[r][c] = candidateConfidence;
          rechecked += 1;
        }
      }
    }
  } finally {
    rendered.canvas.width = 1; rendered.canvas.height = 1;
  }
  return { recoveredBlank, rechecked };
}

async function extractOcrWords(pdf, pageIndex, boundaries, pageWidth, pageHeight, onProgress) {
  const rendered = await renderPage(pdf, pageIndex, OCR_ZOOM, 0);
  const scaleX = rendered.canvas.width / pageWidth;
  const scaleY = rendered.canvas.height / pageHeight;
  const words = [];
  try {
    const padding = Math.max(10, Math.round(OCR_ZOOM * 3));
    for (let c = 0; c < boundaries.length - 1; c += 1) {
      const trueLeft = Math.max(0, Math.floor(boundaries[c] * scaleX));
      const trueRight = Math.min(rendered.canvas.width, Math.ceil(boundaries[c + 1] * scaleX));
      const pixelLeft = Math.max(0, trueLeft - padding);
      const pixelRight = Math.min(rendered.canvas.width, trueRight + padding);
      if (pixelRight - pixelLeft < 3) continue;
      const crop = makeColumnCrop(rendered.canvas, pixelLeft, pixelRight);
      const enlargement = crop.width < 450 ? 1.75 : 1.5;
      const prepared = makeAutocontrastCanvas(crop, enlargement, 16, 'none');
      crop.width = 1; crop.height = 1;
      onProgress(`OCR page ${pageIndex + 1}, column ${c + 1} of ${boundaries.length - 1}...`);
      let recognized;
      try {
        recognized = await recognizeCanvas(prepared.canvas, onProgress, { psm: 6, minConfidence: FIRST_PASS_MIN_CONFIDENCE });
      } finally { prepared.canvas.width = 1; prepared.canvas.height = 1; }
      for (const item of recognized) {
        const localX0 = Math.max(0, (item.x0 - prepared.border) / enlargement);
        const localX1 = Math.max(0, (item.x1 - prepared.border) / enlargement);
        const centreX = pixelLeft + (localX0 + localX1) / 2;
        if (!(trueLeft <= centreX && centreX < trueRight)) continue;
        words.push({
          x0: (pixelLeft + localX0) / scaleX,
          x1: (pixelLeft + localX1) / scaleX,
          y0: Math.max(0, (item.y0 - prepared.border) / enlargement) / scaleY,
          y1: Math.max(0, (item.y1 - prepared.border) / enlargement) / scaleY,
          text: item.text,
          confidence: item.confidence,
          block: item.block,
          paragraph: item.paragraph,
          line: item.line,
          column: c
        });
      }
    }
  } finally { rendered.canvas.width = 1; rendered.canvas.height = 1; }
  return words;
}

function buildReviewSheet(XLSX, reviewRows, arithmeticWarnings) {
  const aoa = [
    ['OCR REVIEW - DIAGNOSTICS ONLY'],
    ['Original Page_X worksheet values are preserved. Arithmetic checks never modify extracted values.'],
    ['Page', 'Excel Row', 'Excel Column', 'Extracted Value / Row', 'Confidence', 'Review Reason']
  ];
  for (const item of reviewRows) aoa.push([item.page, item.row, item.column, item.value, item.confidence, item.reason]);
  for (const warning of arithmeticWarnings) aoa.push([warning.page, warning.row, '', warning.value, '', warning.reason]);
  if (aoa.length === 3) aoa.push(['', '', '', '', '', 'No low-confidence, numeric-format or supported arithmetic diagnostics were triggered.']);
  const sheet = XLSX.utils.aoa_to_sheet(aoa);
  sheet['!cols'] = [{ wch: 8 }, { wch: 12 }, { wch: 12 }, { wch: 28 }, { wch: 12 }, { wch: 90 }];
  return sheet;
}

export async function generateWorkbook(pdf, columnsByPage, onProgress = () => {}) {
  const XLSX = await loadGlobalScript('XLSX', 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js');
  const workbook = XLSX.utils.book_new();
  const usedOcrPages = [];
  const allReviewRows = [];
  const arithmeticWarnings = [];
  let blankRecovered = 0;
  let numericRechecked = 0;
  let wroteSheet = false;

  for (let p = 0; p < pdf.numPages; p += 1) {
    const raw = columnsByPage.get(p) || [];
    if (!raw.length) continue;
    onProgress(`Extracting page ${p + 1} of ${pdf.numPages}...`);
    const page = await pdf.getPage(p + 1);
    const viewport = page.getViewport({ scale: 1 });
    const width = viewport.width, height = viewport.height;
    const boundaries = [0, ...raw.map((x) => x / 1.5).sort((a, b) => a - b), width];
    let words = await getNativeWords(page);
    const usedOcr = words.length === 0;
    if (usedOcr) {
      usedOcrPages.push(p + 1);
      words = await extractOcrWords(pdf, p, boundaries, width, height, onProgress);
    }

    let { rows, centres, confidences } = assembleRows(words, boundaries, usedOcr ? 4.0 : 4.0);
    if (usedOcr && rows.length) {
      onProgress(`Recovering suspicious OCR cells on page ${p + 1}...`);
      const recovered = await recheckCells(pdf, p, boundaries, rows, centres, confidences, width, height, onProgress);
      blankRecovered += recovered.recoveredBlank;
      numericRechecked += recovered.rechecked;
    }

    rows = rows.map((row) => row.map((cell) => cleanNumeric(cleanOcrArtifact(cell))));
    const numericCols = numericColumns(rows);
    const decimalPlaces = inferDecimalPlaces(rows);
    allReviewRows.push(...collectOcrReview(rows, confidences, numericCols, decimalPlaces, p + 1));
    arithmeticWarnings.push(...planSummaryArithmeticDiagnostics(rows, p + 1));
    const sheet = XLSX.utils.aoa_to_sheet(rows);
    XLSX.utils.book_append_sheet(workbook, sheet, `Page_${p + 1}`);
    wroteSheet = true;
  }

  if (!wroteSheet) XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([['No columns marked or no extractable text found.']]), 'Result');
  if (allReviewRows.length || arithmeticWarnings.length) {
    XLSX.utils.book_append_sheet(workbook, buildReviewSheet(XLSX, allReviewRows, arithmeticWarnings), 'OCR Review');
  }
  return {
    bytes: XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }),
    usedOcrPages,
    blankRecovered,
    numericRechecked,
    reviewCount: allReviewRows.length,
    arithmeticWarnings: arithmeticWarnings.length
  };
}
