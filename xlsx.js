import { loadGlobalScript } from './vendor.js';
import { renderPage, getNativeWords } from './pdf.js';
import { recognizeCanvas } from './ocr.js';

// These values mirror the supplied Streamlit/PyMuPDF implementation.
const FIRST_PASS_ZOOM = 4.0;
const RECOVERY_ZOOM = 5.0;
const NATIVE_ROW_TOLERANCE = 4.0;
const OCR_ROW_TOLERANCE = 6.0;
const FIRST_PASS_MIN_CONFIDENCE = 10;

// Additional review thresholds are diagnostics only; they never modify cells.
const LOW_CONFIDENCE_REVIEW = 45;

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

/** Lanczos-3 contribution table using Pillow's pixel-centre mapping. */
function sinc(value) {
  if (Math.abs(value) < 1e-12) return 1;
  const p = Math.PI * value;
  return Math.sin(p) / p;
}

function lanczosKernel(value) {
  const absolute = Math.abs(value);
  if (absolute >= 3) return 0;
  return sinc(value) * sinc(value / 3);
}

function buildLanczosContributions(inputSize, outputSize) {
  const scale = outputSize / inputSize;
  const filterScale = Math.max(1, 1 / scale);
  const support = 3 * filterScale;
  const result = new Array(outputSize);

  for (let out = 0; out < outputSize; out += 1) {
    const centre = (out + 0.5) / scale;
    const first = Math.max(0, Math.floor(centre - support + 0.5));
    const last = Math.min(inputSize, Math.floor(centre + support + 0.5));
    const indices = [];
    const weights = [];
    let total = 0;
    for (let input = first; input < last; input += 1) {
      const weight = lanczosKernel((input + 0.5 - centre) * filterScale);
      if (weight === 0) continue;
      indices.push(input);
      weights.push(weight);
      total += weight;
    }
    if (total !== 0) {
      for (let i = 0; i < weights.length; i += 1) weights[i] /= total;
    }
    result[out] = { indices, weights };
  }
  return result;
}

/**
 * Two-pass Lanczos-3 resizing of an already grayscale/autocontrasted canvas.
 * The separable implementation avoids the cost of a full 2-D convolution.
 */
function resizeGrayLanczos(sourceImageData, outputWidth, outputHeight) {
  const inputWidth = sourceImageData.width;
  const inputHeight = sourceImageData.height;
  const source = sourceImageData.data;
  const horizontal = buildLanczosContributions(inputWidth, outputWidth);
  const vertical = buildLanczosContributions(inputHeight, outputHeight);
  const intermediate = new Float32Array(outputWidth * inputHeight);

  // Horizontal pass.
  for (let y = 0; y < inputHeight; y += 1) {
    const sourceRow = y * inputWidth * 4;
    const targetRow = y * outputWidth;
    for (let x = 0; x < outputWidth; x += 1) {
      const contribution = horizontal[x];
      let value = 0;
      for (let k = 0; k < contribution.indices.length; k += 1) {
        value += source[sourceRow + contribution.indices[k] * 4] * contribution.weights[k];
      }
      intermediate[targetRow + x] = value;
    }
  }

  // Vertical pass. Store grayscale RGBA directly into ImageData.
  const output = new ImageData(outputWidth, outputHeight);
  const target = output.data;
  for (let y = 0; y < outputHeight; y += 1) {
    const contribution = vertical[y];
    const targetRow = y * outputWidth * 4;
    for (let x = 0; x < outputWidth; x += 1) {
      let value = 0;
      for (let k = 0; k < contribution.indices.length; k += 1) {
        value += intermediate[contribution.indices[k] * outputWidth + x] * contribution.weights[k];
      }
      const pixel = Math.max(0, Math.min(255, Math.floor(value + 0.5)));
      const offset = targetRow + x * 4;
      target[offset] = pixel;
      target[offset + 1] = pixel;
      target[offset + 2] = pixel;
      target[offset + 3] = 255;
    }
  }
  return output;
}

/** Browser port of Pillow grayscale -> autocontrast -> LANCZOS -> white border. */
export function makeAutocontrastCanvas(source, enlargement = 1, border = 16, binary = false) {
  const gray = document.createElement('canvas');
  gray.width = Math.max(1, source.width);
  gray.height = Math.max(1, source.height);
  const grayContext = gray.getContext('2d', { willReadFrequently: true });
  grayContext.drawImage(source, 0, 0);

  const image = grayContext.getImageData(0, 0, gray.width, gray.height);
  const pixels = image.data;
  let low = 255;
  let high = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    // Pillow ImageOps.grayscale uses the ITU-R 601-2 luma coefficients.
    const value = Math.max(0, Math.min(255, Math.round(
      pixels[i] * 0.299 + pixels[i + 1] * 0.587 + pixels[i + 2] * 0.114
    )));
    pixels[i] = value;
    pixels[i + 1] = value;
    pixels[i + 2] = value;
    if (value < low) low = value;
    if (value > high) high = value;
  }

  // Pillow ImageOps.autocontrast defaults to cutoff=0 and preserve_tone=false.
  const range = Math.max(1, high - low);
  for (let i = 0; i < pixels.length; i += 4) {
    const value = Math.max(0, Math.min(255, Math.round((pixels[i] - low) * 255 / range)));
    pixels[i] = value;
    pixels[i + 1] = value;
    pixels[i + 2] = value;
    pixels[i + 3] = 255;
  }
  grayContext.putImageData(image, 0, 0);

  // Python uses int(width * scale), i.e. truncation rather than Math.round.
  const scaledWidth = Math.max(1, Math.trunc(gray.width * enlargement));
  const scaledHeight = Math.max(1, Math.trunc(gray.height * enlargement));
  const resizedImage = resizeGrayLanczos(image, scaledWidth, scaledHeight);
  const enlarged = document.createElement('canvas');
  enlarged.width = scaledWidth;
  enlarged.height = scaledHeight;
  enlarged.getContext('2d', { willReadFrequently: binary }).putImageData(resizedImage, 0, 0);
  gray.width = 1;
  gray.height = 1;

  if (binary) {
    const context = enlarged.getContext('2d', { willReadFrequently: true });
    const enlargedImage = context.getImageData(0, 0, scaledWidth, scaledHeight);
    const enlargedPixels = enlargedImage.data;
    for (let i = 0; i < enlargedPixels.length; i += 4) {
      const value = enlargedPixels[i] > 175 ? 255 : 0;
      enlargedPixels[i] = value;
      enlargedPixels[i + 1] = value;
      enlargedPixels[i + 2] = value;
      enlargedPixels[i + 3] = 255;
    }
    context.putImageData(enlargedImage, 0, 0);
  }

  const output = document.createElement('canvas');
  output.width = scaledWidth + border * 2;
  output.height = scaledHeight + border * 2;
  const outputContext = output.getContext('2d');
  outputContext.fillStyle = '#ffffff';
  outputContext.fillRect(0, 0, output.width, output.height);
  outputContext.drawImage(enlarged, border, border);
  enlarged.width = 1;
  enlarged.height = 1;
  return { canvas: output, enlargement, border };
}

function canvasToBlob(canvas, type = 'image/png') {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('Could not capture OCR diagnostic image.')), type);
  });
}

function makeColumnCrop(pageCanvas, pixelLeft, pixelRight) {
  const width = Math.max(1, pixelRight - pixelLeft);
  const crop = document.createElement('canvas');
  crop.width = width;
  crop.height = pageCanvas.height;
  crop.getContext('2d').drawImage(pageCanvas, pixelLeft, 0, width, pageCanvas.height, 0, 0, width, pageCanvas.height);
  return crop;
}

/** Match Python group_words_into_rows: sort by y0/x0, first matching row wins,
 * then update its centre using the same pairwise averaging operation. */
function groupWordsIntoRows(words, yTolerance = 4) {
  const rows = [];
  const sorted = [...words].sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
  for (const word of sorted) {
    const yMid = (word.y0 + word.y1) / 2;
    let placed = false;
    for (const row of rows) {
      if (Math.abs(row.y - yMid) <= yTolerance) {
        row.words.push(word);
        row.y = (row.y + yMid) / 2;
        placed = true;
        break;
      }
    }
    if (!placed) rows.push({ y: yMid, words: [word] });
  }
  return rows;
}

function assignWordToColumn(word, boundaries) {
  const xMid = (word.x0 + word.x1) / 2;
  for (let i = 0; i < boundaries.length - 1; i += 1) {
    if (boundaries[i] <= xMid && xMid < boundaries[i + 1]) return i;
  }
  return null;
}

function buildTableRowsWithGeometry(words, boundaries, yTolerance) {
  const groupedRows = groupWordsIntoRows(words, yTolerance);
  const tableRows = [];
  const rowCentres = [];
  const rowConfidences = [];

  for (const grouped of groupedRows) {
    const cells = Array(boundaries.length - 1).fill('');
    const cellScores = cells.map(() => []);
    for (const word of [...grouped.words].sort((a, b) => a.x0 - b.x0)) {
      const columnIndex = assignWordToColumn(word, boundaries);
      if (columnIndex == null) continue;
      cells[columnIndex] = `${cells[columnIndex]} ${word.text}`.trim();
      if (Number.isFinite(word.confidence)) cellScores[columnIndex].push(word.confidence);
    }
    if (cells.some((cell) => cell.trim())) {
      tableRows.push(cells);
      rowCentres.push(Number(grouped.y));
      rowConfidences.push(cellScores.map((scores) => scores.length
        ? scores.reduce((sum, score) => sum + score, 0) / scores.length
        : null));
    }
  }
  return { rows: tableRows, centres: rowCentres, confidences: rowConfidences };
}

function looksNumericOcr(value) {
  let text = String(value ?? '').trim();
  if (!text) return false;
  text = text.replace(/ /g, '').replace(/\$/g, '').replace(/€/g, '').replace(/£/g, '');
  return /^\(?-?\d[\d,]*(?:\.\d+)?%?\)?$/.test(text);
}

function numericColumns(tableRows) {
  if (!tableRows.length) return new Set();
  const numeric = new Set();
  const width = Math.max(...tableRows.map((row) => row.length));
  for (let columnIndex = 0; columnIndex < width; columnIndex += 1) {
    const values = tableRows
      .filter((row) => columnIndex < row.length && String(row[columnIndex] ?? '').trim())
      .map((row) => String(row[columnIndex]).trim());
    if (values.length >= 2 && values.filter(looksNumericOcr).length / values.length >= 0.55) numeric.add(columnIndex);
  }
  return numeric;
}

function createOcrPageCache(renderedCanvas, pageWidth, pageHeight) {
  return {
    canvas: renderedCanvas,
    scaleX: renderedCanvas.width / pageWidth,
    scaleY: renderedCanvas.height / pageHeight,
    crops: new Map()
  };
}

function getCachedOcrCrop(cache, box) {
  const key = box.map((value) => Math.trunc(value)).join(',');
  let crop = cache.crops.get(key);
  if (!crop) {
    const [left, top, right, bottom] = box.map((value) => Math.trunc(value));
    crop = document.createElement('canvas');
    crop.width = Math.max(1, right - left);
    crop.height = Math.max(1, bottom - top);
    crop.getContext('2d').drawImage(
      cache.canvas, left, top, crop.width, crop.height, 0, 0, crop.width, crop.height
    );
    cache.crops.set(key, crop);
  }
  return crop;
}

function closeOcrPageCache(cache) {
  if (!cache) return;
  for (const crop of cache.crops.values()) {
    crop.width = 1;
    crop.height = 1;
  }
  cache.crops.clear();
  if (cache.canvas) {
    cache.canvas.width = 1;
    cache.canvas.height = 1;
  }
}

function blankCellIsSurrounded(tableRows, rowIndex, columnIndex) {
  const row = tableRows[rowIndex];
  const left = columnIndex > 0 && Boolean(String(row[columnIndex - 1] ?? '').trim());
  const right = columnIndex + 1 < row.length && Boolean(String(row[columnIndex + 1] ?? '').trim());
  const above = rowIndex > 0 && columnIndex < tableRows[rowIndex - 1].length
    && Boolean(String(tableRows[rowIndex - 1][columnIndex] ?? '').trim());
  const below = rowIndex + 1 < tableRows.length && columnIndex < tableRows[rowIndex + 1].length
    && Boolean(String(tableRows[rowIndex + 1][columnIndex] ?? '').trim());
  return (left && right) || (above && below);
}

function rowBoundsForRecovery(rowCentres, rowIndex) {
  const centre = rowCentres[rowIndex];
  const previous = rowIndex > 0 ? rowCentres[rowIndex - 1] : null;
  const next = rowIndex + 1 < rowCentres.length ? rowCentres[rowIndex + 1] : null;
  let halfHeight;
  if (previous == null && next == null) halfHeight = 8.0;
  else if (previous == null) halfHeight = Math.max(5.0, (next - centre) * 0.48);
  else if (next == null) halfHeight = Math.max(5.0, (centre - previous) * 0.48);
  else halfHeight = Math.max(5.0, Math.min(centre - previous, next - centre) * 0.48);
  return [Math.max(0, centre - halfHeight), centre + halfHeight];
}

function extractOcrCellCandidate(words, numericOnly) {
  const tokens = words.map((word) => String(word.text ?? '').trim()).filter(Boolean);
  let candidate = tokens.join(' ').trim();
  if (!candidate) return { text: '', confidence: 0 };
  if (numericOnly) {
    candidate = candidate.replace(/ /g, '');
    if (!looksNumericOcr(candidate)) return { text: '', confidence: 0 };
  }
  const confidence = words.length
    ? words.reduce((sum, word) => sum + (Number.isFinite(word.confidence) ? word.confidence : 0), 0) / words.length
    : 0;
  return { text: candidate, confidence };
}

/** Port of Python _ocr_cell_candidate. Tries clean enlarged OCR first, then
 * thresholded OCR only if the first pass does not yield a strong candidate. */
async function ocrCellCandidate(source, onProgress, numericOnly, expectedDecimalPlaces = null) {
  const whitelist = numericOnly ? '0123456789.,()%-$' : '';
  const configs = [7, 8, 13];
  const candidates = [];
  const variants = [
    makeAutocontrastCanvas(source, 4, 24, false),
    null
  ];

  try {
    for (let variantIndex = 0; variantIndex < 2; variantIndex += 1) {
      if (variantIndex === 1) variants[1] = makeAutocontrastCanvas(source, 4, 24, true);
      const variant = variants[variantIndex];
      for (const psm of configs) {
        const words = await recognizeCanvas(variant.canvas, onProgress, {
          psm,
          whitelist,
          minConfidence: 0
        });
        const found = extractOcrCellCandidate(words, numericOnly);
        if (!found.text) continue;

        let score = found.confidence;
        let exactDecimal = false;
        if (numericOnly && /\d/.test(found.text)) score += 8;
        if (numericOnly && expectedDecimalPlaces != null) {
          const pattern = new RegExp(`^\\(?-?\\d[\\d,]*\\.\\d{${expectedDecimalPlaces}}%?\\)?$`);
          exactDecimal = pattern.test(found.text);
          if (exactDecimal) score += 30;
          else if (!found.text.includes('.') && /^\d{5,}$/.test(found.text)) score -= 10;
        }

        if ((exactDecimal && score >= 75)
          || (expectedDecimalPlaces == null && score >= 82)) {
          return { ...found, score, exactDecimal };
        }
        candidates.push({ ...found, score, exactDecimal });
      }
    }
  } finally {
    for (const variant of variants) {
      if (variant?.canvas) {
        variant.canvas.width = 1;
        variant.canvas.height = 1;
      }
    }
  }

  candidates.sort((a, b) => b.score - a.score || b.text.length - a.text.length);
  return candidates[0] || { text: '', confidence: 0, score: 0, exactDecimal: false };
}

async function recoverBlankOcrCells(cache, boundaries, tableRows, rowCentres, onProgress, pageNumber) {
  if (!tableRows.length || !rowCentres.length) return 0;
  const numeric = numericColumns(tableRows);
  let recovered = 0;

  for (let rowIndex = 0; rowIndex < tableRows.length; rowIndex += 1) {
    const row = tableRows[rowIndex];
    const populated = row.filter((cell) => Boolean(String(cell ?? '').trim())).length;
    if (populated < 3) continue;
    const [topPdf, bottomPdf] = rowBoundsForRecovery(rowCentres, rowIndex);

    for (let columnIndex = 0; columnIndex < row.length; columnIndex += 1) {
      if (String(row[columnIndex] ?? '').trim()) continue;
      if (!blankCellIsSurrounded(tableRows, rowIndex, columnIndex)) continue;
      if (columnIndex === 0 && !numeric.has(columnIndex)) continue;

      const leftPdf = Math.max(0, boundaries[columnIndex] - 2.0);
      const rightPdf = Math.min(cache.canvas.width / cache.scaleX, boundaries[columnIndex + 1] + 2.0);
      const cropBox = [
        Math.max(0, Math.round(leftPdf * cache.scaleX)),
        Math.max(0, Math.round(topPdf * cache.scaleY)),
        Math.min(cache.canvas.width, Math.round(rightPdf * cache.scaleX)),
        Math.min(cache.canvas.height, Math.round(bottomPdf * cache.scaleY))
      ];
      if (cropBox[2] - cropBox[0] < 4 || cropBox[3] - cropBox[1] < 4) continue;

      const crop = getCachedOcrCrop(cache, cropBox);
      onProgress(`Recovering blank OCR cell: page ${pageNumber}, row ${rowIndex + 1}, column ${columnIndex + 1}...`);
      const candidate = await ocrCellCandidate(crop, onProgress, numeric.has(columnIndex), null);
      if (candidate.text) {
        row[columnIndex] = candidate.text;
        recovered += 1;
      }
    }
  }
  return recovered;
}

function normalizeOcrNumericSpacing(value) {
  if (value == null || typeof value === 'number') return value;
  const text = String(value).trim();
  if (!text || /[A-Za-z]/.test(text)) return value;
  const compact = text.replace(/\s+/g, '');
  return looksNumericOcr(compact) ? compact : value;
}

function normalizeOcrTableValues(tableRows) {
  for (const row of tableRows) {
    for (let columnIndex = 0; columnIndex < row.length; columnIndex += 1) {
      row[columnIndex] = normalizeOcrNumericSpacing(row[columnIndex]);
    }
  }
  return tableRows;
}

function inferColumnDecimalPlaces(tableRows) {
  const result = new Map();
  if (!tableRows.length) return result;
  const width = Math.max(...tableRows.map((row) => row.length));
  for (let columnIndex = 0; columnIndex < width; columnIndex += 1) {
    const counts = new Map();
    let decimalValues = 0;
    for (const row of tableRows) {
      if (columnIndex >= row.length) continue;
      const normalized = normalizeOcrNumericSpacing(row[columnIndex]);
      const text = String(normalized ?? '').trim().replace(/,/g, '');
      const match = text.match(/^\(?-?\d+\.(\d+)%?\)?$/);
      if (!match) continue;
      const places = match[1].length;
      counts.set(places, (counts.get(places) || 0) + 1);
      decimalValues += 1;
    }
    if (decimalValues >= 4) {
      const [places, count] = [...counts.entries()].sort((a, b) => a[1] - b[1]).pop();
      if (count / decimalValues >= 0.70) result.set(columnIndex, places);
    }
  }
  return result;
}

function columnNumericMagnitudes(tableRows, columnIndex) {
  const values = [];
  for (const row of tableRows) {
    if (columnIndex >= row.length) continue;
    let text = String(normalizeOcrNumericSpacing(row[columnIndex]) ?? '').trim()
      .replace(/,/g, '').replace(/%/g, '');
    if (text.startsWith('(') && text.endsWith(')')) text = `-${text.slice(1, -1)}`;
    if (text.includes('.')) {
      const value = Number(text);
      if (Number.isFinite(value)) values.push(Math.abs(value));
    }
  }
  return values.filter((value) => value > 0);
}

function repairMissingDecimalByColumnPattern(rawValue, decimalPlaces, referenceValues) {
  const text = String(rawValue ?? '').trim().replace(/,/g, '');
  if (!/^\d+$/.test(text) || text.length <= decimalPlaces) return '';
  const proposedText = `${text.slice(0, -decimalPlaces)}.${text.slice(-decimalPlaces)}`;
  const originalValue = Number(text);
  const proposedValue = Number(proposedText);
  if (!Number.isFinite(originalValue) || !Number.isFinite(proposedValue) || !referenceValues.length) return '';
  const ordered = [...referenceValues].sort((a, b) => a - b);
  const medianValue = median(ordered);
  const maximum = Math.max(...ordered);
  const minimum = Math.min(...ordered);
  const upper = Math.max(maximum * 5, medianValue * 20);
  const originalIsOutlier = originalValue > upper;
  const proposedIsPlausible = proposedValue >= minimum / 100 && proposedValue <= upper;
  return originalIsOutlier && proposedIsPlausible ? proposedText : '';
}

async function recoverSuspiciousNumericCells(cache, boundaries, tableRows, rowCentres, onProgress, pageNumber) {
  const expectedPlaces = inferColumnDecimalPlaces(tableRows);
  if (!expectedPlaces.size || !rowCentres.length) return 0;
  const references = new Map([...expectedPlaces.keys()].map((columnIndex) => [
    columnIndex,
    columnNumericMagnitudes(tableRows, columnIndex)
  ]));
  let corrected = 0;

  for (let rowIndex = 0; rowIndex < tableRows.length; rowIndex += 1) {
    const row = tableRows[rowIndex];
    const [topPdf, bottomPdf] = rowBoundsForRecovery(rowCentres, rowIndex);
    for (const [columnIndex, places] of expectedPlaces.entries()) {
      if (columnIndex >= row.length) continue;
      const original = normalizeOcrNumericSpacing(row[columnIndex]);
      const text = String(original ?? '').trim().replace(/,/g, '');
      // Exact match to the Python implementation: only five or more digits.
      if (!/^\d{5,}$/.test(text)) continue;

      const leftPdf = Math.max(0, boundaries[columnIndex] - 2.0);
      const rightPdf = Math.min(cache.canvas.width / cache.scaleX, boundaries[columnIndex + 1] + 2.0);
      const cropBox = [
        Math.max(0, Math.round(leftPdf * cache.scaleX)),
        Math.max(0, Math.round(topPdf * cache.scaleY)),
        Math.min(cache.canvas.width, Math.round(rightPdf * cache.scaleX)),
        Math.min(cache.canvas.height, Math.round(bottomPdf * cache.scaleY))
      ];
      if (cropBox[2] - cropBox[0] < 4 || cropBox[3] - cropBox[1] < 4) continue;

      const crop = getCachedOcrCrop(cache, cropBox);
      onProgress(`Checking suspicious numeric OCR cell: page ${pageNumber}, row ${rowIndex + 1}, column ${columnIndex + 1}...`);
      const candidate = await ocrCellCandidate(crop, onProgress, true, places);
      const candidateText = String(normalizeOcrNumericSpacing(candidate.text) ?? '').trim().replace(/,/g, '');

      const exactDecimalPattern = new RegExp(`^\\(?-?\\d+\\.\\d{${places}}%?\\)?$`);
      if (exactDecimalPattern.test(candidateText)) {
        row[columnIndex] = candidate.text;
        corrected += 1;
        continue;
      }

      const fallbackSource = /^\d+$/.test(candidateText) ? candidateText : text;
      const repaired = repairMissingDecimalByColumnPattern(
        fallbackSource,
        places,
        references.get(columnIndex) || []
      );
      if (repaired) {
        row[columnIndex] = repaired;
        corrected += 1;
      }
    }
  }
  return corrected;
}

function cleanNumericString(value) {
  if (value == null || typeof value === 'number') return value;
  const text = String(value).trim();
  if (!text) return value;
  // Match Python clean_numeric_string: preserve labels, dates, ranges and IDs.
  if (/[A-Za-z]/.test(text)) return value;
  if (/\d{1,4}[-/]\d{1,2}[-/]\d{1,4}/.test(text)) return value;
  if (/\d+\s*-\s*\d+/.test(text)) return value;

  let cleaned = text;
  let negative = false;
  if (cleaned.startsWith('(') && cleaned.endsWith(')')) {
    negative = true;
    cleaned = cleaned.slice(1, -1).trim();
  }
  cleaned = cleaned.replace(/,/g, '').replace(/\$/g, '').replace(/€/g, '').replace(/£/g, '').replace(/¥/g, '').trim();
  let percent = false;
  if (cleaned.endsWith('%')) {
    percent = true;
    cleaned = cleaned.slice(0, -1).trim();
  }
  if (cleaned.startsWith('-')) {
    negative = true;
    cleaned = cleaned.slice(1).trim();
  }
  if (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(cleaned)) return value;
  let number = Number(cleaned);
  if (!Number.isFinite(number)) return value;
  if (negative) number = -number;
  if (percent) number /= 100;
  if (Number.isInteger(number) && !percent && !cleaned.includes('.')) return Math.trunc(number);
  return number;
}

function numericColumnsForReview(tableRows) {
  return numericColumns(tableRows);
}

function parseNumeric(value) {
  const text = String(value ?? '').trim().replace(/\s+/g, '').replace(/,/g, '').replace(/[$€£]/g, '');
  if (!text || /[A-Za-z]/.test(text)) return null;
  let cleaned = text;
  let negative = false;
  if (cleaned.startsWith('(') && cleaned.endsWith(')')) { negative = true; cleaned = cleaned.slice(1, -1); }
  const percent = cleaned.endsWith('%');
  if (percent) cleaned = cleaned.slice(0, -1);
  if (!/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(cleaned)) return null;
  let valueNumber = Number(cleaned) * (negative ? -1 : 1);
  if (percent) valueNumber /= 100;
  return Number.isFinite(valueNumber) ? valueNumber : null;
}

function identifyPlanSummaryProfile(rows) {
  const allText = rows.flat().join(' ').toLowerCase().replace(/[^a-z]/g, '');
  const reportTitle = allText.includes('plan') && allText.includes('report') && /summ|sumn|mary/.test(allText);
  return reportTitle && rows.some((row) => String(row[0] ?? '').trim().toLowerCase() === 'code');
}

function collectOcrReview(rows, confidences, numericCols, decimalPlaces, pageNumber) {
  const review = [];
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    for (let columnIndex = 0; columnIndex < rows[rowIndex].length; columnIndex += 1) {
      const value = rows[rowIndex][columnIndex];
      if (!String(value ?? '').trim()) continue;
      const confidence = confidences[rowIndex]?.[columnIndex];
      const reasons = [];
      if (confidence != null && confidence < LOW_CONFIDENCE_REVIEW) reasons.push('Low OCR confidence (diagnostic only)');
      if (numericCols.has(columnIndex)) {
        if (!looksNumericOcr(value)) reasons.push('Non-numeric OCR result in a predominantly numeric column');
        const places = decimalPlaces.get(columnIndex);
        const compact = String(value).replace(/,/g, '').replace(/%$/, '');
        if (places != null && /^\d{3,}$/.test(compact)) reasons.push(`Possible missing decimal punctuation (column pattern: ${places} decimal places)`);
      }
      if (reasons.length) review.push({
        page: pageNumber,
        row: rowIndex + 1,
        column: columnIndex + 1,
        value: String(value),
        confidence: confidence == null ? '' : Number(confidence.toFixed(1)),
        reason: reasons.join('; ')
      });
    }
  }
  return review;
}

function planSummaryArithmeticDiagnostics(rows, pageNumber) {
  // Diagnostic only. This function never modifies rows or cell values.
  if (!identifyPlanSummaryProfile(rows)) return [];
  const warnings = [];
  const excludedLabels = /^(prior|net|grand|reversed|unadjudicated|plan summary|code|)$/i;
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    const row = rows[rowIndex];
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
      if (Math.abs(actual - expected) > tolerance) warnings.push({
        page: pageNumber,
        row: rowIndex + 1,
        value: code,
        reason: `Arithmetic diagnostic only: ${label}; observed difference ${Number((actual - expected).toFixed(3))}. No cell values changed.`
      });
    };
    if ([aac, costMarkup, effectiveMarkup].every((value) => value != null)) check(
      'A.A.C. + effective markup vs. Cost + MkUp', costMarkup, aac + effectiveMarkup,
      Math.max(0.03, Math.abs(costMarkup) * 0.0001)
    );
    if ([costMarkup, fees, totalDollars].every((value) => value != null)) check(
      'Cost + MkUp + Fees vs. Total', totalDollars, costMarkup + fees,
      Math.max(0.03, Math.abs(totalDollars) * 0.0001)
    );
    if ([effectiveMarkup, fees, totalDollars, gp].every((value) => value != null) && totalDollars !== 0) check(
      'Derived margin vs. G.P.%', gp, ((effectiveMarkup + fees) / totalDollars) * 100, 0.15
    );
    if ([newCount, repeatCount, totalCount].every((value) => value != null)) check(
      'New + Repeat vs. Total prescription count', totalCount, newCount + repeatCount, 0
    );
  }
  return warnings;
}

function buildReviewSheet(XLSX, reviewRows, arithmeticWarnings) {
  const rows = [
    ['OCR REVIEW - DIAGNOSTICS ONLY'],
    ['Original Page_X worksheet values are preserved. Arithmetic checks never modify extracted values.'],
    ['Page', 'Excel Row', 'Excel Column', 'Extracted Value / Row', 'Confidence', 'Review Reason']
  ];
  for (const item of reviewRows) rows.push([item.page, item.row, item.column, item.value, item.confidence, item.reason]);
  for (const warning of arithmeticWarnings) rows.push([warning.page, warning.row, '', warning.value, '', warning.reason]);
  if (rows.length === 3) rows.push(['', '', '', '', '', 'No low-confidence, numeric-format or supported arithmetic diagnostics were triggered.']);
  const sheet = XLSX.utils.aoa_to_sheet(rows);
  sheet['!cols'] = [{ wch: 8 }, { wch: 12 }, { wch: 12 }, { wch: 28 }, { wch: 12 }, { wch: 90 }];
  return sheet;
}

/** Column-by-column, padded OCR fallback. Mirrors extract_ocr_words_by_columns. */
async function extractOcrWordsByColumns(pdf, pageIndex, boundaries, pageWidth, pageHeight, onProgress, diagnostics = null) {
  const rendered = await renderPage(pdf, pageIndex, FIRST_PASS_ZOOM, 0);
  const scaleX = rendered.canvas.width / pageWidth;
  const scaleY = rendered.canvas.height / pageHeight;
  const words = [];
  const cropPaddingPx = Math.max(10, Math.round(FIRST_PASS_ZOOM * 3));

  try {
    for (let columnIndex = 0; columnIndex < boundaries.length - 1; columnIndex += 1) {
      const pdfLeft = Math.max(0, Number(boundaries[columnIndex]));
      const pdfRight = Math.min(pageWidth, Number(boundaries[columnIndex + 1]));
      if (pdfRight <= pdfLeft) continue;
      const truePixelLeft = Math.max(0, Math.round(pdfLeft * scaleX));
      const truePixelRight = Math.min(rendered.canvas.width, Math.round(pdfRight * scaleX));
      const pixelLeft = Math.max(0, truePixelLeft - cropPaddingPx);
      const pixelRight = Math.min(rendered.canvas.width, truePixelRight + cropPaddingPx);
      if (pixelRight - pixelLeft < 3) continue;

      const crop = makeColumnCrop(rendered.canvas, pixelLeft, pixelRight);
      const enlargement = crop.width < 450 ? 1.75 : 1.5;
      const prepared = makeAutocontrastCanvas(crop, enlargement, 16, false);
      onProgress(`OCR page ${pageIndex + 1}, column ${columnIndex + 1} of ${boundaries.length - 1}...`);

      let recognized;
      const columnStarted = performance.now();
      try {
        recognized = await recognizeCanvas(prepared.canvas, onProgress, {
          psm: 6,
          minConfidence: FIRST_PASS_MIN_CONFIDENCE,
          captureTsv: Boolean(diagnostics)
        });
        const recognitionElapsedMs = Math.round(performance.now() - columnStarted);
        if (diagnostics) {
          diagnostics.push({
            page: pageIndex + 1,
            column: columnIndex + 1,
            pdfLeft,
            pdfRight,
            renderedPixelLeft: pixelLeft,
            renderedPixelRight: pixelRight,
            enlargement,
            cropPng: await canvasToBlob(crop),
            ocrInputPng: await canvasToBlob(prepared.canvas),
            tsv: recognized.rawTsv || '',
            recognizedWordCount: recognized.length,
            elapsedMs: recognitionElapsedMs
          });
        }
      } finally {
        prepared.canvas.width = 1;
        prepared.canvas.height = 1;
        crop.width = 1;
        crop.height = 1;
      }

      for (const item of recognized) {
        const localLeft = (item.x0 - 16) / enlargement;
        const localTop = (item.y0 - 16) / enlargement;
        const localWidth = (item.x1 - item.x0) / enlargement;
        const localHeight = (item.y1 - item.y0) / enlargement;
        const leftPx = pixelLeft + Math.max(0, localLeft);
        const rightPx = leftPx + localWidth;
        const centrePx = (leftPx + rightPx) / 2;
        if (!(truePixelLeft <= centrePx && centrePx < truePixelRight)) continue;
        const left = leftPx / scaleX;
        const top = Math.max(0, localTop) / scaleY;
        const width = localWidth / scaleX;
        const height = localHeight / scaleY;
        words.push({
          x0: left,
          y0: top,
          x1: left + width,
          y1: top + height,
          text: item.text,
          confidence: item.confidence,
          block: item.block,
          paragraph: item.paragraph,
          line: item.line,
          column: columnIndex
        });
      }
    }
  } finally {
    rendered.canvas.width = 1;
    rendered.canvas.height = 1;
  }
  return words;
}

export async function generateWorkbook(pdf, columnsByPage, onProgress = () => {}, options = {}) {
  const XLSX = await loadGlobalScript('XLSX', 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js');
  const workbook = XLSX.utils.book_new();
  const usedOcrPages = [];
  const allReviewRows = [];
  const arithmeticWarnings = [];
  const diagnostics = options.captureDiagnostics ? [] : null;
  let blankRecovered = 0;
  let numericRechecked = 0;
  let wroteSheet = false;

  for (let pageIndex = 0; pageIndex < pdf.numPages; pageIndex += 1) {
    const rawColumns = columnsByPage.get(pageIndex) || [];
    if (!rawColumns.length) continue;
    onProgress(`Extracting page ${pageIndex + 1} of ${pdf.numPages}...`);
    const page = await pdf.getPage(pageIndex + 1);
    const viewport = page.getViewport({ scale: 1 });
    const pageWidth = viewport.width;
    const pageHeight = viewport.height;
    const pdfColumns = [...rawColumns].sort((a, b) => a - b).map((x) => x / 1.5);
    const boundaries = [0, ...pdfColumns, pageWidth];

    // Exact workflow: native PDF words first; OCR only when no native words exist.
    let words = await getNativeWords(page);
    const usedOcr = words.length === 0;
    if (usedOcr) {
      usedOcrPages.push(pageIndex + 1);
      words = await extractOcrWordsByColumns(pdf, pageIndex, boundaries, pageWidth, pageHeight, onProgress, diagnostics);
    }

    const { rows, centres, confidences } = buildTableRowsWithGeometry(
      words,
      boundaries,
      usedOcr ? OCR_ROW_TOLERANCE : NATIVE_ROW_TOLERANCE
    );

    if (usedOcr && rows.length) {
      // The two Python recovery passes share one 5x page render and one crop cache.
      onProgress(`Preparing targeted OCR recovery for page ${pageIndex + 1}...`);
      const recoveryRender = await renderPage(pdf, pageIndex, RECOVERY_ZOOM, 0);
      const cache = createOcrPageCache(recoveryRender.canvas, pageWidth, pageHeight);
      try {
        onProgress(`Recovering surrounded blank cells on page ${pageIndex + 1}...`);
        blankRecovered += await recoverBlankOcrCells(
          cache, boundaries, rows, centres, onProgress, pageIndex + 1
        );
        normalizeOcrTableValues(rows);
        onProgress(`Recovering suspicious numeric cells on page ${pageIndex + 1}...`);
        numericRechecked += await recoverSuspiciousNumericCells(
          cache, boundaries, rows, centres, onProgress, pageIndex + 1
        );
      } finally {
        closeOcrPageCache(cache);
      }
    }

    // Match normalize_ocr_table_values followed by clean_numeric_string cell-by-cell.
    normalizeOcrTableValues(rows);
    const convertedRows = rows.map((row) => row.map(cleanNumericString));
    const numericCols = numericColumnsForReview(rows);
    const decimalPlaces = inferColumnDecimalPlaces(rows);
    allReviewRows.push(...collectOcrReview(rows, confidences, numericCols, decimalPlaces, pageIndex + 1));
    arithmeticWarnings.push(...planSummaryArithmeticDiagnostics(rows, pageIndex + 1));

    const sheet = XLSX.utils.aoa_to_sheet(convertedRows);
    XLSX.utils.book_append_sheet(workbook, sheet, `Page_${pageIndex + 1}`);
    wroteSheet = true;
  }

  if (!wroteSheet) {
    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.aoa_to_sheet([['No columns marked or no extractable text found.']]),
      'Result'
    );
  }
  if (allReviewRows.length || arithmeticWarnings.length) {
    XLSX.utils.book_append_sheet(
      workbook,
      buildReviewSheet(XLSX, allReviewRows, arithmeticWarnings),
      'OCR Review'
    );
  }

  return {
    bytes: XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }),
    usedOcrPages,
    blankRecovered,
    numericRechecked,
    reviewCount: allReviewRows.length,
    arithmeticWarnings: arithmeticWarnings.length,
    diagnostics
  };
}


/** Package the exact per-column OCR inputs and raw Tesseract TSV for debugging. */
export async function createOcrDiagnosticsZip(diagnostics) {
  if (!diagnostics?.length) throw new Error('No OCR diagnostics were captured.');
  const JSZip = await loadGlobalScript('JSZip', 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js');
  const zip = new JSZip();
  const summary = [
    'PDF Tools OCR Diagnostics',
    'These files show the exact first-pass column crops and processed images sent to Tesseract.js.',
    'The PDF itself is not included.',
    '',
    'page,column,pdf_left,pdf_right,render_pixel_left,render_pixel_right,enlargement,recognized_words,elapsed_ms'
  ];
  const tsvRows = ['page\tcolumn\tword_count\telapsed_ms\traw_tsv_file'];
  for (const item of diagnostics) {
    const prefix = `page_${String(item.page).padStart(3, '0')}_column_${String(item.column).padStart(2, '0')}`;
    zip.file(`${prefix}_source_crop.png`, item.cropPng);
    zip.file(`${prefix}_tesseract_input.png`, item.ocrInputPng);
    zip.file(`${prefix}_tesseract.tsv`, item.tsv || '');
    summary.push([
      item.page, item.column, item.pdfLeft.toFixed(3), item.pdfRight.toFixed(3),
      item.renderedPixelLeft, item.renderedPixelRight, item.enlargement,
      item.recognizedWordCount, item.elapsedMs
    ].join(','));
    tsvRows.push([item.page, item.column, item.recognizedWordCount, item.elapsedMs, `${prefix}_tesseract.tsv`].join('\t'));
  }
  zip.file('README.txt', summary.join('\n'));
  zip.file('column_summary.tsv', tsvRows.join('\n'));
  return zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 3 } });
}
