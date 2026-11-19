import { RENDER_ZOOM } from './config.js';
import { loadGlobalScript } from './vendor.js';

const PDFJS_VERSION = '6.5.438';
const PDFJS_ROOT = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}`;
const PDFJS_CDN = `${PDFJS_ROOT}/build`;
const PDFJS_WASM = `${PDFJS_ROOT}/wasm/`;
let pdfjsPromise;

async function getPdfJs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(`${PDFJS_CDN}/pdf.mjs`).then((pdfjsLib) => {
      pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS_CDN}/pdf.worker.min.mjs`;
      return pdfjsLib;
    }).catch((error) => {
      pdfjsPromise = null;
      throw new Error(`PDF.js could not be loaded. Check CDN access and try again. ${error.message}`);
    });
  }
  return pdfjsPromise;
}

async function getPdfLib() {
  return loadGlobalScript('PDFLib', 'https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js');
}

export async function openPdf(file) {
  const pdfjsLib = await getPdfJs();
  const bytes = new Uint8Array(await file.arrayBuffer());
  // PDF.js 6 moved JBIG2 and JPEG 2000 decoding to WebAssembly.
  // Supplying wasmUrl is essential for scanned PDFs that embed JBIG2 images;
  // without it, a PDF can load and report its page count while the scan renders blank.
  const pdf = await pdfjsLib.getDocument({
    data: bytes.slice(),
    wasmUrl: PDFJS_WASM,
    useWasm: true
  }).promise;
  return { bytes, pdf, pageCount: pdf.numPages };
}

export async function renderPage(pdf, pageIndex, zoom = RENDER_ZOOM, rotation = 0) {
  const page = await pdf.getPage(pageIndex + 1);
  const effectiveRotation = ((page.rotate || 0) + rotation + 360) % 360;
  const viewport = page.getViewport({ scale: zoom, rotation: effectiveRotation });
  const canvas = document.createElement('canvas');
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  canvas.style.width = `${Math.ceil(viewport.width)}px`;
  canvas.style.height = `${Math.ceil(viewport.height)}px`;
  canvas.style.background = '#ffffff';
  // Use the current PDF.js canvas API. The renderer obtains its own 2D context.
  await page.render({ canvas, viewport, background: '#ffffff' }).promise;
  return { canvas, viewport, page };
}

export async function getNativeWords(page) {
  const content = await page.getTextContent();
  const viewport = page.getViewport({ scale: 1 });
  const words = [];
  const measureCanvas = document.createElement('canvas');
  const measureContext = measureCanvas.getContext('2d');

  for (const item of content.items) {
    if (!item.str || !item.str.trim() || !item.transform) continue;
    const text = item.str;
    const tx = item.transform;
    const totalWidth = Math.max(0, item.width || 0);
    if (!totalWidth) continue;

    const style = content.styles?.[item.fontName] || {};
    const fontSize = Math.hypot(tx[2], tx[3]) || item.height || 10;
    const family = String(style.fontFamily || 'sans-serif').replace(/["\\]/g, '');
    const fontWeight = style.fontWeight || (style.bold ? 'bold' : 'normal');
    const fontStyle = style.italic ? 'italic' : 'normal';
    measureContext.font = `${fontStyle} ${fontWeight} ${fontSize}px "${family}"`;
    const measuredFull = measureContext.measureText(text).width || text.length;
    const xAxisLength = Math.hypot(tx[0], tx[1]) || 1;
    const xAxis = { x: tx[0] / xAxisLength, y: tx[1] / xAxisLength };
    const yAxis = { x: -xAxis.y, y: xAxis.x };
    const origin = { x: tx[4], y: tx[5] };
    const tokens = [...text.matchAll(/\S+/g)];

    for (const token of tokens) {
      const start = token.index ?? 0;
      const end = start + token[0].length;
      const startRatio = measureContext.measureText(text.slice(0, start)).width / measuredFull;
      const endRatio = measureContext.measureText(text.slice(0, end)).width / measuredFull;
      const startDistance = totalWidth * startRatio;
      const endDistance = totalWidth * endRatio;
      const startPoint = { x: origin.x + xAxis.x * startDistance, y: origin.y + xAxis.y * startDistance };
      const endPoint = { x: origin.x + xAxis.x * endDistance, y: origin.y + xAxis.y * endDistance };
      const corners = [
        [startPoint.x - yAxis.x * fontSize * 0.22, startPoint.y - yAxis.y * fontSize * 0.22],
        [endPoint.x - yAxis.x * fontSize * 0.22, endPoint.y - yAxis.y * fontSize * 0.22],
        [endPoint.x + yAxis.x * fontSize * 0.78, endPoint.y + yAxis.y * fontSize * 0.78],
        [startPoint.x + yAxis.x * fontSize * 0.78, startPoint.y + yAxis.y * fontSize * 0.78]
      ];
      const screenCorners = corners.map(([x, y]) => viewport.convertToViewportPoint(x, y));
      const xs = screenCorners.map((point) => point[0]);
      const ys = screenCorners.map((point) => point[1]);
      words.push({
        x0: Math.min(...xs), y0: Math.min(...ys),
        x1: Math.max(...xs), y1: Math.max(...ys),
        text: token[0]
      });
    }
  }
  measureCanvas.width = 1;
  measureCanvas.height = 1;
  return words;
}

export async function makeRasterPdf(sourcePdf, redactionsByPage, displayZoom = RENDER_ZOOM, outputZoom = 2) {
  const PDFLib = await getPdfLib();
  const out = await PDFLib.PDFDocument.create();
  for (let i = 0; i < sourcePdf.numPages; i += 1) {
    const rendered = await renderPage(sourcePdf, i, outputZoom, 0);
    const canvas = rendered.canvas;
    try {
      const boxes = redactionsByPage.get(i) || [];
      if (boxes.length) {
        const context = canvas.getContext('2d');
        const factor = outputZoom / displayZoom;
        context.fillStyle = '#000000';
        for (const box of boxes) {
          context.fillRect(box.x * factor, box.y * factor, box.w * factor, box.h * factor);
        }
      }
      const blob = await new Promise((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error('Could not encode a redacted page.')), 'image/png'));
      const pngBytes = new Uint8Array(await blob.arrayBuffer());
      const image = await out.embedPng(pngBytes);
      const width = canvas.width / outputZoom;
      const height = canvas.height / outputZoom;
      const page = out.addPage([width, height]);
      page.drawImage(image, { x: 0, y: 0, width, height });
    } finally {
      canvas.width = 1;
      canvas.height = 1;
    }
  }
  return out.save();
}

export async function rotatePdf(bytes, rotations) {
  const PDFLib = await getPdfLib();
  const doc = await PDFLib.PDFDocument.load(bytes);
  doc.getPages().forEach((page, i) => {
    const next = ((page.getRotation().angle + (rotations.get(i) || 0)) % 360 + 360) % 360;
    page.setRotation(PDFLib.degrees(next));
  });
  return doc.save();
}

export async function extractPages(bytes, order) {
  const PDFLib = await getPdfLib();
  const source = await PDFLib.PDFDocument.load(bytes);
  const out = await PDFLib.PDFDocument.create();
  const pages = await out.copyPages(source, order);
  pages.forEach((page) => out.addPage(page));
  return out.save();
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
