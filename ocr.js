let enginePromise;

export async function getOcrEngine(onStatus = () => {}) {
  if (!enginePromise) {
    enginePromise = (async () => {
      onStatus('Loading PaddleOCR browser models (first use may take a little while)...');
      // PP-OCRv6 uses a unified multilingual recognition model covering English,
      // French and other Latin-script languages. Inference stays in this browser.
      const { PaddleOCR } = await import('https://esm.sh/@paddleocr/paddleocr-js@0.4.2?bundle');
      const engine = await PaddleOCR.create({
        lang: 'en',
        ocrVersion: 'PP-OCRv6',
        worker: typeof OffscreenCanvas !== 'undefined',
        ortOptions: { backend: 'auto' }
      });
      onStatus('PaddleOCR is ready.');
      return engine;
    })().catch((error) => {
      enginePromise = null;
      throw error;
    });
  }
  return enginePromise;
}

export async function recognizeCanvas(canvas, onStatus = () => {}) {
  const engine = await getOcrEngine(onStatus);
  const blob = await new Promise((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error('Could not prepare page image for OCR.')), 'image/png'));
  const [result] = await engine.predict(blob, { textRecScoreThresh: 0.1 });
  return (result?.items || []).map((item) => {
    const points = item.poly || [];
    const xs = points.map((p) => Array.isArray(p) ? p[0] : p.x);
    const ys = points.map((p) => Array.isArray(p) ? p[1] : p.y);
    return {
      text: String(item.text || '').trim(),
      confidence: Number(item.score || 0) * 100,
      x0: Math.min(...xs), y0: Math.min(...ys),
      x1: Math.max(...xs), y1: Math.max(...ys)
    };
  }).filter((item) => item.text && Number.isFinite(item.x0));
}
