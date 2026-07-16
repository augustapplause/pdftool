# PDF Toolkit - v0.7
# Annotation fixes: preserve select-mode edits, object angles, and avoid component reloads on tool-only changes.

import base64
import gc
import os
import html
import json
import hashlib
import re
import tempfile
import time
from io import BytesIO

import fitz
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

try:
    import easyocr
except Exception:
    easyocr = None

try:
    import numpy as np
except Exception:
    np = None


st.set_page_config(page_title="PDF Redactor / Annotator / PDF to XLSX", layout="wide")

st.title("PDF Redactor / Annotator / PDF to XLSX")

st.markdown("""
<style>

/* Radio button label */
div[role="radiogroup"] label {
    font-size: 2rem !important;
}

/* Choose Function label */
div[data-testid="stRadio"] > label {
    font-size: 2rem !important;
    font-weight: 600 !important;
}

/* Upload PDF label */
div[data-testid="stFileUploader"] > label {
    font-size: 2rem !important;
    font-weight: 600 !important;
}

/* Upload box text */
div[data-testid="stFileUploader"] small,
div[data-testid="stFileUploader"] span,
div[data-testid="stFileUploader"] p {
    font-size: 1.5rem !important;
}

/* Browse files button */
div[data-testid="stFileUploader"] button {
    font-size: 1.5rem !important;
}

/* Success messages */
div[data-testid="stAlert"] {
    font-size: 1.3rem !important;
}

/* Stable XLSX preview rendering */
.xlsx-scroll-window {
    width: 100%;
    max-width: 100%;
    height: 650px;
    overflow: auto;
    border: 1px solid #cccccc;
    padding: 0;
    background: #ffffff;
}

.xlsx-scroll-window img {
    display: block;
    max-width: none !important;
}

/* XLSX markup is rendered inside a Streamlit components iframe.
   The iframe itself is the independent PDF scroll window. */
.xlsx-frame-note {
    font-size: 0.9rem;
    color: #555555;
    margin-top: 0.35rem;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# Session / memory cleanup helpers
# ============================================================


def hard_reset_app():
    """Clear Streamlit state and rotate the uploader key so the uploaded file is released."""
    next_upload_key = st.session_state.get("uploader_key", 0) + 1
    st.session_state.clear()
    st.session_state["uploader_key"] = next_upload_key
    gc.collect()
    st.rerun()


if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

if "redactions" not in st.session_state:
    st.session_state.redactions = {}

if "columns" not in st.session_state:
    st.session_state.columns = {}

if "last_ruler_click" not in st.session_state:
    st.session_state.last_ruler_click = {}

if "last_redaction_click" not in st.session_state:
    st.session_state.last_redaction_click = {}

if "redaction_first_corner" not in st.session_state:
    st.session_state.redaction_first_corner = {}

if "annotations" not in st.session_state:
    st.session_state.annotations = {}

if "annotation_first_point" not in st.session_state:
    st.session_state.annotation_first_point = {}

if "last_annotation_click" not in st.session_state:
    st.session_state.last_annotation_click = {}

if "selected_annotation_id" not in st.session_state:
    st.session_state.selected_annotation_id = {}

if "ocr_reader" not in st.session_state:
    st.session_state.ocr_reader = None


with st.sidebar:
    st.subheader("Privacy / Cleanup")
    st.caption(
        "This version does not intentionally save uploaded PDFs or generated files. "
        "Use this button after downloading your result to clear the session state and release in-memory objects."
    )
    if st.button("Clear uploaded PDF and reset app"):
        hard_reset_app()


task = st.radio(
    "Choose Function",
    ["Redact PDF", "Annotate PDF", "Convert PDF to XLSX", "Convert PDF to Plain Text"],
    horizontal=True,
    key="task_choice",
)

uploaded = st.file_uploader(
    "Upload PDF",
    type=["pdf"],
    key=f"pdf_upload_{st.session_state.uploader_key}",
)

if uploaded is not None:
    st.session_state["pdf_bytes"] = uploaded.getvalue()
    st.session_state["pdf_name"] = uploaded.name

if uploaded is None and "pdf_bytes" not in st.session_state:
    st.stop()

# Remove stale ruler-click URL parameters from older versions.
if "col_click" in st.query_params:
    st.query_params.clear()

pdf_bytes = st.session_state["pdf_bytes"]

# Open the document only long enough to validate it, get page count, and render the selected page.
try:
    view_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(view_doc)
except Exception as e:
    st.error(f"Unable to open PDF: {e}")
    gc.collect()
    st.stop()

st.success(f"Loaded PDF with {page_count} page(s).")

zoom = 1.5

if "active_page_num" not in st.session_state:
    st.session_state.active_page_num = 0

st.session_state.active_page_num = max(0, min(int(st.session_state.active_page_num), page_count - 1))
page_num = st.session_state.active_page_num

try:
    page = view_doc[page_num]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    page_image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
finally:
    view_doc.close()
    del view_doc
    gc.collect()

st.session_state.redactions.setdefault(page_num, [])
st.session_state.columns.setdefault(page_num, [])
st.session_state.annotations.setdefault(page_num, [])


# ============================================================
# Status summary helpers
# ============================================================


def get_page_count_summary(mapping, page_count, item_label):
    """Return human-readable per-page counts for saved redactions/columns."""
    lines = []

    for pnum in range(page_count):
        count = len(mapping.get(pnum, []))
        lines.append(f"Page {pnum + 1}: {count} {item_label if count == 1 else item_label + 's'}")

    return lines


def show_document_status(page_count):
    """Display saved redaction, annotation, and column-marker counts for every page."""
    st.subheader("Document Status")

    redaction_lines = get_page_count_summary(
        st.session_state.redactions,
        page_count,
        "redaction",
    )

    annotation_lines = get_page_count_summary(
        st.session_state.annotations,
        page_count,
        "annotation",
    )

    column_lines = get_page_count_summary(
        st.session_state.columns,
        page_count,
        "column marker",
    )

    status_col1, status_col2, status_col3 = st.columns(3)

    with status_col1:
        st.markdown("**Redactions**")
        for line in redaction_lines:
            st.write(line)

    with status_col2:
        st.markdown("**Annotations**")
        for line in annotation_lines:
            st.write(line)

    with status_col3:
        st.markdown("**Column markers**")
        for line in column_lines:
            st.write(line)


show_document_status(page_count)

# ============================================================
# UI drawing helpers
# ============================================================


def make_ruler(width, height=70, columns=None):
    columns = columns or []

    ruler = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(ruler)

    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=(80, 80, 80), width=1)

    for x in range(0, width, 10):
        tick_height = 25 if x % 50 == 0 else 10
        draw.line([(x, 0), (x, tick_height)], fill=(0, 0, 0), width=1)

        if x % 50 == 0:
            draw.text((x + 3, 30), str(x), fill=(0, 0, 0))

    for x in columns:
        draw.line([(int(x), 0), (int(x), height)], fill=(0, 0, 255), width=4)
        draw.ellipse([(int(x) - 6, height - 18), (int(x) + 6, height - 6)], fill=(0, 0, 255))

    return ruler


def draw_column_overlay(image, columns):
    preview = image.copy()
    draw = ImageDraw.Draw(preview)

    for x in columns:
        draw.line(
            [(int(x), 0), (int(x), preview.height)],
            fill=(0, 0, 255),
            width=4,
        )

    return preview


def draw_redaction_overlay(image, rects, first_corner=None):
    preview = image.copy()
    draw = ImageDraw.Draw(preview)

    for r in rects:
        x0 = int(r["x"])
        y0 = int(r["y"])
        x1 = int(r["x"] + r["w"])
        y1 = int(r["y"] + r["h"])
        draw.rectangle([(x0, y0), (x1, y1)], fill=(0, 0, 0))

    if first_corner is not None:
        x = int(first_corner["x"])
        y = int(first_corner["y"])
        draw.ellipse([(x - 8, y - 8), (x + 8, y + 8)], fill=(255, 0, 0))
        draw.text((x + 10, y + 10), "1st corner", fill=(255, 0, 0))

    return preview




# ============================================================
# Annotation helpers
# ============================================================

ANNOTATION_COLOR_CHOICES = {
    "Red": (255, 0, 0),
    "Blue": (0, 0, 255),
    "Green": (0, 128, 0),
    "Black": (0, 0, 0),
    "Yellow": (255, 215, 0),
}

ANNOTATION_COLOR_CHOICES_PDF = {
    name: (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    for name, rgb in ANNOTATION_COLOR_CHOICES.items()
}


def hex_to_pdf_rgb(hex_color, fallback=(1, 0, 0)):
    try:
        value = str(hex_color or "").strip().lstrip("#")
        if len(value) != 6:
            return fallback
        return (
            int(value[0:2], 16) / 255,
            int(value[2:4], 16) / 255,
            int(value[4:6], 16) / 255,
        )
    except Exception:
        return fallback


def get_pdf_font_name(font_family, bold=False, italic=False):
    """Return a built-in PDF font. Helvetica is the PDF-safe Arial/sans-serif equivalent."""
    if bold and italic:
        return "hebi"
    if bold:
        return "hebo"
    if italic:
        return "heit"
    return "helv"


def draw_arrowhead(draw, x0, y0, x1, y1, fill, width=3):
    import math

    angle = math.atan2(y1 - y0, x1 - x0)
    head_len = max(12, width * 5)
    head_angle = math.radians(28)

    p1 = (
        x1 - head_len * math.cos(angle - head_angle),
        y1 - head_len * math.sin(angle - head_angle),
    )
    p2 = (
        x1 - head_len * math.cos(angle + head_angle),
        y1 - head_len * math.sin(angle + head_angle),
    )
    draw.line([(x1, y1), p1], fill=fill, width=width)
    draw.line([(x1, y1), p2], fill=fill, width=width)


def normalize_annotation_for_component(ann):
    ann = dict(ann)
    ann.setdefault("id", f"ann_{int(time.time() * 1000)}_{len(str(ann))}")
    ann.setdefault("kind", "textbox")
    ann.setdefault("color", "Custom")
    ann.setdefault("custom_color", "#ff0000")
    ann.setdefault("line_width", 3)
    ann.setdefault("angle", 0)
    ann.setdefault("text", "")
    ann.setdefault("font_family", "Sans Serif")
    ann.setdefault("font_size", 18)
    ann.setdefault("bold", False)
    ann.setdefault("italic", False)
    ann.setdefault("underline", False)
    return ann



def get_annotation_component():
    """Create/load a local Streamlit component backed by Fabric.js."""
    component_dir = os.path.join(tempfile.gettempdir(), "pdf_fabric_annotation_component_v7")
    os.makedirs(component_dir, exist_ok=True)
    index_path = os.path.join(component_dir, "index.html")

    component_html = """
<!doctype html>
<html>
<head>
<meta charset=\"utf-8\">
<script src=\"https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js\"></script>
<style>
html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #ffffff; font-family: Arial, sans-serif; }
#scroll-window { width: 100%; height: 780px; overflow: auto; border: 1px solid #cccccc; background: #f7f7f7; box-sizing: border-box; }
#content { position: relative; background: #ffffff; }
#hint { position: sticky; left: 0; top: 0; z-index: 10; height: 34px; box-sizing: border-box; background: rgba(255,255,255,0.96); padding: 8px 10px; font-size: 13px; color: #333333; border-bottom: 1px solid #dddddd; }
canvas { display: block; }
</style>
</head>
<body>
<div id=\"scroll-window\"><div id=\"content\"><div id=\"hint\">Loading annotation editor...</div><canvas id=\"pdf-canvas\"></canvas></div></div>
<script>
let canvas = null, args = {}, isDrawing = false, startPoint = null, tempObject = null, isLoading = false;
let loadedPageNum = null, loadedSignature = null, loadedWidth = null, loadedHeight = null;
function sendMessageToStreamlitClient(type, data) { window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), \"*\"); }
function setFrameHeight(height) { sendMessageToStreamlitClient(\"streamlit:setFrameHeight\", { height: height }); }
function setComponentValue(value) { sendMessageToStreamlitClient(\"streamlit:setComponentValue\", { value: value, dataType: \"json\" }); }
function nonce() { return Date.now() + \"_\" + Math.random().toString(36).slice(2); }
function makeId() { return \"ann_\" + nonce(); }
function tool() { return args.tool || \"select\"; }
function color() { return args.color_hex || \"#ff0000\"; }
function lineWidth() { return Math.max(1, Number(args.line_width || 3)); }
function updateHint() {
  const el = document.getElementById(\"hint\"); const t = tool();
  if (t === \"select\") el.textContent = \"Select / Move: drag annotations, resize with handles, press Delete or Backspace to remove selected objects.\";
  else if (t === \"textbox\") el.textContent = \"Text: click the PDF and type directly. Click outside the text box to save it.\";
  else el.textContent = \"Draw: drag on the PDF to create the selected shape. Switch to Select / Move to reposition or resize it.\";
}
function fabricPoint(opt) { const pointer = canvas.getPointer(opt.e); return { x: pointer.x, y: pointer.y }; }
function setCommon(obj, kind, id) { obj.set({ id: id || makeId(), kind: kind, transparentCorners: false, cornerSize: 9, borderColor: \"#0066ff\", cornerColor: \"#ffffff\", cornerStrokeColor: \"#0066ff\", objectCaching: false }); return obj; }
function arrowRender(ctx) {
  fabric.Line.prototype._render.call(this, ctx);
  const xDiff = this.x2 - this.x1, yDiff = this.y2 - this.y1;
  const angle = Math.atan2(yDiff, xDiff); const head = Math.max(12, Number(this.line_width || this.strokeWidth || 3) * 5);
  ctx.save(); ctx.translate((this.x2 - this.x1) / 2, (this.y2 - this.y1) / 2); ctx.rotate(angle);
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(-head, head / 2.4); ctx.lineTo(-head, -head / 2.4); ctx.closePath(); ctx.fillStyle = this.stroke || \"#ff0000\"; ctx.fill(); ctx.restore();
}
function createFabricObject(a) {
  const kind = a.kind || \"textbox\", id = a.id || makeId(), stroke = a.custom_color || a.color_hex || \"#ff0000\", width = Math.max(1, Number(a.line_width || 3));
  const x0 = Number(a.x0 || 0), y0 = Number(a.y0 || 0), x1 = Number(a.x1 || x0), y1 = Number(a.y1 || y0), angle = Number(a.angle || 0);
  if (kind === \"textbox\") { const txt = new fabric.IText(a.text || \"\", { left: x0, top: y0, angle: angle, fill: stroke, fontFamily: a.font_family === \"Arial\" ? \"Arial\" : \"sans-serif\", fontSize: Number(a.font_size || 18), fontWeight: a.bold ? \"bold\" : \"normal\", fontStyle: a.italic ? \"italic\" : \"normal\", underline: !!a.underline, backgroundColor: \"\", stroke: null, strokeWidth: 0, padding: 0, objectCaching: false }); txt.set({ font_family: a.font_family || \"Sans Serif\", custom_color: stroke, line_width: 1 }); return setCommon(txt, \"textbox\", id); }
  if (kind === \"line\" || kind === \"arrow\") { const line = new fabric.Line([x0, y0, x1, y1], { stroke: stroke, strokeWidth: width, fill: stroke, objectCaching: false, strokeUniform: true }); line.set({ custom_color: stroke, line_width: width }); if (kind === \"arrow\") line._render = arrowRender; return setCommon(line, kind, id); }
  if (kind === \"rectangle\") { const rect = new fabric.Rect({ left: x0, top: y0, angle: angle, width: Math.max(1, Math.abs(x1 - x0)), height: Math.max(1, Math.abs(y1 - y0)), fill: \"rgba(0,0,0,0)\", stroke: stroke, strokeWidth: width, strokeUniform: true, objectCaching: false }); rect.set({ custom_color: stroke, line_width: width }); return setCommon(rect, kind, id); }
  if (kind === \"circle\") { const ell = new fabric.Ellipse({ left: x0, top: y0, angle: angle, rx: Math.max(1, Math.abs(x1 - x0) / 2), ry: Math.max(1, Math.abs(y1 - y0) / 2), fill: \"rgba(0,0,0,0)\", stroke: stroke, strokeWidth: width, strokeUniform: true, objectCaching: false }); ell.set({ custom_color: stroke, line_width: width }); return setCommon(ell, kind, id); }
  return null;
}
function lineEndpoints(obj) {
  // Fabric.Line stores endpoints in the line's own coordinate system after
  // selection/move/scale. calcLinePoints() returns those local endpoint points;
  // calcTransformMatrix() maps them back to canvas coordinates. This is more
  // reliable than reading obj.x1/obj.y1/obj.x2/obj.y2 directly after edits.
  const m = obj.calcTransformMatrix();
  let lp = null;
  if (typeof obj.calcLinePoints === "function") lp = obj.calcLinePoints();
  if (!lp) lp = { x1: obj.x1 || 0, y1: obj.y1 || 0, x2: obj.x2 || 0, y2: obj.y2 || 0 };
  const p1 = fabric.util.transformPoint(new fabric.Point(lp.x1, lp.y1), m);
  const p2 = fabric.util.transformPoint(new fabric.Point(lp.x2, lp.y2), m);
  return { x0: p1.x, y0: p1.y, x1: p2.x, y1: p2.y };
}
function normalizeScaledObject(obj) {
  const kind = obj.kind || \"\";
  if (kind === \"textbox\") { const sx = Number(obj.scaleX || 1), sy = Number(obj.scaleY || 1); if (Math.abs(sx - 1) > 0.001 || Math.abs(sy - 1) > 0.001) { obj.set({ fontSize: Math.max(1, Math.round(Number(obj.fontSize || 18) * Math.max(sx, sy))), scaleX: 1, scaleY: 1 }); obj.setCoords(); } }
  else if (kind === \"rectangle\") { const sx = Number(obj.scaleX || 1), sy = Number(obj.scaleY || 1); if (Math.abs(sx - 1) > 0.001 || Math.abs(sy - 1) > 0.001) { obj.set({ width: Math.max(1, obj.width * sx), height: Math.max(1, obj.height * sy), scaleX: 1, scaleY: 1 }); obj.setCoords(); } }
  else if (kind === \"circle\") { const sx = Number(obj.scaleX || 1), sy = Number(obj.scaleY || 1); if (Math.abs(sx - 1) > 0.001 || Math.abs(sy - 1) > 0.001) { obj.set({ rx: Math.max(1, obj.rx * sx), ry: Math.max(1, obj.ry * sy), scaleX: 1, scaleY: 1 }); obj.setCoords(); } }
}
function serializeObject(obj) {
  normalizeScaledObject(obj);
  const kind = obj.kind || "textbox";
  if (kind === "textbox") return { id: obj.id || makeId(), kind: "textbox", x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + Number(obj.width || obj.getScaledWidth() || 1), y1: Number(obj.top || 0) + Number(obj.height || obj.getScaledHeight() || 1), angle: Number(obj.angle || 0), text: obj.text || "", custom_color: obj.custom_color || obj.fill || "#ff0000", line_width: 1, font_family: obj.font_family || (obj.fontFamily === "Arial" ? "Arial" : "Sans Serif"), font_size: Number(obj.fontSize || 18), bold: obj.fontWeight === "bold", italic: obj.fontStyle === "italic", underline: obj.underline === true };
  if (kind === "line" || kind === "arrow") { const p = lineEndpoints(obj); return { id: obj.id || makeId(), kind: kind, x0: Number(p.x0), y0: Number(p.y0), x1: Number(p.x1), y1: Number(p.y1), angle: 0, custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) }; }
  if (kind === "rectangle") return { id: obj.id || makeId(), kind: kind, x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + Number(obj.width || 1), y1: Number(obj.top || 0) + Number(obj.height || 1), angle: Number(obj.angle || 0), custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) };
  if (kind === "circle") return { id: obj.id || makeId(), kind: kind, x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + 2 * Number(obj.rx || 1), y1: Number(obj.top || 0) + 2 * Number(obj.ry || 1), angle: Number(obj.angle || 0), custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) };
  return null;
}
function serializeAnnotations() { return canvas.getObjects().filter(o => o.kind).map(serializeObject).filter(Boolean); }
function emit(action) { if (isLoading) return; const active = canvas.getActiveObject(); setComponentValue({ page_num: args.page_num, action: action, annotations: serializeAnnotations(), selected_id: active && active.id ? active.id : null, nonce: nonce() }); }
function installCanvasEvents() {
  canvas.on(\"object:modified\", function(opt) { if (opt && opt.target) normalizeScaledObject(opt.target); canvas.requestRenderAll(); emit(\"modify\"); });
  canvas.on(\"editing:exited\", function(opt) { if (opt && opt.target && opt.target.kind === \"textbox\") { if (!(opt.target.text || \"\").trim()) canvas.remove(opt.target); emit(\"text_done\"); } });
  canvas.on(\"mouse:down\", function(opt) { if (tool() === \"select\") return; const p = fabricPoint(opt); if (tool() === \"textbox\") { const obj = createFabricObject({ kind: \"textbox\", x0: p.x, y0: p.y, x1: p.x + 80, y1: p.y + 24, text: \"\", custom_color: color(), font_family: args.font_family || \"Sans Serif\", font_size: args.font_size || 18, bold: !!args.bold, italic: !!args.italic, underline: !!args.underline }); canvas.add(obj); canvas.setActiveObject(obj); obj.enterEditing(); if (obj.hiddenTextarea) obj.hiddenTextarea.focus(); canvas.requestRenderAll(); return; } isDrawing = true; startPoint = p; tempObject = createFabricObject({ kind: tool(), x0: p.x, y0: p.y, x1: p.x, y1: p.y, custom_color: color(), line_width: lineWidth() }); if (tempObject) { tempObject.selectable = false; tempObject.evented = false; canvas.add(tempObject); } });
  canvas.on(\"mouse:move\", function(opt) {
    if (!isDrawing || !tempObject || !startPoint) return;
    const p = fabricPoint(opt);
    const kind = tempObject.kind;

    // Fabric.Line does not reliably recompute its internal width/height when
    // x2/y2 are mutated during a drag. Recreate the temporary line/arrow on
    // each drag frame so the final object has stable endpoints before emit().
    if (kind === \"line\" || kind === \"arrow\") {
      canvas.remove(tempObject);
      tempObject = createFabricObject({
        kind: kind,
        x0: startPoint.x,
        y0: startPoint.y,
        x1: p.x,
        y1: p.y,
        custom_color: color(),
        line_width: lineWidth()
      });
      tempObject.selectable = false;
      tempObject.evented = false;
      canvas.add(tempObject);
    } else if (kind === \"rectangle\") {
      tempObject.set({ left: Math.min(startPoint.x, p.x), top: Math.min(startPoint.y, p.y), width: Math.max(1, Math.abs(p.x - startPoint.x)), height: Math.max(1, Math.abs(p.y - startPoint.y)) });
    } else if (kind === \"circle\") {
      tempObject.set({ left: Math.min(startPoint.x, p.x), top: Math.min(startPoint.y, p.y), rx: Math.max(1, Math.abs(p.x - startPoint.x) / 2), ry: Math.max(1, Math.abs(p.y - startPoint.y) / 2) });
    }
    tempObject.setCoords();
    canvas.requestRenderAll();
  });
  canvas.on(\"mouse:up\", function() {
    if (!isDrawing) return;
    isDrawing = false;
    if (tempObject) {
      tempObject.selectable = true;
      tempObject.evented = true;
      canvas.setActiveObject(tempObject);
      tempObject.setCoords();
      const br = tempObject.getBoundingRect(true, true);
      if (br.width < 4 && br.height < 4) canvas.remove(tempObject);
      canvas.requestRenderAll();
      window.setTimeout(function() { emit(\"create\"); }, 0);
    }
    tempObject = null;
    startPoint = null;
  });
}
function deleteSelection() { const active = canvas.getActiveObject(); if (!active) return; if (active.type === \"activeSelection\") active.forEachObject(o => canvas.remove(o)); else canvas.remove(active); canvas.discardActiveObject(); canvas.requestRenderAll(); emit(\"delete\"); }
window.addEventListener(\"keydown\", function(e) { if (!canvas) return; const active = canvas.getActiveObject(); if (active && active.isEditing) return; if (e.key === \"Delete\" || e.key === \"Backspace\") { e.preventDefault(); deleteSelection(); } });
function applyMode() { const selectable = tool() === \"select\"; canvas.selection = selectable; canvas.getObjects().forEach(o => { if (o.kind) { o.selectable = selectable; o.evented = true; } }); canvas.defaultCursor = selectable ? \"default\" : \"crosshair\"; canvas.hoverCursor = selectable ? \"move\" : \"crosshair\"; updateHint(); canvas.requestRenderAll(); }
function render(argsIn) {
  const newArgs = argsIn || {};
  const width = Number(newArgs.width || 800), height = Number(newArgs.preview_height || 1000);
  const signature = String(newArgs.annotations_signature || "");
  const pageNum = Number(newArgs.page_num || 0);
  const content = document.getElementById("content");
  content.style.width = width + "px";
  content.style.minHeight = (height + 34) + "px";
  if (!window.fabric) { document.getElementById("hint").textContent = "Fabric.js could not be loaded. Check internet/CDN access or bundle fabric.min.js locally."; setFrameHeight(800); return; }
  if (!canvas) { canvas = new fabric.Canvas("pdf-canvas", { preserveObjectStacking: true, selection: true }); installCanvasEvents(); }

  const needsReload = loadedPageNum !== pageNum || loadedSignature !== signature || loadedWidth !== width || loadedHeight !== height;
  args = newArgs;
  updateHint();
  if (!needsReload) { applyMode(); setFrameHeight(800); return; }

  isLoading = true;
  loadedPageNum = pageNum; loadedSignature = signature; loadedWidth = width; loadedHeight = height;
  canvas.setWidth(width); canvas.setHeight(height); canvas.clear();
  fabric.Image.fromURL("data:image/png;base64," + args.preview_b64, function(img) {
    img.set({ left: 0, top: 0, selectable: false, evented: false, originX: "left", originY: "top" });
    img.scaleX = 1; img.scaleY = 1;
    canvas.setBackgroundImage(img, function() {
      (args.annotations || []).forEach(a => { const obj = createFabricObject(a); if (obj) canvas.add(obj); });
      isLoading = false; applyMode(); canvas.requestRenderAll(); setFrameHeight(800);
    }, { originX: "left", originY: "top" });
  }, { crossOrigin: "anonymous" });
}
window.addEventListener(\"message\", function(event) { if (event.data && event.data.type === \"streamlit:render\") render(event.data.args || {}); });
sendMessageToStreamlitClient(\"streamlit:componentReady\", { apiVersion: 1 }); setFrameHeight(800);
</script>
</body>
</html>
"""

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(component_html)

    return components.declare_component("pdf_fabric_annotation_component_v7", path=component_dir)


def annotation_signature(annotations):
    """Stable signature so the Fabric component only reloads objects when saved objects actually change."""
    try:
        payload = json.dumps(annotations, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        payload = repr(annotations)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def show_draggable_annotation_page(page_num, base_image, annotations, settings, selected_id=None, caption=None):
    '''Render a scrollable Fabric.js PDF annotation editor.'''
    preview_b64 = image_to_base64_png(base_image)
    component = get_annotation_component()

    result = component(
        page_num=page_num,
        width=base_image.width,
        preview_height=base_image.height,
        preview_b64=preview_b64,
        annotations=[normalize_annotation_for_component(a) for a in annotations],
        annotations_signature=annotation_signature([normalize_annotation_for_component(a) for a in annotations]),
        selected_id=selected_id,
        tool=settings.get("tool", "select"),
        color_hex=settings.get("color_hex", "#ff0000"),
        line_width=int(settings.get("line_width", 3)),
        text=settings.get("text", ""),
        font_family=settings.get("font_family", "Sans Serif"),
        font_size=int(settings.get("font_size", 18)),
        bold=bool(settings.get("bold", False)),
        italic=bool(settings.get("italic", False)),
        underline=bool(settings.get("underline", False)),
        key=f"pdf_fabric_annotation_component_page_{page_num}",
        default=None,
    )

    if caption:
        st.caption(caption)

    return result

def add_annotation_click(page_num, clicked_x, clicked_y, settings):
    tool = settings["tool"]

    if tool in ["line", "arrow", "circle", "rectangle", "textbox"]:
        first_point = st.session_state.annotation_first_point.get(page_num)

        if first_point is None:
            st.session_state.annotation_first_point[page_num] = {"x": clicked_x, "y": clicked_y}
            return

        x0 = first_point["x"]
        y0 = first_point["y"]
        x1 = clicked_x
        y1 = clicked_y

        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
            st.session_state.annotation_first_point.pop(page_num, None)
            return

        if tool in ["circle", "rectangle", "textbox"]:
            x0, x1 = sorted([x0, x1])
            y0, y1 = sorted([y0, y1])

        annotation = {
            "kind": tool,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "color": settings["color"],
            "line_width": settings["line_width"],
        }

        if tool == "textbox":
            annotation.update({
                "text": settings["text"],
                "font_family": settings["font_family"],
                "font_size": settings["font_size"],
                "bold": settings["bold"],
                "italic": settings["italic"],
                "underline": settings["underline"],
            })

        st.session_state.annotations[page_num].append(annotation)
        st.session_state.annotation_first_point.pop(page_num, None)


def draw_pdf_arrow(page, p0, p1, color, width):
    import math

    page.draw_line(p0, p1, color=color, width=width)
    angle = math.atan2(p1.y - p0.y, p1.x - p0.x)
    head_len = max(8, width * 4)
    head_angle = math.radians(28)

    a = fitz.Point(
        p1.x - head_len * math.cos(angle - head_angle),
        p1.y - head_len * math.sin(angle - head_angle),
    )
    b = fitz.Point(
        p1.x - head_len * math.cos(angle + head_angle),
        p1.y - head_len * math.sin(angle + head_angle),
    )
    page.draw_line(p1, a, color=color, width=width)
    page.draw_line(p1, b, color=color, width=width)


def apply_annotations_to_pdf(pdf_bytes, annotations_by_page, zoom):
    annotated_doc = None
    output_pdf = BytesIO()

    try:
        annotated_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        for pnum, annotations in annotations_by_page.items():
            if pnum >= len(annotated_doc):
                continue

            page = annotated_doc[pnum]

            for ann in annotations:
                color = hex_to_pdf_rgb(
                    ann.get("custom_color"),
                    ANNOTATION_COLOR_CHOICES_PDF.get(ann.get("color", "Red"), (1, 0, 0)),
                )
                width = float(ann.get("line_width", 3)) / zoom
                kind = ann.get("kind")

                x0 = ann["x0"] / zoom
                y0 = ann["y0"] / zoom
                x1 = ann["x1"] / zoom
                y1 = ann["y1"] / zoom

                if kind == "textbox":
                    text_value = ann.get("text", "")
                    fontsize = float(ann.get("font_size", 12)) / zoom
                    fontname = get_pdf_font_name(
                        ann.get("font_family", "Sans Serif"),
                        ann.get("bold", False),
                        ann.get("italic", False),
                    )
                    # Text annotations intentionally have no rectangle, border, or fill.
                    # The large text box rectangle is only used internally to support wrapping.
                    line_count = max(1, len(str(text_value).splitlines()))
                    rect = fitz.Rect(
                        x0,
                        y0,
                        page.rect.width,
                        min(page.rect.height, y0 + (fontsize * 1.35 * line_count) + fontsize),
                    )
                    page.insert_textbox(
                        rect,
                        text_value,
                        fontsize=fontsize,
                        fontname=fontname,
                        color=color,
                        align=fitz.TEXT_ALIGN_LEFT,
                    )

                    if ann.get("underline"):
                        line_height = fontsize * 1.25
                        text_lines = str(text_value).splitlines() or [str(text_value)]
                        underline_y = y0 + fontsize + 2
                        for line in text_lines:
                            if underline_y < page.rect.height:
                                text_width = fitz.get_text_length(line, fontname=fontname, fontsize=fontsize)
                                page.draw_line(
                                    fitz.Point(x0, underline_y),
                                    fitz.Point(min(page.rect.width, x0 + text_width), underline_y),
                                    color=color,
                                    width=max(0.4, width / 2),
                                )
                            underline_y += line_height

                elif kind == "line":
                    page.draw_line(fitz.Point(x0, y0), fitz.Point(x1, y1), color=color, width=width)

                elif kind == "arrow":
                    draw_pdf_arrow(page, fitz.Point(x0, y0), fitz.Point(x1, y1), color, width)

                elif kind == "rectangle":
                    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=color, width=width)

                elif kind == "circle":
                    page.draw_oval(fitz.Rect(x0, y0, x1, y1), color=color, width=width)

        annotated_doc.save(output_pdf)
        output_pdf.seek(0)
        return output_pdf.getvalue()

    finally:
        if annotated_doc is not None:
            annotated_doc.close()
        output_pdf.close()
        gc.collect()

def image_to_base64_png(image):
    buffer = BytesIO()
    try:
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    finally:
        buffer.close()


def get_xlsx_ruler_component():
    """Create/load a tiny local Streamlit component for ruler clicks.

    This avoids URL navigation. The component sends clicked ruler x-values
    directly back to Python through Streamlit's component message protocol.
    """
    component_dir = os.path.join(tempfile.gettempdir(), "xlsx_ruler_component_v1")
    os.makedirs(component_dir, exist_ok=True)

    index_path = os.path.join(component_dir, "index.html")

    component_html = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
    html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #ffffff;
        font-family: sans-serif;
    }

    #scroll-window {
        width: 100%;
        height: 720px;
        overflow: auto;
        border: 1px solid #cccccc;
        background: #ffffff;
        box-sizing: border-box;
    }

    #content {
        position: relative;
        background: #ffffff;
    }

    #ruler {
        position: sticky;
        top: 0;
        z-index: 1000;
        background: #ffffff;
        border-bottom: 1px solid #777777;
        box-sizing: border-box;
    }

    #ruler-border {
        position: absolute;
        left: 0;
        top: 0;
        border: 1px solid #505050;
        box-sizing: border-box;
        pointer-events: none;
    }

    .tick {
        position: absolute;
        top: 0;
        width: 1px;
        background: #000000;
        pointer-events: none;
    }

    .label {
        position: absolute;
        top: 30px;
        font-size: 14px;
        color: #000000;
        pointer-events: none;
    }

    .marker-line {
        position: absolute;
        top: 0;
        width: 4px;
        background: #0000ff;
        z-index: 1500;
        pointer-events: none;
    }

    .marker-dot {
        position: absolute;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #0000ff;
        z-index: 1501;
        pointer-events: none;
    }

    .click-zone {
        position: absolute;
        top: 0;
        width: 5px;
        display: block;
        cursor: pointer;
        z-index: 2000;
    }

    .click-zone:hover {
        background: rgba(0, 0, 255, 0.10);
    }

    img {
        display: block;
        max-width: none;
    }
</style>
</head>
<body>
<div id="scroll-window">
    <div id="content"></div>
</div>

<script>
    function sendMessageToStreamlitClient(type, data) {
        window.parent.postMessage(
            Object.assign({ isStreamlitMessage: true, type: type }, data),
            "*"
        );
    }

    function setFrameHeight(height) {
        sendMessageToStreamlitClient("streamlit:setFrameHeight", { height: height });
    }

    function setComponentValue(value) {
        sendMessageToStreamlitClient("streamlit:setComponentValue", {
            value: value,
            dataType: "json"
        });
    }

    function buildRuler(args) {
        const width = args.width;
        const height = args.ruler_height;
        const columns = args.columns || [];
        const pageNum = args.page_num;

        const ruler = document.createElement("div");
        ruler.id = "ruler";
        ruler.style.width = width + "px";
        ruler.style.height = height + "px";

        const border = document.createElement("div");
        border.id = "ruler-border";
        border.style.width = (width - 1) + "px";
        border.style.height = (height - 1) + "px";
        ruler.appendChild(border);

        for (let x = 0; x < width; x += 10) {
            const tick = document.createElement("div");
            tick.className = "tick";
            tick.style.left = x + "px";
            tick.style.height = (x % 50 === 0 ? 25 : 10) + "px";
            ruler.appendChild(tick);

            if (x % 50 === 0) {
                const label = document.createElement("div");
                label.className = "label";
                label.style.left = (x + 3) + "px";
                label.textContent = String(x);
                ruler.appendChild(label);
            }
        }

        columns.forEach(function(rawX) {
            const x = Math.round(Number(rawX));

            const line = document.createElement("div");
            line.className = "marker-line";
            line.style.left = x + "px";
            line.style.height = height + "px";
            ruler.appendChild(line);

            const dot = document.createElement("div");
            dot.className = "marker-dot";
            dot.style.left = (x - 6) + "px";
            dot.style.top = (height - 18) + "px";
            ruler.appendChild(dot);
        });

        for (let x = 0; x < width; x += 5) {
            const zone = document.createElement("div");
            zone.className = "click-zone";
            zone.style.left = x + "px";
            zone.style.height = height + "px";
            zone.title = "Set/remove column at " + x;

            zone.addEventListener("click", function(event) {
                event.preventDefault();
                event.stopPropagation();

                setComponentValue({
                    page_num: pageNum,
                    x: x,
                    nonce: Date.now() + "_" + Math.random().toString(36).slice(2)
                });
            });

            ruler.appendChild(zone);
        }

        return ruler;
    }

    function render(args) {
        const width = args.width;
        const previewHeight = args.preview_height;
        const rulerHeight = args.ruler_height;

        const content = document.getElementById("content");
        content.innerHTML = "";
        content.style.width = width + "px";
        content.style.minHeight = (previewHeight + rulerHeight) + "px";

        content.appendChild(buildRuler(args));

        const img = document.createElement("img");
        img.src = "data:image/png;base64," + args.preview_b64;
        img.width = width;
        img.height = previewHeight;
        content.appendChild(img);

        setFrameHeight(735);
    }

    window.addEventListener("message", function(event) {
        if (event.data && event.data.type === "streamlit:render") {
            render(event.data.args || {});
        }
    });

    sendMessageToStreamlitClient("streamlit:componentReady", { apiVersion: 1 });
    setFrameHeight(735);
</script>
</body>
</html>
"""

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(component_html)

    return components.declare_component("xlsx_ruler_component", path=component_dir)


def show_scrollable_clickable_xlsx_markup(page_num, preview_image, columns, ruler_height=70, caption=None):
    """Render ruler and PDF in one independent scroll window.

    The ruler stays visible at the top of the PDF window while vertically
    scrolling and stays horizontally aligned with all PDF column lines.
    Ruler clicks are returned to Python without navigating away from the app.
    """
    preview_b64 = image_to_base64_png(preview_image)
    component = get_xlsx_ruler_component()

    click_result = component(
        page_num=page_num,
        width=preview_image.width,
        preview_height=preview_image.height,
        ruler_height=ruler_height,
        columns=[int(x) for x in columns],
        preview_b64=preview_b64,
        key=f"xlsx_ruler_component_page_{page_num}",
        default=None,
    )

    if caption:
        st.markdown(f'<div class="xlsx-frame-note">{caption}</div>', unsafe_allow_html=True)

    return click_result

def toggle_column(columns, clicked_x, tolerance=10):
    columns = list(columns)

    for existing_x in columns:
        if abs(existing_x - clicked_x) <= tolerance:
            columns.remove(existing_x)
            return sorted(columns)

    columns.append(clicked_x)
    return sorted(columns)


def add_redaction_click(page_num, clicked_x, clicked_y):
    first_corner = st.session_state.redaction_first_corner.get(page_num)

    if first_corner is None:
        st.session_state.redaction_first_corner[page_num] = {
            "x": clicked_x,
            "y": clicked_y,
        }
        return

    x0 = min(first_corner["x"], clicked_x)
    y0 = min(first_corner["y"], clicked_y)
    x1 = max(first_corner["x"], clicked_x)
    y1 = max(first_corner["y"], clicked_y)

    if abs(x1 - x0) >= 5 and abs(y1 - y0) >= 5:
        st.session_state.redactions[page_num].append(
            {
                "x": x0,
                "y": y0,
                "w": x1 - x0,
                "h": y1 - y0,
            }
        )

    st.session_state.redaction_first_corner.pop(page_num, None)


# ============================================================
# XLSX extraction helpers
# ============================================================


def group_words_into_rows(words, y_tolerance=4):
    rows = []

    for word in sorted(words, key=lambda w: (w[1], w[0])):
        x0, y0, x1, y1, text, *_ = word
        y_mid = (y0 + y1) / 2

        placed = False

        for row in rows:
            if abs(row["y"] - y_mid) <= y_tolerance:
                row["words"].append(word)
                row["y"] = (row["y"] + y_mid) / 2
                placed = True
                break

        if not placed:
            rows.append({"y": y_mid, "words": [word]})

    return rows


def assign_word_to_column(word, boundaries):
    x0, y0, x1, y1, text, *_ = word
    x_mid = (x0 + x1) / 2

    for i in range(len(boundaries) - 1):
        if boundaries[i] <= x_mid < boundaries[i + 1]:
            return i

    return None


def clean_numeric_string(value):
    """Convert numeric-looking strings to int/float while leaving normal text unchanged."""
    if value is None:
        return value

    if isinstance(value, (int, float)):
        return value

    text = str(value).strip()

    if text == "":
        return value

    # Keep obvious text, dates, phone numbers, ranges, and IDs as text.
    if re.search(r"[A-Za-z]", text):
        return value

    if re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", text):
        return value

    if re.search(r"\d+\s*-\s*\d+", text):
        return value

    cleaned = text
    negative = False

    # Accounting negative format: ($1,234.56)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()

    cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace("$", "")
    cleaned = cleaned.replace("€", "")
    cleaned = cleaned.replace("£", "")
    cleaned = cleaned.replace("¥", "")
    cleaned = cleaned.strip()

    is_percent = False
    if cleaned.endswith("%"):
        is_percent = True
        cleaned = cleaned[:-1].strip()

    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:].strip()

    if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", cleaned):
        return value

    try:
        number = float(cleaned)

        if negative:
            number = -number

        if is_percent:
            number = number / 100

        if number.is_integer() and not is_percent and "." not in cleaned:
            return int(number)

        return number

    except Exception:
        return value


def convert_numeric_cells(df):
    """Apply numeric conversion cell-by-cell without forcing entire columns to one type."""
    return df.applymap(clean_numeric_string)


# ============================================================
# OCR helpers
# ============================================================


def has_enough_selectable_words(words, minimum_words=12):
    """Return True when PyMuPDF extracted enough words to skip OCR."""
    return words is not None and len(words) >= minimum_words


def get_ocr_reader():
    """Load EasyOCR once per session."""
    if easyocr is None or np is None:
        return None

    if st.session_state.ocr_reader is None:
        with st.spinner("Loading OCR engine for the first time..."):
            st.session_state.ocr_reader = easyocr.Reader(["en"], gpu=False)

    return st.session_state.ocr_reader


def easyocr_results_to_words(ocr_results, zoom):
    """Convert EasyOCR output into PyMuPDF-like word tuples.

    PyMuPDF words look like:
    (x0, y0, x1, y1, text, block_no, line_no, word_no)

    EasyOCR boxes are in rendered-image pixel coordinates, so divide by zoom
    to return PDF coordinate values compatible with the existing XLSX logic.
    """
    words = []

    for idx, result in enumerate(ocr_results):
        try:
            box, text_value, confidence = result
        except Exception:
            continue

        text_value = str(text_value).strip()

        if not text_value:
            continue

        xs = [point[0] for point in box]
        ys = [point[1] for point in box]

        x0 = min(xs) / zoom
        y0 = min(ys) / zoom
        x1 = max(xs) / zoom
        y1 = max(ys) / zoom

        words.append((x0, y0, x1, y1, text_value, 0, idx, idx))

    return words


def ocr_page_words(page, zoom):
    """Render a page to an image and OCR it into word-like tuples."""
    reader = get_ocr_reader()

    if reader is None:
        raise RuntimeError(
            "OCR support requires easyocr and numpy. Add easyocr and numpy to requirements.txt."
        )

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")

    try:
        ocr_results = reader.readtext(np.array(image), detail=1, paragraph=False)
    finally:
        image.close()
        del image
        gc.collect()

    return easyocr_results_to_words(ocr_results, zoom)


def get_words_with_ocr_fallback(page, pnum, zoom, status_area=None):
    """Try selectable text first, then OCR only when selectable text is weak.

    If OCR runs, compare the OCR word count against the selectable-text word count
    and use whichever result appears more complete.
    """
    page_start = time.perf_counter()

    text_start = time.perf_counter()
    selectable_words = page.get_text("words")
    selectable_seconds = time.perf_counter() - text_start
    selectable_count = len(selectable_words or [])

    if has_enough_selectable_words(selectable_words):
        total_seconds = time.perf_counter() - page_start

        if status_area is not None:
            status_area.success(
                f"Page {pnum + 1}: selectable text found. "
                f"{selectable_count} words, {selectable_seconds:.2f}s. "
                f"OCR skipped. Total {total_seconds:.2f}s."
            )

        return selectable_words, "selectable", {
            "selectable_words": selectable_count,
            "ocr_words": 0,
            "selectable_seconds": selectable_seconds,
            "ocr_seconds": 0,
            "total_seconds": total_seconds,
        }

    if status_area is not None:
        status_area.warning(
            f"Page {pnum + 1}: only {selectable_count} selectable words found "
            f"({selectable_seconds:.2f}s). Running OCR fallback..."
        )

    ocr_start = time.perf_counter()
    ocr_words = ocr_page_words(page, zoom)
    ocr_seconds = time.perf_counter() - ocr_start
    ocr_count = len(ocr_words or [])

    # Use OCR only when it improves the result. This avoids replacing a small
    # but valid selectable-text result with weaker OCR output.
    if ocr_count > selectable_count:
        chosen_words = ocr_words
        chosen_method = "ocr"
    else:
        chosen_words = selectable_words
        chosen_method = "selectable"

    total_seconds = time.perf_counter() - page_start

    if status_area is not None:
        if chosen_method == "ocr":
            status_area.success(
                f"Page {pnum + 1}: OCR used. "
                f"Selectable: {selectable_count} words in {selectable_seconds:.2f}s. "
                f"OCR: {ocr_count} text items in {ocr_seconds:.2f}s. "
                f"Total {total_seconds:.2f}s."
            )
        else:
            status_area.info(
                f"Page {pnum + 1}: OCR did not improve extraction, so selectable text was kept. "
                f"Selectable: {selectable_count} words. OCR: {ocr_count} text items. "
                f"Total {total_seconds:.2f}s."
            )

    return chosen_words, chosen_method, {
        "selectable_words": selectable_count,
        "ocr_words": ocr_count,
        "selectable_seconds": selectable_seconds,
        "ocr_seconds": ocr_seconds,
        "total_seconds": total_seconds,
    }


# ============================================================
# Plain text extraction helper
# ============================================================


def extract_plain_text_from_pdf(pdf_bytes):
    text_doc = None

    try:
        text_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts = []

        for pnum in range(len(text_doc)):
            page = text_doc[pnum]
            page_text = page.get_text("text").strip()

            parts.append(f"===== Page {pnum + 1} =====")
            parts.append(page_text)
            parts.append("")

        return "\n".join(parts).encode("utf-8")

    finally:
        if text_doc is not None:
            text_doc.close()
        gc.collect()


# ============================================================
# Main app layout
# ============================================================

left_col, right_col = st.columns([4, 1])

with right_col:
    st.subheader("Page Tools")

    if st.button("Clear Current Page"):
        st.session_state.redactions[page_num] = []
        st.session_state.columns[page_num] = []
        st.session_state.annotations[page_num] = []
        st.session_state.last_ruler_click.pop(page_num, None)
        st.session_state.last_redaction_click.pop(page_num, None)
        st.session_state.last_annotation_click.pop(page_num, None)
        st.session_state.redaction_first_corner.pop(page_num, None)
        st.session_state.annotation_first_point.pop(page_num, None)
        gc.collect()
        st.rerun()

    if task == "Convert PDF to XLSX":
        st.info("Click the ruler to add/remove column boundaries.")
        st.write("Saved column points:")
        st.write(st.session_state.columns.get(page_num, []))

    elif task == "Redact PDF":
        st.info("Click two opposite corners to create each redaction rectangle.")
        st.write("Saved redaction rectangles:")
        st.write(len(st.session_state.redactions.get(page_num, [])))

        if st.session_state.redaction_first_corner.get(page_num):
            st.warning("First corner selected. Click the opposite corner.")

        if st.button("Undo Last Redaction"):
            if st.session_state.redactions.get(page_num):
                st.session_state.redactions[page_num].pop()
                gc.collect()
                st.rerun()

        if st.button("Cancel Current Rectangle"):
            st.session_state.redaction_first_corner.pop(page_num, None)
            gc.collect()
            st.rerun()

    elif task == "Annotate PDF":
        st.info("Use the menu bar above the PDF. Select mode lets you move annotations.")
        st.write("Saved annotations:")
        st.write(len(st.session_state.annotations.get(page_num, [])))

        if st.button("Undo Last Annotation"):
            if st.session_state.annotations.get(page_num):
                st.session_state.annotations[page_num].pop()
                st.session_state.selected_annotation_id.pop(page_num, None)
                gc.collect()
                st.rerun()

        if st.button("Delete Selected Annotation"):
            selected_id = st.session_state.selected_annotation_id.get(page_num)
            if selected_id:
                st.session_state.annotations[page_num] = [
                    a for a in st.session_state.annotations.get(page_num, [])
                    if a.get("id") != selected_id
                ]
                st.session_state.selected_annotation_id.pop(page_num, None)
                gc.collect()
                st.rerun()

        if st.button("Clear Annotations on Page"):
            st.session_state.annotations[page_num] = []
            st.session_state.selected_annotation_id.pop(page_num, None)
            gc.collect()
            st.rerun()

    else:
        st.info("Extract all selectable text from the PDF and download it as a .txt file.")


with left_col:
    if task == "Convert PDF to XLSX":
        st.subheader("Column Markup")

        current_columns = st.session_state.columns.get(page_num, [])

        current_columns = st.session_state.columns.get(page_num, [])

        preview = draw_column_overlay(
            page_image,
            current_columns,
        )

        click_result = show_scrollable_clickable_xlsx_markup(
            page_num=page_num,
            preview_image=preview,
            columns=current_columns,
            caption=(
                f"Page {page_num + 1}: click the ruler inside the PDF scroll window to add/remove multiple column boundaries. The ruler stays visible at the top and scrolls left/right with the PDF."
            ),
        )

        if click_result is not None:
            try:
                clicked_page = int(click_result.get("page_num"))
                clicked_x = int(float(click_result.get("x")))
                click_nonce = str(click_result.get("nonce", ""))

                if (
                    clicked_page == page_num
                    and st.session_state.last_ruler_click.get(page_num) != click_nonce
                ):
                    latest_columns = st.session_state.columns.get(page_num, [])
                    st.session_state.columns[page_num] = toggle_column(
                        latest_columns,
                        clicked_x,
                    )
                    st.session_state.last_ruler_click[page_num] = click_nonce
                    st.rerun()
            except Exception as e:
                st.warning(f"Column click could not be processed: {e}")

        if st.button("Generate XLSX"):
            source_doc = None
            output_xlsx = BytesIO()
            status_header = st.empty()
            status_area = st.container()
            progress_bar = st.progress(0)
            conversion_start = time.perf_counter()

            try:
                source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                total_pages = len(source_doc)

                status_header.info("Starting XLSX conversion...")

                with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
                    wrote_sheet = False
                    pages_with_columns = 0
                    pages_with_rows = 0
                    pages_using_ocr = 0
                    total_selectable_words = 0
                    total_ocr_words = 0

                    for pnum in range(total_pages):
                        progress_bar.progress((pnum + 1) / total_pages)

                        p = source_doc[pnum]
                        raw_columns = st.session_state.columns.get(pnum, [])

                        with status_area:
                            st.markdown(f"**Page {pnum + 1}**")

                            if not raw_columns:
                                st.write("Skipped: no column markers are set.")
                                continue

                        pages_with_columns += 1

                        pdf_columns = sorted([x / zoom for x in raw_columns])
                        boundaries = [0] + pdf_columns + [p.rect.width]

                        try:
                            with status_area:
                                page_status = st.empty()

                            words, extraction_method, extraction_stats = get_words_with_ocr_fallback(
                                p,
                                pnum,
                                zoom,
                                status_area=page_status,
                            )
                        except Exception as e:
                            with status_area:
                                st.error(f"Page {pnum + 1}: OCR/text extraction failed: {e}")
                            words = []
                            extraction_method = "failed"
                            extraction_stats = {
                                "selectable_words": 0,
                                "ocr_words": 0,
                                "total_seconds": 0,
                            }

                        total_selectable_words += extraction_stats.get("selectable_words", 0)
                        total_ocr_words += extraction_stats.get("ocr_words", 0)

                        if extraction_method == "ocr":
                            pages_using_ocr += 1

                        rows = group_words_into_rows(words)
                        table_rows = []

                        for row in rows:
                            cells = [""] * (len(boundaries) - 1)

                            for word in sorted(row["words"], key=lambda w: w[0]):
                                col_index = assign_word_to_column(word, boundaries)

                                if col_index is not None:
                                    cells[col_index] = (
                                        cells[col_index] + " " + word[4]
                                    ).strip()

                            if any(cell.strip() for cell in cells):
                                table_rows.append(cells)

                        with status_area:
                            if table_rows:
                                pages_with_rows += 1
                                df = pd.DataFrame(table_rows)
                                df = convert_numeric_cells(df)

                                df.to_excel(
                                    writer,
                                    sheet_name=f"Page_{pnum + 1}",
                                    index=False,
                                    header=False,
                                )

                                wrote_sheet = True
                                st.success(
                                    f"Wrote {len(table_rows)} row(s) using {extraction_method} extraction."
                                )
                            else:
                                st.warning(
                                    "No rows were created after applying the column markers."
                                )

                    if not wrote_sheet:
                        pd.DataFrame(
                            [["No columns marked or no extractable text/OCR text found."]]
                        ).to_excel(
                            writer,
                            sheet_name="Result",
                            index=False,
                            header=False,
                        )

                output_xlsx.seek(0)
                output_bytes = output_xlsx.getvalue()

                progress_bar.progress(1.0)
                total_seconds = time.perf_counter() - conversion_start

                status_header.success(
                    "XLSX conversion complete. "
                    f"Pages with column markers: {pages_with_columns}. "
                    f"Pages written: {pages_with_rows}. "
                    f"Pages using OCR fallback: {pages_using_ocr}. "
                    f"Selectable words found: {total_selectable_words}. "
                    f"OCR text items found: {total_ocr_words}. "
                    f"Total time: {total_seconds:.2f}s."
                )

            finally:
                if source_doc is not None:
                    source_doc.close()
                output_xlsx.close()
                gc.collect()

            st.download_button(
                label="Download XLSX",
                data=output_bytes,
                file_name="extracted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            st.caption("After downloading, use 'Clear uploaded PDF and reset app' in the sidebar.")

    elif task == "Redact PDF":
        st.subheader("Redaction Markup")

        preview = draw_redaction_overlay(
            page_image,
            st.session_state.redactions.get(page_num, []),
            st.session_state.redaction_first_corner.get(page_num),
        )

        click = streamlit_image_coordinates(
            preview,
            width=page_image.width,
            key=f"redaction_page_{page_num}",
        )

        if click is not None:
            clicked_x = int(click["x"])
            clicked_y = int(click["y"])
            click_signature = f"{clicked_x}_{clicked_y}"

            previous_click = st.session_state.last_redaction_click.get(page_num)

            if previous_click != click_signature:
                st.session_state.last_redaction_click[page_num] = click_signature
                add_redaction_click(page_num, clicked_x, clicked_y)
                st.rerun()

        st.caption("Click two opposite corners for each redaction box. Saved boxes stay visible.")

        if st.button("Generate Redacted PDF"):
            redacted_doc = None
            output_pdf = BytesIO()

            try:
                redacted_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

                for pnum, rects in st.session_state.redactions.items():
                    p = redacted_doc[pnum]

                    for r in rects:
                        x0 = r["x"] / zoom
                        y0 = r["y"] / zoom
                        x1 = (r["x"] + r["w"]) / zoom
                        y1 = (r["y"] + r["h"]) / zoom

                        rect = fitz.Rect(x0, y0, x1, y1)
                        p.add_redact_annot(rect, fill=(0, 0, 0))

                    p.apply_redactions()

                redacted_doc.save(output_pdf)
                output_pdf.seek(0)
                output_bytes = output_pdf.getvalue()

            finally:
                if redacted_doc is not None:
                    redacted_doc.close()
                output_pdf.close()
                gc.collect()

            st.download_button(
                label="Download Redacted PDF",
                data=output_bytes,
                file_name="redacted.pdf",
                mime="application/pdf",
            )

            st.caption("After downloading, use 'Clear uploaded PDF and reset app' in the sidebar.")

    elif task == "Annotate PDF":
        st.subheader("Annotation Markup")

        st.markdown("**Annotation Menu Bar**")
        menu_col1, menu_col2, menu_col3, menu_col4 = st.columns([1.5, 1, 1, 1])

        with menu_col1:
            annotation_tool = st.radio(
                "Tool",
                ["select", "textbox", "arrow", "line", "rectangle", "circle"],
                format_func=lambda x: {
                    "select": "Select / Move",
                    "textbox": "Text",
                    "arrow": "Arrow",
                    "line": "Line",
                    "rectangle": "Rectangle",
                    "circle": "Circle",
                }[x],
                horizontal=True,
                key="annotation_tool",
            )

        with menu_col2:
            annotation_color_hex = st.color_picker("Colour", "#ff0000", key="annotation_color_hex")

        with menu_col3:
            annotation_line_width = st.slider(
                "Line thickness",
                min_value=1,
                max_value=12,
                value=3,
                key="annotation_line_width",
            )

        with menu_col4:
            st.caption("Text boxes have no outline or fill. Use Select / Move to reposition objects.")

        annotation_text = ""
        annotation_font_family = "Sans Serif"
        annotation_font_size = 18
        annotation_bold = False
        annotation_italic = False
        annotation_underline = False

        if annotation_tool == "textbox":
            text_col1, text_col2, text_col3, text_col4 = st.columns([1, 1, 1, 1])
            with text_col1:
                annotation_font_family = st.selectbox("Font", ["Arial", "Sans Serif"], key="annotation_font_family")
            with text_col2:
                annotation_font_size = st.slider("Font size", 8, 72, 18, key="annotation_font_size")
            with text_col3:
                annotation_bold = st.checkbox("Bold", key="annotation_bold")
                annotation_italic = st.checkbox("Italic", key="annotation_italic")
            with text_col4:
                annotation_underline = st.checkbox("Underline", key="annotation_underline")
            st.caption("Text is typed directly on the PDF. Click the page, type, then click outside the text to save it.")

        settings = {
            "tool": annotation_tool,
            "color": "Custom",
            "color_hex": annotation_color_hex,
            "line_width": annotation_line_width,
            "text": annotation_text,
            "font_family": annotation_font_family,
            "font_size": annotation_font_size,
            "bold": annotation_bold,
            "italic": annotation_italic,
            "underline": annotation_underline,
        }
        st.session_state.current_annotation_settings = settings

        current_annotations = [
            normalize_annotation_for_component(a)
            for a in st.session_state.annotations.get(page_num, [])
        ]

        event = show_draggable_annotation_page(
            page_num=page_num,
            base_image=page_image,
            annotations=current_annotations,
            settings=settings,
            selected_id=st.session_state.selected_annotation_id.get(page_num),
            caption=(
                f"Page {page_num + 1}: draw by dragging. Switch to Select / Move to drag existing annotations. "
                "Text boxes render as text only; the blue outline appears only while selected in the editor."
            ),
        )

        new_page_num = st.slider(
            "Page",
            min_value=1,
            max_value=page_count,
            value=page_num + 1,
            key="annotation_page_slider_under_pdf",
        ) - 1

        if new_page_num != page_num:
            st.session_state.active_page_num = new_page_num
            st.session_state.selected_annotation_id.pop(page_num, None)
            st.rerun()

        if event is not None:
            try:
                clicked_page = int(event.get("page_num"))
                event_nonce = str(event.get("nonce", ""))
                if (
                    clicked_page == page_num
                    and st.session_state.last_annotation_click.get(page_num) != event_nonce
                ):
                    st.session_state.annotations[page_num] = [
                        normalize_annotation_for_component(a)
                        for a in event.get("annotations", [])
                    ]
                    selected_id = event.get("selected_id")
                    if selected_id:
                        st.session_state.selected_annotation_id[page_num] = selected_id
                    else:
                        st.session_state.selected_annotation_id.pop(page_num, None)
                    st.session_state.last_annotation_click[page_num] = event_nonce
                    # A Streamlit component value causes a rerun before this Python block
                    # updates session_state. During that rerun, the iframe is rendered with
                    # the previous annotation list, which made new lines/arrows and resized
                    # text appear to vanish. After saving the canonical Fabric snapshot,
                    # rerun once more so the iframe is rebuilt from the updated state.
                    st.rerun()
            except Exception as e:
                st.warning(f"Annotation update could not be processed: {e}")

        if st.button("Generate Annotated PDF"):
            output_bytes = apply_annotations_to_pdf(
                pdf_bytes,
                st.session_state.annotations,
                zoom,
            )

            st.download_button(
                label="Download Annotated PDF",
                data=output_bytes,
                file_name="annotated.pdf",
                mime="application/pdf",
            )

            st.caption("After downloading, use 'Clear uploaded PDF and reset app' in the sidebar.")

    else:
        st.subheader("Convert Whole PDF to Plain Text")

        st.write(
            "This extracts selectable text from every page and saves it as a plain `.txt` file. "
            "For scanned/image-only PDFs, OCR fallback is currently used in the XLSX conversion tool."
        )

        st.image(
            page_image,
            caption=f"Page {page_num + 1} preview",
            use_column_width=False,
        )

        if st.button("Generate Plain Text File"):
            output_text_bytes = extract_plain_text_from_pdf(pdf_bytes)

            st.download_button(
                label="Download Plain Text File",
                data=output_text_bytes,
                file_name="extracted_text.txt",
                mime="text/plain",
            )

            st.caption("After downloading, use 'Clear uploaded PDF and reset app' in the sidebar.")

# Release render-only objects at the end of each run.
del page_image
try:
    del pdf_bytes
except NameError:
    pass
gc.collect()
