import * as pdfjsLib from 'https://cdn.jsdelivr.net/npm/pdfjs-dist@6.5.438/build/pdf.mjs';
const pdfWorkerUrl = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@6.5.438/build/pdf.worker.min.mjs';
import { PDFDocument, degrees } from 'https://esm.sh/pdf-lib@1.17.1?bundle';
import { RENDER_ZOOM } from './config.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export async function openPdf(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const pdf = await pdfjsLib.getDocument({ data: bytes.slice() }).promise;
  return { bytes, pdf, pageCount: pdf.numPages };
}

export async function renderPage(pdf, pageIndex, zoom = RENDER_ZOOM, rotation = 0) {
  const page = await pdf.getPage(pageIndex + 1);
  const effectiveRotation = ((page.rotate || 0) + rotation) % 360;
  const viewport = page.getViewport({ scale: zoom, rotation: effectiveRotation });
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { alpha: false });
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  await page.render({ canvasContext: context, viewport }).promise;
  return { canvas, viewport, page };
}

export async function getNativeWords(page) {
  const content = await page.getTextContent();
  const viewport = page.getViewport({ scale: 1 });
  const words = [];
  for (const item of content.items) {
    if (!item.str || !item.str.trim() || !item.transform) continue;
    const tx = item.transform;
    const height = Math.hypot(tx[2], tx[3]) || item.height || 10;
    const baseline = tx[5];
    const startX = tx[4];
    const totalWidth = Math.max(0, item.width || 0);
    const text = item.str.trim();
    const pieces = text.match(/\S+/g) || [];
    const spans = pieces.length <= 1 || totalWidth <= 0
      ? [{ text, from: 0, to: text.length }]
      : pieces.map((piece) => {
          const from = text.indexOf(piece);
          return { text: piece, from, to: from + piece.length };
        });
    for (const part of spans) {
      const from = totalWidth > 0 ? part.from / text.length : 0;
      const to = totalWidth > 0 ? part.to / text.length : 1;
      const x0 = startX + totalWidth * from;
      const x1 = startX + totalWidth * to;
      const pdfCorners = [
        [x0, baseline - height * 0.22], [x1, baseline - height * 0.22],
        [x1, baseline + height * 0.78], [x0, baseline + height * 0.78]
      ];
      const screenCorners = pdfCorners.map(([x, y]) => viewport.convertToViewportPoint(x, y));
      const xs = screenCorners.map((point) => point[0]);
      const ys = screenCorners.map((point) => point[1]);
      words.push({ x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys), text: part.text });
    }
  }
  return words;
}

export async function makeRasterPdf(sourcePdf, replacementPages) {
  const out = await PDFDocument.create();
  for (let i = 0; i < sourcePdf.numPages; i++) {
    const replacement = replacementPages.get(i);
    let canvas = replacement;
    if (!canvas) {
      const rendered = await renderPage(sourcePdf, i, 2, 0);
      canvas = rendered.canvas;
    }
    const png = await out.embedPng(canvas.toDataURL('image/png'));
    const width = canvas.width / 2;
    const height = canvas.height / 2;
    const page = out.addPage([width, height]);
    page.drawImage(png, { x: 0, y: 0, width, height });
  }
  return out.save();
}

export async function rotatePdf(bytes, rotations) {
  const doc = await PDFDocument.load(bytes);
  doc.getPages().forEach((page, i) => {
    const next = ((page.getRotation().angle + (rotations.get(i) || 0)) % 360 + 360) % 360;
    page.setRotation(degrees(next));
  });
  return doc.save();
}

export async function extractPages(bytes, order) {
  const source = await PDFDocument.load(bytes);
  const out = await PDFDocument.create();
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
