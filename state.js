export const state = {
  activeTool: null,
  file: null,
  bytes: null,
  pdf: null,
  pageIndex: 0,
  pageCount: 0,
  pageImages: new Map(),
  toolState: {
    redact: { boxes: new Map(), first: new Map() },
    xlsx: { columns: new Map(), dragging: null, ocrStats: {} },
    rotate: { angles: new Map() },
    annotate: { objects: new Map(), settings: { tool: 'select', color: '#000000', thickness: 2, font: 'Arial', fontSize: 18, bold: false, italic: false, underline: false }, images: new Map(), history: new Map(), selected: new Map() },
    split: { selected: [], order: [] }
  }
};

export function resetDocument(keepTool = true) {
  if (state.pdf) { try { const closing = state.pdf.destroy(); closing?.catch?.(() => {}); } catch {} }
  const tool = keepTool ? state.activeTool : null;
  state.file = null;
  state.bytes = null;
  state.pdf = null;
  state.pageIndex = 0;
  state.pageCount = 0;
  state.pageImages.clear();
  state.toolState = {
    redact: { boxes: new Map(), first: new Map() },
    xlsx: { columns: new Map(), dragging: null, ocrStats: {} },
    rotate: { angles: new Map() },
    annotate: { objects: new Map(), settings: { tool: 'select', color: '#000000', thickness: 2, font: 'Arial', fontSize: 18, bold: false, italic: false, underline: false }, images: new Map(), history: new Map(), selected: new Map() },
    split: { selected: [], order: [] }
  };
  state.activeTool = tool;
}
