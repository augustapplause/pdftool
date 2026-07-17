# ============================================================
# PDF Toolkit
# Version 2.3
#
# Changelog
# v2.3
# - XLSX: Added Rotate Left / Rotate Right button controls.
# - XLSX: Rotated conversion uses the rotated page image for OCR/extraction.
# v2.2
# - XLSX: Added shared column layout mode with source page selection.
# v2.1
# - Annotation: Added freehand pen tool.
# v2.0
# - Refactor-safe task routing and redaction render fix.
# - Redaction preview uses a local base64 HTML component.
# ============================================================

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
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
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

if "annotation_overlays" not in st.session_state:
    st.session_state.annotation_overlays = {}

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


def rotate_xlsx_preview_image(image, rotation_degrees):
    """Rotate the displayed XLSX markup image clockwise for sideways PDFs."""
    rotation_degrees = int(rotation_degrees or 0) % 360

    if rotation_degrees == 90:
        return image.rotate(-90, expand=True)
    if rotation_degrees == 180:
        return image.rotate(180, expand=True)
    if rotation_degrees == 270:
        return image.rotate(90, expand=True)

    return image.copy()


def rotated_page_width_points(page, rotation_degrees):
    """Return the PDF-space page width after the XLSX view rotation."""
    rotation_degrees = int(rotation_degrees or 0) % 360

    if rotation_degrees in (90, 270):
        return page.rect.height

    return page.rect.width


def transform_word_bbox_for_xlsx_rotation(word, page_width, page_height, rotation_degrees):
    """Transform a PyMuPDF word tuple into the rotated XLSX view coordinate space.

    Column markers are clicked on the rotated preview image. Text extraction still
    returns original PDF coordinates, so each word bbox is transformed to match
    the visual orientation before row grouping and column assignment.
    """
    rotation_degrees = int(rotation_degrees or 0) % 360

    if rotation_degrees == 0:
        return word

    x0, y0, x1, y1, text, *rest = word
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def rotate_point(x, y):
        if rotation_degrees == 90:
            return page_height - y, x
        if rotation_degrees == 180:
            return page_width - x, page_height - y
        if rotation_degrees == 270:
            return y, page_width - x
        return x, y

    rotated = [rotate_point(x, y) for x, y in corners]
    xs = [pt[0] for pt in rotated]
    ys = [pt[1] for pt in rotated]

    return (min(xs), min(ys), max(xs), max(ys), text, *rest)


def transform_words_for_xlsx_rotation(words, page, rotation_degrees):
    """Rotate extracted/OCR words so XLSX extraction matches the preview orientation."""
    rotation_degrees = int(rotation_degrees or 0) % 360

    if rotation_degrees == 0:
        return words

    return [
        transform_word_bbox_for_xlsx_rotation(
            word,
            page.rect.width,
            page.rect.height,
            rotation_degrees,
        )
        for word in (words or [])
    ]


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
    """Create/load a local Streamlit component backed by Fabric.js.

    v1.0 architecture:
    - Fabric owns all editing state in the browser while the user edits.
    - Tool switching, moving, resizing, rotating, and typing do not call Streamlit.
    - Streamlit receives a full annotation snapshot only when the user clicks
      Save Page Annotations inside the component.
    """
    component_dir = os.path.join(tempfile.gettempdir(), "pdf_annotation_component_v21")
    os.makedirs(component_dir, exist_ok=True)
    index_path = os.path.join(component_dir, "index.html")

    component_html = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js"></script>
<style>
html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #ffffff; font-family: Arial, sans-serif; }
#editor { width: 100%; height: 780px; overflow: auto; border: 1px solid #cccccc; background: #f7f7f7; box-sizing: border-box; }
#toolbar { position: sticky; top: 0; left: 0; z-index: 20; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-height: 42px; padding: 6px 8px; border-bottom: 1px solid #cccccc; background: rgba(255,255,255,0.98); box-sizing: border-box; }
#toolbar button, #toolbar select, #toolbar input { font-size: 13px; }
#toolbar button { padding: 5px 8px; border: 1px solid #999999; border-radius: 4px; background: #ffffff; cursor: pointer; }
#toolbar button.active { background: #dbeafe; border-color: #2563eb; }
#toolbar button.primary { background: #2563eb; color: white; border-color: #1d4ed8; }
#toolbar button.danger { background: #fff1f2; border-color: #f43f5e; }
#hint { font-size: 12px; color: #333333; margin-left: 6px; }
#content { position: relative; background: #ffffff; }
canvas { display: block; }
.small-label { font-size: 12px; color: #333333; }
</style>
</head>
<body>
<div id="editor">
  <div id="toolbar">
    <button id="tool_select" data-tool="select">Select</button>
    <button id="tool_textbox" data-tool="textbox">Text</button>
    <button id="tool_pen" data-tool="pen">Pen</button>
    <button id="tool_arrow" data-tool="arrow">Arrow</button>
    <button id="tool_line" data-tool="line">Line</button>
    <button id="tool_rectangle" data-tool="rectangle">Rectangle</button>
    <button id="tool_circle" data-tool="circle">Circle</button>
    <span class="small-label">Colour</span><input id="color" type="color" value="#ff0000">
    <span class="small-label">Thickness</span><input id="line_width" type="range" min="1" max="12" value="3"><span id="line_width_value" class="small-label">3</span>
    <span class="small-label">Font</span><select id="font_family"><option>Sans Serif</option><option>Arial</option></select>
    <span class="small-label">Size</span><input id="font_size" type="number" min="8" max="96" value="18" style="width:54px">
    <label class="small-label"><input id="bold" type="checkbox"> Bold</label>
    <label class="small-label"><input id="italic" type="checkbox"> Italic</label>
    <label class="small-label"><input id="underline" type="checkbox"> Underline</label>
    <button id="delete_btn" class="danger">Delete</button>
    <button id="clear_btn" class="danger">Clear Page</button>
    <button id="save_btn" class="primary">Save Page Annotations</button>
    <span id="hint">Loading annotation editor...</span>
  </div>
  <div id="content"><canvas id="pdf-canvas"></canvas></div>
</div>
<script>
let canvas = null;
let args = {};
let currentTool = "select";
let isDrawing = false;
let startPoint = null;
let tempObject = null;
let loadedPageNum = null;
let loadedSignature = null;
let loadedWidth = null;
let loadedHeight = null;
let saveCounter = 0;
let toolbarReady = false;

function sendMessageToStreamlitClient(type, data) {
  window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*");
}
function setFrameHeight(height) { sendMessageToStreamlitClient("streamlit:setFrameHeight", { height: height }); }
function setComponentValue(value) { sendMessageToStreamlitClient("streamlit:setComponentValue", { value: value, dataType: "json" }); }
function nonce() { return Date.now() + "_" + Math.random().toString(36).slice(2); }
function makeId() { return "ann_" + nonce(); }
function byId(id) { return document.getElementById(id); }
function color() { return byId("color").value || "#ff0000"; }
function lineWidth() { return Math.max(1, Number(byId("line_width").value || 3)); }
function fontFamily() { return byId("font_family").value || "Sans Serif"; }
function fontSize() { return Math.max(8, Number(byId("font_size").value || 18)); }
function isBold() { return !!byId("bold").checked; }
function isItalic() { return !!byId("italic").checked; }
function isUnderline() { return !!byId("underline").checked; }

function updateHint(txt) { byId("hint").textContent = txt; }
function updateToolButtons() {
  ["select","textbox","pen","arrow","line","rectangle","circle"].forEach(t => {
    const el = byId("tool_" + t); if (el) el.classList.toggle("active", currentTool === t);
  });
  if (!canvas) return;
  const selectable = currentTool === "select";
  canvas.selection = selectable;
  canvas.getObjects().forEach(o => { if (o.kind) { o.selectable = selectable; o.evented = true; } });
  canvas.isDrawingMode = currentTool === "pen";
  if (canvas.isDrawingMode) {
    canvas.freeDrawingBrush = new fabric.PencilBrush(canvas);
    canvas.freeDrawingBrush.color = color();
    canvas.freeDrawingBrush.width = lineWidth();
  }
  canvas.defaultCursor = selectable ? "default" : "crosshair";
  canvas.hoverCursor = selectable ? "move" : "crosshair";
  if (currentTool === "select") updateHint("Select: move, resize, rotate, or delete objects. Click Save when finished.");
  else if (currentTool === "textbox") updateHint("Text: click the PDF and type directly. Click outside text when done.");
  else if (currentTool === "pen") updateHint("Pen: draw freehand directly on the PDF. Click Save when finished.");
  else updateHint("Draw: drag on the PDF. Edits stay in the browser until you click Save.");
  canvas.requestRenderAll();
}
function setupToolbar() {
  if (toolbarReady) return;
  toolbarReady = true;
  ["select","textbox","pen","arrow","line","rectangle","circle"].forEach(t => {
    byId("tool_" + t).addEventListener("click", function(e) { e.preventDefault(); currentTool = t; updateToolButtons(); });
  });
  byId("line_width").addEventListener("input", function() { byId("line_width_value").textContent = String(lineWidth()); if (canvas && canvas.freeDrawingBrush) canvas.freeDrawingBrush.width = lineWidth(); });
  byId("color").addEventListener("input", function() { if (canvas && canvas.freeDrawingBrush) canvas.freeDrawingBrush.color = color(); });
  byId("delete_btn").addEventListener("click", function(e) { e.preventDefault(); deleteSelection(false); });
  byId("clear_btn").addEventListener("click", function(e) { e.preventDefault(); if (!canvas) return; canvas.getObjects().slice().forEach(o => { if (o.kind) canvas.remove(o); }); canvas.discardActiveObject(); canvas.requestRenderAll(); updateHint("Page annotations cleared in editor. Click Save to commit."); });
  byId("save_btn").addEventListener("click", function(e) { e.preventDefault(); saveSnapshot("save_button"); });
}
function fabricPoint(opt) { const pointer = canvas.getPointer(opt.e); return { x: pointer.x, y: pointer.y }; }
function setCommon(obj, kind, id) {
  obj.set({ id: id || makeId(), kind: kind, transparentCorners: false, cornerSize: 9, borderColor: "#0066ff", cornerColor: "#ffffff", cornerStrokeColor: "#0066ff", objectCaching: false, originX: "left", originY: "top" });
  return obj;
}
function arrowRender(ctx) {
  const p = this.calcLinePoints();
  const dx = p.x2 - p.x1;
  const dy = p.y2 - p.y1;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const head = Math.max(12, Number(this.line_width || this.strokeWidth || 3) * 5);
  const shaftEndX = p.x2 - ux * Math.min(head * 0.78, len * 0.45);
  const shaftEndY = p.y2 - uy * Math.min(head * 0.78, len * 0.45);

  ctx.save();
  ctx.strokeStyle = this.stroke || "#ff0000";
  ctx.lineWidth = this.strokeWidth || Number(this.line_width || 3);
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(p.x1, p.y1);
  ctx.lineTo(shaftEndX, shaftEndY);
  ctx.stroke();

  const angle = Math.atan2(dy, dx);
  ctx.translate(p.x2, p.y2);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(-head, head / 2.4);
  ctx.lineTo(-head, -head / 2.4);
  ctx.closePath();
  ctx.fillStyle = this.stroke || "#ff0000";
  ctx.fill();
  ctx.restore();
}
function createFabricObject(a) {
  const kind = a.kind || "textbox", id = a.id || makeId(), stroke = a.custom_color || a.color_hex || "#ff0000", lw = Math.max(1, Number(a.line_width || 3));
  const x0 = Number(a.x0 || 0), y0 = Number(a.y0 || 0), x1 = Number(a.x1 || x0), y1 = Number(a.y1 || y0), angle = Number(a.angle || 0);
  if (kind === "textbox") {
    const txt = new fabric.IText(a.text || "", { left: x0, top: y0, angle: angle, fill: stroke, fontFamily: a.font_family === "Arial" ? "Arial" : "sans-serif", fontSize: Number(a.font_size || 18), fontWeight: a.bold ? "bold" : "normal", fontStyle: a.italic ? "italic" : "normal", underline: !!a.underline, backgroundColor: "", stroke: null, strokeWidth: 0, padding: 0, objectCaching: false, originX: "left", originY: "top" });
    txt.set({ font_family: a.font_family || "Sans Serif", custom_color: stroke, line_width: 1 });
    return setCommon(txt, "textbox", id);
  }
  if (kind === "line" || kind === "arrow") {
    const line = new fabric.Line([x0, y0, x1, y1], { stroke: stroke, strokeWidth: lw, fill: stroke, objectCaching: false, strokeUniform: true });
    line.set({ custom_color: stroke, line_width: lw });
    if (kind === "arrow") line._render = arrowRender;
    return setCommon(line, kind, id);
  }
  if (kind === "rectangle") {
    const rect = new fabric.Rect({ left: x0, top: y0, angle: angle, width: Math.max(1, Math.abs(x1 - x0)), height: Math.max(1, Math.abs(y1 - y0)), fill: "rgba(0,0,0,0)", stroke: stroke, strokeWidth: lw, strokeUniform: true, objectCaching: false, originX: "left", originY: "top" });
    rect.set({ custom_color: stroke, line_width: lw });
    return setCommon(rect, kind, id);
  }
  if (kind === "circle") {
    const ell = new fabric.Ellipse({ left: x0, top: y0, angle: angle, rx: Math.max(1, Math.abs(x1 - x0) / 2), ry: Math.max(1, Math.abs(y1 - y0) / 2), fill: "rgba(0,0,0,0)", stroke: stroke, strokeWidth: lw, strokeUniform: true, objectCaching: false, originX: "left", originY: "top" });
    ell.set({ custom_color: stroke, line_width: lw });
    return setCommon(ell, kind, id);
  }
  if (kind === "pen" || kind === "freehand") {
    if (!a.path) return null;
    const pathObj = new fabric.Path(a.path, {
      left: Number(a.left ?? x0 ?? 0),
      top: Number(a.top ?? y0 ?? 0),
      angle: angle,
      scaleX: Number(a.scaleX || 1),
      scaleY: Number(a.scaleY || 1),
      stroke: stroke,
      strokeWidth: lw,
      fill: null,
      strokeLineCap: "round",
      strokeLineJoin: "round",
      strokeUniform: true,
      objectCaching: false,
      originX: "left",
      originY: "top"
    });
    pathObj.set({ custom_color: stroke, line_width: lw });
    return setCommon(pathObj, "pen", id);
  }
  return null;
}
function lineEndpoints(obj) {
  const m = obj.calcTransformMatrix();
  let lp = null;
  if (typeof obj.calcLinePoints === "function") lp = obj.calcLinePoints();
  if (!lp) lp = { x1: obj.x1 || 0, y1: obj.y1 || 0, x2: obj.x2 || 0, y2: obj.y2 || 0 };
  const p1 = fabric.util.transformPoint(new fabric.Point(lp.x1, lp.y1), m);
  const p2 = fabric.util.transformPoint(new fabric.Point(lp.x2, lp.y2), m);
  return { x0: p1.x, y0: p1.y, x1: p2.x, y1: p2.y };
}
function bakeScale(obj) {
  const kind = obj.kind || "";
  const sx = Number(obj.scaleX || 1), sy = Number(obj.scaleY || 1);
  if (Math.abs(sx - 1) <= 0.0001 && Math.abs(sy - 1) <= 0.0001) return;
  const center = obj.getCenterPoint();
  if (kind === "textbox") {
    obj.set({ fontSize: Math.max(1, Math.round(Number(obj.fontSize || 18) * Math.max(sx, sy))), scaleX: 1, scaleY: 1 });
  } else if (kind === "rectangle") {
    obj.set({ width: Math.max(1, Number(obj.width || 1) * sx), height: Math.max(1, Number(obj.height || 1) * sy), scaleX: 1, scaleY: 1 });
  } else if (kind === "circle") {
    obj.set({ rx: Math.max(1, Number(obj.rx || 1) * sx), ry: Math.max(1, Number(obj.ry || 1) * sy), scaleX: 1, scaleY: 1 });
  }
  obj.setPositionByOrigin(center, "center", "center"); obj.setCoords();
}
function serializeObject(obj) {
  const kind = obj.kind || "textbox";
  if (kind === "textbox") {
    return { id: obj.id || makeId(), kind: "textbox", x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + Number(obj.width || 1), y1: Number(obj.top || 0) + Number(obj.height || 1), angle: Number(obj.angle || 0), text: obj.text || "", custom_color: obj.custom_color || obj.fill || "#ff0000", line_width: 1, font_family: obj.font_family || (obj.fontFamily === "Arial" ? "Arial" : "Sans Serif"), font_size: Number(obj.fontSize || 18), bold: obj.fontWeight === "bold", italic: obj.fontStyle === "italic", underline: obj.underline === true };
  }
  if (kind === "line" || kind === "arrow") {
    const p = lineEndpoints(obj);
    return { id: obj.id || makeId(), kind: kind, x0: Number(p.x0), y0: Number(p.y0), x1: Number(p.x1), y1: Number(p.y1), angle: 0, custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) };
  }
  if (kind === "rectangle") {
    return { id: obj.id || makeId(), kind: kind, x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + Number(obj.width || 1), y1: Number(obj.top || 0) + Number(obj.height || 1), angle: Number(obj.angle || 0), custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) };
  }
  if (kind === "circle") {
    return { id: obj.id || makeId(), kind: kind, x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + 2 * Number(obj.rx || 1), y1: Number(obj.top || 0) + 2 * Number(obj.ry || 1), angle: Number(obj.angle || 0), custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) };
  }
  if (kind === "pen" || kind === "freehand") {
    return { id: obj.id || makeId(), kind: "pen", path: obj.path || null, left: Number(obj.left || 0), top: Number(obj.top || 0), x0: Number(obj.left || 0), y0: Number(obj.top || 0), x1: Number(obj.left || 0) + Number(obj.width || 1) * Number(obj.scaleX || 1), y1: Number(obj.top || 0) + Number(obj.height || 1) * Number(obj.scaleY || 1), scaleX: Number(obj.scaleX || 1), scaleY: Number(obj.scaleY || 1), angle: Number(obj.angle || 0), custom_color: obj.custom_color || obj.stroke || "#ff0000", line_width: Number(obj.line_width || obj.strokeWidth || 3) };
  }
  return null;
}
function serializeAnnotations() {
  canvas.getObjects().forEach(o => { if (o.kind) bakeScale(o); });
  canvas.requestRenderAll();
  return canvas.getObjects().filter(o => !!o.kind).map(serializeObject).filter(Boolean);
}
function exportAnnotationOverlayB64() {
  if (!canvas) return null;

  const active = canvas.getActiveObject();
  const bgImage = canvas.backgroundImage;
  const bgColor = canvas.backgroundColor;

  // Hide selection handles and the PDF background so the saved image contains
  // only the annotations on a transparent background. This is restored immediately.
  canvas.discardActiveObject();
  canvas.backgroundImage = null;
  canvas.backgroundColor = null;
  canvas.requestRenderAll();

  let dataUrl = null;
  try {
    dataUrl = canvas.toDataURL({ format: "png", multiplier: 1, enableRetinaScaling: false });
  } catch (err) {
    dataUrl = null;
  }

  canvas.backgroundImage = bgImage;
  canvas.backgroundColor = bgColor;
  if (active) canvas.setActiveObject(active);
  canvas.requestRenderAll();

  if (!dataUrl || dataUrl.indexOf(",") < 0) return null;
  return dataUrl.split(",")[1];
}
function saveSnapshot(reason) {
  if (!canvas) return;
  saveCounter += 1;
  const active = canvas.getActiveObject();
  const annotations = serializeAnnotations();
  const overlayB64 = exportAnnotationOverlayB64();
  setComponentValue({ page_num: args.page_num, action: reason || "save", commit_id: saveCounter, annotations: annotations, overlay_png_b64: overlayB64, selected_id: active && active.id ? active.id : null, nonce: nonce() });
  updateHint("Saved " + annotations.length + " annotation" + (annotations.length === 1 ? "" : "s") + " for this page.");
}
function deleteSelection(shouldSave) {
  const active = canvas.getActiveObject();
  if (!active) return;
  if (active.type === "activeSelection") active.forEachObject(o => canvas.remove(o)); else canvas.remove(active);
  canvas.discardActiveObject(); canvas.requestRenderAll();
  updateHint("Deleted selected annotation. Click Save to commit.");
  if (shouldSave) saveSnapshot("delete");
}
function installCanvasEvents() {
  canvas.on("mouse:down", function(opt) {
    if (currentTool === "select" || currentTool === "pen") return;
    const p = fabricPoint(opt);
    if (currentTool === "textbox") {
      const obj = createFabricObject({ kind: "textbox", x0: p.x, y0: p.y, text: "", custom_color: color(), font_family: fontFamily(), font_size: fontSize(), bold: isBold(), italic: isItalic(), underline: isUnderline() });
      canvas.add(obj); canvas.setActiveObject(obj); obj.enterEditing(); if (obj.hiddenTextarea) obj.hiddenTextarea.focus(); canvas.requestRenderAll(); updateHint("Typing text. Click outside the text, then Save."); return;
    }
    isDrawing = true; startPoint = p;
    tempObject = createFabricObject({ kind: currentTool, x0: p.x, y0: p.y, x1: p.x, y1: p.y, custom_color: color(), line_width: lineWidth() });
    if (tempObject) { tempObject.selectable = false; tempObject.evented = false; canvas.add(tempObject); }
  });
  canvas.on("mouse:move", function(opt) {
    if (!isDrawing || !tempObject || !startPoint) return;
    const p = fabricPoint(opt), kind = tempObject.kind;
    if (kind === "line" || kind === "arrow") { tempObject.set({ x2: p.x, y2: p.y }); }
    else if (kind === "rectangle") { tempObject.set({ left: Math.min(startPoint.x, p.x), top: Math.min(startPoint.y, p.y), width: Math.max(1, Math.abs(p.x - startPoint.x)), height: Math.max(1, Math.abs(p.y - startPoint.y)) }); }
    else if (kind === "circle") { tempObject.set({ left: Math.min(startPoint.x, p.x), top: Math.min(startPoint.y, p.y), rx: Math.max(1, Math.abs(p.x - startPoint.x) / 2), ry: Math.max(1, Math.abs(p.y - startPoint.y) / 2) }); }
    tempObject.setCoords(); canvas.requestRenderAll();
  });
  canvas.on("mouse:up", function() {
    if (!isDrawing) return;
    isDrawing = false;
    if (tempObject) {
      tempObject.selectable = currentTool === "select";
      tempObject.evented = true;
      canvas.setActiveObject(tempObject); tempObject.setCoords();
      const br = tempObject.getBoundingRect(true, true);
      if (br.width < 4 && br.height < 4) canvas.remove(tempObject);
      canvas.requestRenderAll(); updateHint("Object added. Click Save Page Annotations to commit.");
    }
    tempObject = null; startPoint = null;
  });
  canvas.on("path:created", function(opt) {
    if (!opt || !opt.path) return;
    const pathObj = opt.path;
    setCommon(pathObj, "pen", makeId());
    pathObj.set({ custom_color: color(), line_width: lineWidth(), stroke: color(), strokeWidth: lineWidth(), fill: null, strokeLineCap: "round", strokeLineJoin: "round", strokeUniform: true, objectCaching: false });
    pathObj.selectable = currentTool === "select";
    pathObj.evented = true;
    pathObj.setCoords();
    canvas.requestRenderAll();
    updateHint("Freehand stroke added. Click Save Page Annotations to commit.");
  });
  canvas.on("object:modified", function(opt) { if (opt && opt.target) bakeScale(opt.target); canvas.requestRenderAll(); updateHint("Edit made. Click Save Page Annotations to commit."); });
  canvas.on("editing:exited", function(opt) { if (opt && opt.target && opt.target.kind === "textbox") { if (!(opt.target.text || "").trim()) canvas.remove(opt.target); canvas.requestRenderAll(); updateHint("Text edit complete. Click Save Page Annotations to commit."); } });
}
window.addEventListener("keydown", function(e) { if (!canvas) return; const active = canvas.getActiveObject(); if (active && active.isEditing) return; if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelection(false); } });
function render(argsIn) {
  setupToolbar();
  const newArgs = argsIn || {};
  const width = Number(newArgs.width || 800), height = Number(newArgs.preview_height || 1000);
  const signature = String(newArgs.annotations_signature || "");
  const pageNum = Number(newArgs.page_num || 0);
  const content = byId("content"); content.style.width = width + "px"; content.style.minHeight = (height + 48) + "px";
  if (!window.fabric) { updateHint("Fabric.js could not be loaded. Check internet/CDN access or bundle fabric.min.js locally."); setFrameHeight(800); return; }
  if (!canvas) { canvas = new fabric.Canvas("pdf-canvas", { preserveObjectStacking: true, selection: true }); installCanvasEvents(); }
  const needsReload = loadedPageNum !== pageNum || loadedSignature !== signature || loadedWidth !== width || loadedHeight !== height;
  args = newArgs;
  if (!needsReload) { updateToolButtons(); setFrameHeight(800); return; }
  loadedPageNum = pageNum; loadedSignature = signature; loadedWidth = width; loadedHeight = height;
  canvas.setWidth(width); canvas.setHeight(height); canvas.clear();
  fabric.Image.fromURL("data:image/png;base64," + args.preview_b64, function(img) {
    img.set({ left: 0, top: 0, selectable: false, evented: false, originX: "left", originY: "top" });
    img.scaleX = 1; img.scaleY = 1;
    canvas.setBackgroundImage(img, function() {
      (args.annotations || []).forEach(a => { const obj = createFabricObject(a); if (obj) canvas.add(obj); });
      updateToolButtons(); canvas.requestRenderAll(); setFrameHeight(800);
    }, { originX: "left", originY: "top" });
  }, { crossOrigin: "anonymous" });
}
window.addEventListener("message", function(event) { if (event.data && event.data.type === "streamlit:render") render(event.data.args || {}); });
sendMessageToStreamlitClient("streamlit:componentReady", { apiVersion: 1 }); setFrameHeight(800);
</script>
</body>
</html>
"""

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(component_html)

    return components.declare_component("pdf_annotation_component_v21", path=component_dir)

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
        key=f"pdf_annotation_component_v21_page_{page_num}",
        default=None,
    )

    if caption:
        st.caption(caption)

    return result


def apply_annotation_component_event(page_num, event):
    """Apply the newest Fabric snapshot before rendering the component.

    Streamlit custom components return their value at the start of a rerun.
    Applying it before the component is called prevents the iframe from being
    rehydrated with the previous annotation list, which caused the visible
    off-by-one rollback after move/resize/rotate edits.
    """
    if not isinstance(event, dict):
        return False

    try:
        clicked_page = int(event.get("page_num"))
    except Exception:
        return False

    if clicked_page != page_num:
        return False

    try:
        commit_id = int(event.get("commit_id", 0))
    except Exception:
        commit_id = 0

    event_nonce = str(event.get("nonce", ""))
    last_commit_key = f"last_annotation_commit_{page_num}"
    last_nonce_key = f"last_annotation_nonce_{page_num}"

    last_commit = int(st.session_state.get(last_commit_key, 0) or 0)
    last_nonce = str(st.session_state.get(last_nonce_key, ""))

    # Prefer monotonic commits; fall back to nonce for old component values.
    if commit_id and commit_id <= last_commit:
        return False
    if not commit_id and event_nonce and event_nonce == last_nonce:
        return False

    st.session_state.annotations[page_num] = [
        normalize_annotation_for_component(a)
        for a in event.get("annotations", [])
    ]

    overlay_png_b64 = event.get("overlay_png_b64")
    if overlay_png_b64:
        st.session_state.annotation_overlays[page_num] = overlay_png_b64
    elif not st.session_state.annotations.get(page_num):
        st.session_state.annotation_overlays.pop(page_num, None)

    selected_id = event.get("selected_id")
    if selected_id:
        st.session_state.selected_annotation_id[page_num] = selected_id
    else:
        st.session_state.selected_annotation_id.pop(page_num, None)

    if commit_id:
        st.session_state[last_commit_key] = commit_id
    if event_nonce:
        st.session_state[last_nonce_key] = event_nonce

    return True

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
    """Draw a continuous arrow in the exported PDF.

    The browser/Fabric arrow is rendered as a shaft with a filled triangular
    head. Earlier PDF export versions shortened the shaft and then drew only a
    V-shaped head, which could leave a visible white gap between the shaft and
    arrowhead. This version draws the shaft to the arrow tip first, then paints
    a filled arrowhead over the end of the shaft. That keeps the arrow visually
    continuous without the shaft protruding past the tip.
    """
    import math

    dx = p1.x - p0.x
    dy = p1.y - p0.y
    line_len = math.hypot(dx, dy)

    if line_len < 0.01:
        return

    angle = math.atan2(dy, dx)
    head_len = min(max(10, width * 5), line_len * 0.45)
    head_angle = math.radians(28)

    # Draw the full shaft first. The filled head below covers the shaft end,
    # so the exported PDF has no gap and no visible overshoot at the tip.
    page.draw_line(p0, p1, color=color, width=width)

    left = fitz.Point(
        p1.x - head_len * math.cos(angle - head_angle),
        p1.y - head_len * math.sin(angle - head_angle),
    )
    right = fitz.Point(
        p1.x - head_len * math.cos(angle + head_angle),
        p1.y - head_len * math.sin(angle + head_angle),
    )

    try:
        page.draw_polygon([p1, left, right], color=color, fill=color, width=0)
    except Exception:
        # Compatibility fallback for older PyMuPDF builds.
        page.draw_polyline([left, p1, right], color=color, width=max(width, 1))


def apply_annotations_to_pdf(pdf_bytes, annotations_by_page, zoom, annotation_overlays_by_page=None):
    annotated_doc = None
    output_pdf = BytesIO()
    annotation_overlays_by_page = annotation_overlays_by_page or {}

    try:
        annotated_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        for pnum, annotations in annotations_by_page.items():
            if pnum >= len(annotated_doc):
                continue

            page = annotated_doc[pnum]

            # v1.7 preferred export path: use the transparent annotation layer saved
            # by the browser editor. This preserves the exact visual placement, font
            # metrics, arrowheads, and anti-aliasing seen in the annotation screen.
            overlay_png_b64 = annotation_overlays_by_page.get(pnum)
            if overlay_png_b64:
                try:
                    overlay_bytes = base64.b64decode(overlay_png_b64)
                    page.insert_image(page.rect, stream=overlay_bytes, overlay=True)
                    continue
                except Exception:
                    # Fall back to vector reconstruction below if the overlay cannot be decoded.
                    pass

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
                    text_value = str(ann.get("text", ""))
                    fontsize = float(ann.get("font_size", 12)) / zoom
                    fontname = get_pdf_font_name(
                        ann.get("font_family", "Sans Serif"),
                        ann.get("bold", False),
                        ann.get("italic", False),
                    )

                    # The browser editor stores text using a visual top-left anchor,
                    # while PyMuPDF insert_text() expects a baseline point.
                    # A lower baseline factor moves exported annotations up slightly
                    # and better matches the on-screen browser preview against nearby
                    # native PDF text.
                    baseline_factor = 0.67
                    line_height = fontsize * 1.16
                    text_lines = text_value.splitlines() or [text_value]
                    angle = float(ann.get("angle", 0) or 0)
                    can_rotate_natively = abs(angle) % 360 in (0, 90, 180, 270)
                    native_rotate = int(abs(angle) % 360) if can_rotate_natively else 0

                    for line_index, line in enumerate(text_lines):
                        baseline_y = y0 + (fontsize * baseline_factor) + (line_index * line_height)
                        if baseline_y > page.rect.height:
                            continue
                        page.insert_text(
                            fitz.Point(x0, baseline_y),
                            line,
                            fontsize=fontsize,
                            fontname=fontname,
                            color=color,
                            rotate=native_rotate,
                        )

                        if ann.get("underline"):
                            underline_y = baseline_y + max(1.0, fontsize * 0.08)
                            text_width = fitz.get_text_length(line, fontname=fontname, fontsize=fontsize)
                            if underline_y < page.rect.height:
                                page.draw_line(
                                    fitz.Point(x0, underline_y),
                                    fitz.Point(min(page.rect.width, x0 + text_width), underline_y),
                                    color=color,
                                    width=max(0.4, width / 2),
                                )

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




def get_redaction_click_component():
    """Create/load a local Streamlit component for reliable redaction clicks."""
    component_dir = os.path.join(tempfile.gettempdir(), "pdf_redaction_click_component_v20")
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
        height: 735px;
        overflow: auto;
        border: 1px solid #cccccc;
        background: #ffffff;
        box-sizing: border-box;
    }
    #page-wrap {
        position: relative;
        display: inline-block;
        background: #ffffff;
    }
    #page-img {
        display: block;
        max-width: none;
        cursor: crosshair;
        user-select: none;
    }
</style>
</head>
<body>
<div id="scroll-window"><div id="page-wrap"><img id="page-img" /></div></div>
<script>
    function sendMessageToStreamlitClient(type, data) {
        window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*");
    }
    function setFrameHeight(height) {
        sendMessageToStreamlitClient("streamlit:setFrameHeight", { height: height });
    }
    function setComponentValue(value) {
        sendMessageToStreamlitClient("streamlit:setComponentValue", { value: value, dataType: "json" });
    }
    function nonce() {
        return Date.now() + "_" + Math.random().toString(36).slice(2);
    }
    function render(args) {
        const img = document.getElementById("page-img");
        img.src = "data:image/png;base64," + args.preview_b64;
        img.width = Number(args.width || 0);
        img.height = Number(args.height || 0);
        img.onclick = function(event) {
            const rect = img.getBoundingClientRect();
            const x = Math.round(event.clientX - rect.left);
            const y = Math.round(event.clientY - rect.top);
            setComponentValue({ page_num: Number(args.page_num || 0), x: x, y: y, nonce: nonce() });
        };
        setFrameHeight(750);
    }
    window.addEventListener("message", function(event) {
        if (event.data && event.data.type === "streamlit:render") {
            render(event.data.args || {});
        }
    });
    sendMessageToStreamlitClient("streamlit:componentReady", { apiVersion: 1 });
    setFrameHeight(750);
</script>
</body>
</html>
"""

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(component_html)

    return components.declare_component("pdf_redaction_click_component_v20", path=component_dir)


def show_scrollable_clickable_redaction_markup(page_num, preview_image, caption=None):
    """Render a reliable scrollable clickable redaction page preview."""
    preview_b64 = image_to_base64_png(preview_image)
    component = get_redaction_click_component()

    click_result = component(
        page_num=page_num,
        width=preview_image.width,
        height=preview_image.height,
        preview_b64=preview_b64,
        key=f"pdf_redaction_click_component_v20_page_{page_num}",
        default=None,
    )

    if caption:
        st.caption(caption)

    return click_result


def show_page_slider_below_pdf(page_num, page_count, key_prefix):
    """Show page navigation below the current PDF window."""
    if page_count <= 1:
        st.caption("Page 1 of 1")
        return page_num

    slider_key = f"{key_prefix}_page_slider_under_pdf"
    new_page_num = st.slider(
        "Page",
        min_value=1,
        max_value=page_count,
        value=min(max(page_num + 1, 1), page_count),
        key=slider_key,
    ) - 1

    if new_page_num != page_num:
        st.session_state.active_page_num = new_page_num
        st.rerun()

    return page_num

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
    """Apply numeric conversion cell-by-cell without forcing entire columns to one type.

    pandas 3 removed DataFrame.applymap. pandas 2 provides DataFrame.map
    as the replacement, so use map when available and fall back to applymap
    for older environments.
    """
    if hasattr(df, "map"):
        return df.map(clean_numeric_string)
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


def prepare_image_for_ocr(image, quality_mode="High Accuracy"):
    """Improve OCR readability for scanned/rotated reports without changing coordinates."""
    quality_mode = str(quality_mode or "High Accuracy")

    if quality_mode == "Normal":
        return image.convert("RGB")

    # Most missed XLSX rows come from small, compressed black text. Grayscale +
    # contrast + sharpening usually improves EasyOCR without changing geometry.
    gray = ImageOps.grayscale(image)
    gray = ImageEnhance.Contrast(gray).enhance(1.8 if quality_mode == "High Accuracy" else 2.2)
    gray = ImageEnhance.Sharpness(gray).enhance(1.8 if quality_mode == "High Accuracy" else 2.5)
    gray = gray.filter(ImageFilter.SHARPEN)

    if quality_mode == "High Accuracy / Slower":
        # Gentle thresholding darkens faint glyphs while preserving anti-aliased edges
        # better than an aggressive black/white conversion.
        gray = gray.point(lambda px: 0 if px < 165 else 255 if px > 235 else px)

    return gray.convert("RGB")


def limit_ocr_image_size(image, max_pixels=4_500_000, max_side=2600):
    """Limit OCR image size to avoid Streamlit Cloud memory crashes.

    Returns (possibly_resized_image, scale_factor). OCR coordinates must be
    divided by the original render zoom multiplied by this scale factor.
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        return image, 1.0

    scale_by_pixels = (float(max_pixels) / float(width * height)) ** 0.5 if width * height > max_pixels else 1.0
    scale_by_side = float(max_side) / float(max(width, height)) if max(width, height) > max_side else 1.0
    scale = min(scale_by_pixels, scale_by_side, 1.0)

    if scale >= 0.999:
        return image, 1.0

    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    resized = image.resize(new_size, Image.Resampling.LANCZOS)
    return resized, scale


def readtext_with_quality(reader, pil_image, quality_mode="High Accuracy"):
    """Run EasyOCR with safer settings that avoid unsupported/OOM-prone paths."""
    image_array = np.array(pil_image)

    if quality_mode == "Normal":
        return reader.readtext(image_array, detail=1, paragraph=False)

    try:
        return reader.readtext(
            image_array,
            detail=1,
            paragraph=False,
            decoder="greedy",
            text_threshold=0.40,
            low_text=0.25,
            link_threshold=0.25,
            width_ths=0.90,
            add_margin=0.02,
        )
    except TypeError:
        # Older EasyOCR versions may not support every tuning parameter.
        return reader.readtext(image_array, detail=1, paragraph=False)


def merge_ocr_results(primary_results, secondary_results, zoom, distance_tolerance=3.0):
    """Merge two OCR passes while avoiding obvious duplicates."""
    merged = list(primary_results or [])
    seen = []

    def result_key(result):
        try:
            box, text_value, *_ = result
            text_value = str(text_value).strip().lower()
            xs = [point[0] / zoom for point in box]
            ys = [point[1] / zoom for point in box]
            return text_value, sum(xs) / len(xs), sum(ys) / len(ys)
        except Exception:
            return None

    for item in merged:
        key = result_key(item)
        if key:
            seen.append(key)

    for item in secondary_results or []:
        key = result_key(item)
        if not key or not key[0]:
            continue
        text_value, cx, cy = key
        duplicate = False
        for old_text, old_cx, old_cy in seen:
            if text_value == old_text and abs(cx - old_cx) <= distance_tolerance and abs(cy - old_cy) <= distance_tolerance:
                duplicate = True
                break
        if not duplicate:
            merged.append(item)
            seen.append(key)

    return merged


def ocr_page_words_for_xlsx_rotation(page, preview_zoom, rotation_degrees, ocr_zoom=None, quality_mode="High Accuracy"):
    """Render the page in the same rotated orientation as the XLSX preview and OCR it.

    v2.4 renders OCR at a higher resolution than the on-screen preview and
    preprocesses the rotated image before EasyOCR. Returned word coordinates are
    still divided by the OCR zoom, so they remain in the same PDF/ruler point
    coordinate system used by column extraction.
    """
    reader = get_ocr_reader()

    if reader is None:
        raise RuntimeError(
            "OCR support requires easyocr and numpy. Add easyocr and numpy to requirements.txt."
        )

    quality_mode = str(quality_mode or "High Accuracy")
    if ocr_zoom is None:
        ocr_zoom = 1.5 if quality_mode == "Normal" else 2.25 if quality_mode == "High Accuracy" else 2.75

    pix = page.get_pixmap(matrix=fitz.Matrix(ocr_zoom, ocr_zoom), alpha=False)
    image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    rotated_image = None
    limited_image = None
    processed_image = None
    effective_ocr_zoom = float(ocr_zoom)

    try:
        rotated_image = rotate_xlsx_preview_image(image, rotation_degrees)
        limited_image, resize_scale = limit_ocr_image_size(rotated_image)
        effective_ocr_zoom = float(ocr_zoom) * float(resize_scale or 1.0)
        processed_image = prepare_image_for_ocr(limited_image, quality_mode)
        ocr_results = readtext_with_quality(reader, processed_image, quality_mode)

        if quality_mode == "High Accuracy / Slower":
            # Second pass on the unprocessed image can recover text that
            # thresholding/sharpening occasionally damages. Merge by text + location.
            secondary_results = readtext_with_quality(reader, limited_image, "High Accuracy")
            ocr_results = merge_ocr_results(ocr_results, secondary_results, effective_ocr_zoom)
    finally:
        image.close()
        if rotated_image is not None:
            rotated_image.close()
        if limited_image is not None and limited_image is not rotated_image:
            limited_image.close()
        if processed_image is not None:
            processed_image.close()
        gc.collect()

    return easyocr_results_to_words(ocr_results, effective_ocr_zoom)


def get_words_for_xlsx_extraction(page, pnum, zoom, rotation_degrees, status_area=None, ocr_quality_mode="High Accuracy", ocr_zoom=None):
    """Return words in the same coordinate space as the XLSX preview/ruler.

    For normal pages, use the existing selectable-text/OCR fallback. For rotated
    XLSX views, selectable text boxes are transformed mathematically, but OCR is
    performed on the rotated image itself. This prevents sideways image PDFs from
    being OCR'd in the original orientation and producing garbage output.
    """
    rotation_degrees = int(rotation_degrees or 0) % 360

    if rotation_degrees == 0:
        return get_words_with_ocr_fallback(page, pnum, zoom, status_area=status_area)

    page_start = time.perf_counter()

    text_start = time.perf_counter()
    selectable_words = page.get_text("words")
    selectable_seconds = time.perf_counter() - text_start
    selectable_count = len(selectable_words or [])

    if has_enough_selectable_words(selectable_words):
        total_seconds = time.perf_counter() - page_start
        rotated_selectable_words = transform_words_for_xlsx_rotation(
            selectable_words,
            page,
            rotation_degrees,
        )

        if status_area is not None:
            status_area.success(
                f"Page {pnum + 1}: selectable text found and rotated to match the XLSX preview. "
                f"{selectable_count} words, {selectable_seconds:.2f}s. "
                f"OCR skipped. Total {total_seconds:.2f}s."
            )

        return rotated_selectable_words, "selectable", {
            "selectable_words": selectable_count,
            "ocr_words": 0,
            "selectable_seconds": selectable_seconds,
            "ocr_seconds": 0,
            "total_seconds": total_seconds,
        }

    if status_area is not None:
        status_area.warning(
            f"Page {pnum + 1}: only {selectable_count} selectable words found "
            f"({selectable_seconds:.2f}s). Running {ocr_quality_mode} OCR on the rotated XLSX preview..."
        )

    ocr_start = time.perf_counter()
    if ocr_zoom is None:
        ocr_zoom = 1.5 if ocr_quality_mode == "Normal" else 2.25 if ocr_quality_mode == "High Accuracy" else 2.75
    ocr_words = ocr_page_words_for_xlsx_rotation(
        page,
        zoom,
        rotation_degrees,
        ocr_zoom=ocr_zoom,
        quality_mode=ocr_quality_mode,
    )
    ocr_seconds = time.perf_counter() - ocr_start
    ocr_count = len(ocr_words or [])
    total_seconds = time.perf_counter() - page_start

    if status_area is not None:
        status_area.success(
            f"Page {pnum + 1}: rotated OCR used. "
            f"Selectable: {selectable_count} words in {selectable_seconds:.2f}s. "
            f"Rotated OCR: {ocr_count} text items in {ocr_seconds:.2f}s. "
            f"OCR quality: {ocr_quality_mode}. OCR zoom: {ocr_zoom:.2f}x. "
            f"Total {total_seconds:.2f}s."
        )

    return ocr_words, "ocr", {
        "selectable_words": selectable_count,
        "ocr_words": ocr_count,
        "selectable_seconds": selectable_seconds,
        "ocr_seconds": ocr_seconds,
        "total_seconds": total_seconds,
    }


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
# Main task router (single-file refactor boundary)
# Each task keeps its own UI/export branch below. Shared helpers above are task-neutral.
# ============================================================

left_col, right_col = st.columns([4, 1])

with right_col:
    st.subheader("Page Tools")

    if st.button("Clear Current Page"):
        st.session_state.redactions[page_num] = []
        st.session_state.columns[page_num] = []
        st.session_state.annotations[page_num] = []
        st.session_state.annotation_overlays.pop(page_num, None)
        st.session_state.last_ruler_click.pop(page_num, None)
        st.session_state.last_redaction_click.pop(page_num, None)
        st.session_state.last_annotation_click.pop(page_num, None)
        st.session_state.redaction_first_corner.pop(page_num, None)
        st.session_state.annotation_first_point.pop(page_num, None)
        gc.collect()
        st.rerun()

    if task == "Convert PDF to XLSX":
        use_same_columns_sidebar = bool(st.session_state.get("xlsx_use_same_columns_all_pages", False))
        layout_page_for_sidebar = 0 if use_same_columns_sidebar else page_num
        st.info(
            "Click the ruler to add/remove column boundaries. "
            "When shared layout is enabled, the Page 1 layout is used for every page."
        )
        st.write("Saved column points:")
        if use_same_columns_sidebar:
            st.caption("Shared layout from Page 1")
        st.write(st.session_state.columns.get(layout_page_for_sidebar, []))

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
                st.session_state.annotation_overlays.pop(page_num, None)
                st.session_state.annotation_overlays.pop(page_num, None)
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
            st.session_state.annotation_overlays.pop(page_num, None)
            st.session_state.selected_annotation_id.pop(page_num, None)
            gc.collect()
            st.rerun()

    else:
        st.info("Extract all selectable text from the PDF and download it as a .txt file.")


with left_col:
    if task == "Convert PDF to XLSX":
        st.subheader("Column Markup")

        st.markdown("**Column Layout**")

        layout_mode = st.radio(
            "Column layout mode",
            options=["Use same layout for all pages", "Different layout per page"],
            index=0 if st.session_state.get("xlsx_layout_mode", "Use same layout for all pages") == "Use same layout for all pages" else 1,
            horizontal=True,
            key="xlsx_layout_mode",
            help=(
                "Use one shared set of column markers for reports with the same layout on every page, "
                "or use separate markers when each page has a different layout."
            ),
        )
        use_same_columns_all_pages = layout_mode == "Use same layout for all pages"

        source_page_num = 0
        if use_same_columns_all_pages:
            source_page_num = st.selectbox(
                "Source page for shared column layout",
                options=list(range(page_count)),
                index=min(
                    max(int(st.session_state.get("xlsx_shared_source_page", 0) or 0), 0),
                    page_count - 1,
                ),
                format_func=lambda x: f"Page {x + 1}",
                key="xlsx_shared_source_page",
                help=(
                    "The ruler edits this page's saved column markers, and those markers are used for every page during conversion."
                ),
            )
            layout_page_num = int(source_page_num)
            st.caption(
                f"Shared layout is enabled: the ruler edits the Page {layout_page_num + 1} column layout, "
                "and that same layout will be applied to every page during XLSX generation."
            )
        else:
            layout_page_num = page_num
            st.caption("Page-specific layout is enabled: set column markers separately for each page.")

        st.markdown("**Rotation**")
        if "xlsx_rotation_degrees" not in st.session_state:
            st.session_state.xlsx_rotation_degrees = 0

        rotate_col1, rotate_col2, rotate_col3 = st.columns([1, 1, 3])
        with rotate_col1:
            if st.button("⟲ Rotate Left", key="xlsx_rotate_left"):
                st.session_state.xlsx_rotation_degrees = (int(st.session_state.xlsx_rotation_degrees) - 90) % 360
                st.rerun()
        with rotate_col2:
            if st.button("⟳ Rotate Right", key="xlsx_rotate_right"):
                st.session_state.xlsx_rotation_degrees = (int(st.session_state.xlsx_rotation_degrees) + 90) % 360
                st.rerun()
        with rotate_col3:
            xlsx_rotation_degrees = int(st.session_state.get("xlsx_rotation_degrees", 0) or 0) % 360
            st.write("Current XLSX rotation: **No rotation**" if xlsx_rotation_degrees == 0 else f"Current XLSX rotation: **{xlsx_rotation_degrees}° clockwise**")
            st.caption("Rotation affects the XLSX preview, ruler positions, extraction coordinates, and OCR input image.")

        st.markdown("**OCR Quality**")
        xlsx_ocr_quality_mode = st.selectbox(
            "OCR quality for scanned/rotated pages",
            options=["Normal", "High Accuracy", "High Accuracy / Slower"],
            index=1,
            key="xlsx_ocr_quality_mode",
            help=(
                "High Accuracy renders the page at higher resolution and sharpens/contrasts the OCR image. "
                "Use High Accuracy / Slower if small rows are still missed."
            ),
        )
        if xlsx_ocr_quality_mode == "Normal":
            xlsx_ocr_zoom = 1.5
        elif xlsx_ocr_quality_mode == "High Accuracy":
            xlsx_ocr_zoom = 2.25
        else:
            xlsx_ocr_zoom = 2.75
        st.caption(f"OCR render zoom: up to {xlsx_ocr_zoom:.2f}x with automatic pixel cap. Preview zoom remains {zoom:.2f}x, so ruler positions are unchanged.")

        current_columns = st.session_state.columns.get(layout_page_num, [])

        xlsx_preview_image = rotate_xlsx_preview_image(page_image, xlsx_rotation_degrees)

        preview = draw_column_overlay(
            xlsx_preview_image,
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

        show_page_slider_below_pdf(page_num, page_count, "xlsx")

        if click_result is not None:
            try:
                clicked_page = int(click_result.get("page_num"))
                clicked_x = int(float(click_result.get("x")))
                click_nonce = str(click_result.get("nonce", ""))

                last_click_key = f"{layout_page_num}_{page_num}"

                if (
                    clicked_page == page_num
                    and st.session_state.last_ruler_click.get(last_click_key) != click_nonce
                ):
                    latest_columns = st.session_state.columns.get(layout_page_num, [])
                    st.session_state.columns[layout_page_num] = toggle_column(
                        latest_columns,
                        clicked_x,
                    )
                    st.session_state.last_ruler_click[last_click_key] = click_nonce
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
                    # Defensive placeholder sheet.
                    # openpyxl raises IndexError("At least one sheet must be visible")
                    # if the workbook is closed before any sheet is written. Keeping
                    # this visible fallback prevents that failure even when no page has
                    # column markers, OCR/text extraction fails, or all extracted rows are blank.
                    fallback_sheet_name = "Result"
                    pd.DataFrame(
                        [["No columns marked or no extractable text/OCR text found."]]
                    ).to_excel(
                        writer,
                        sheet_name=fallback_sheet_name,
                        index=False,
                        header=False,
                    )

                    wrote_sheet = False
                    pages_with_columns = 0
                    pages_with_rows = 0
                    pages_using_ocr = 0
                    total_selectable_words = 0
                    total_ocr_words = 0

                    for pnum in range(total_pages):
                        progress_bar.progress((pnum + 1) / total_pages)

                        p = source_doc[pnum]
                        if use_same_columns_all_pages:
                            raw_columns = st.session_state.columns.get(layout_page_num, [])
                        else:
                            raw_columns = st.session_state.columns.get(pnum, [])

                        with status_area:
                            st.markdown(f"**Page {pnum + 1}**")

                            if not raw_columns:
                                if use_same_columns_all_pages:
                                    st.write(f"Skipped: shared Page {layout_page_num + 1} column layout has no markers set.")
                                else:
                                    st.write("Skipped: no column markers are set for this page.")
                                continue

                        pages_with_columns += 1

                        pdf_columns = sorted([x / zoom for x in raw_columns])
                        page_extraction_width = rotated_page_width_points(p, xlsx_rotation_degrees)
                        boundaries = [0] + pdf_columns + [page_extraction_width]

                        try:
                            with status_area:
                                page_status = st.empty()

                            words, extraction_method, extraction_stats = get_words_for_xlsx_extraction(
                                p,
                                pnum,
                                zoom,
                                xlsx_rotation_degrees,
                                status_area=page_status,
                                ocr_quality_mode=xlsx_ocr_quality_mode,
                                ocr_zoom=xlsx_ocr_zoom,
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

                    if wrote_sheet:
                        # Remove the placeholder sheet once at least one real page sheet exists.
                        # If removal fails for any reason, leaving the Result sheet is harmless
                        # and still keeps the workbook valid.
                        try:
                            fallback_ws = writer.book[fallback_sheet_name]
                            writer.book.remove(fallback_ws)
                        except Exception:
                            pass

                output_xlsx.seek(0)
                output_bytes = output_xlsx.getvalue()

                progress_bar.progress(1.0)
                total_seconds = time.perf_counter() - conversion_start

                layout_mode_text = (
                    f"Shared Page {layout_page_num + 1} column layout applied to all pages. "
                    if use_same_columns_all_pages
                    else "Page-specific column layouts used. "
                )
                rotation_mode_text = (
                    f"Rotation: {xlsx_rotation_degrees}° clockwise. "
                    if xlsx_rotation_degrees
                    else "Rotation: none. "
                )

                status_header.success(
                    "XLSX conversion complete. "
                    f"{layout_mode_text}"
                    f"{rotation_mode_text}"
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

        click = show_scrollable_clickable_redaction_markup(
            page_num,
            preview,
            caption="Click two opposite corners for each redaction box. Saved boxes stay visible.",
        )

        show_page_slider_below_pdf(page_num, page_count, "redaction")

        if click is not None:
            try:
                clicked_page = int(click.get("page_num", page_num))
                clicked_x = int(click["x"])
                clicked_y = int(click["y"])
                click_nonce = str(click.get("nonce", ""))

                previous_click = st.session_state.last_redaction_click.get(page_num)

                if clicked_page == page_num and previous_click != click_nonce:
                    st.session_state.last_redaction_click[page_num] = click_nonce
                    add_redaction_click(page_num, clicked_x, clicked_y)
                    st.rerun()
            except Exception as e:
                st.warning(f"Redaction click could not be processed: {e}")

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

        st.info(
            "v1.0 uses a browser-owned Fabric.js editor. Tool changes, typing, moving, resizing, "
            "and rotating do not trigger Streamlit reruns. Click Save Page Annotations in the editor "
            "before generating the annotated PDF or changing pages. v1.7 exports the saved transparent annotation layer for visual accuracy."
        )

        annotation_tool = "select"
        annotation_color_hex = "#ff0000"
        annotation_line_width = 3
        annotation_text = ""
        annotation_font_family = "Sans Serif"
        annotation_font_size = 18
        annotation_bold = False
        annotation_italic = False
        annotation_underline = False

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

        annotation_component_key = f"pdf_annotation_component_v21_page_{page_num}"
        pending_event = st.session_state.get(annotation_component_key)
        if pending_event is not None:
            apply_annotation_component_event(page_num, pending_event)

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
                f"Page {page_num + 1}: draw by dragging, or use Pen for freehand strokes. Switch to Select / Move to drag existing annotations. "
                "Text boxes render as text only; the blue outline appears only while selected in the editor."
            ),
        )

        if page_count > 1:
            slider_value = min(max(page_num + 1, 1), page_count)
            new_page_num = st.slider(
                "Page",
                min_value=1,
                max_value=page_count,
                value=slider_value,
                key="annotation_page_slider_under_pdf",
            ) - 1

            if new_page_num != page_num:
                st.session_state.active_page_num = new_page_num
                st.session_state.selected_annotation_id.pop(page_num, None)
                st.rerun()
        else:
            st.caption("Page 1 of 1")

        if event is not None:
            try:
                # Fallback for environments where the component value is returned here
                # but is not yet visible in st.session_state at the top of the rerun.
                # When this applies a new commit, rerun once so the component receives
                # the fresh canonical snapshot instead of the previous one.
                if apply_annotation_component_event(page_num, event):
                    st.rerun()
            except Exception as e:
                st.warning(f"Annotation update could not be processed: {e}")

        if st.button("Generate Annotated PDF"):
            output_bytes = apply_annotations_to_pdf(
                pdf_bytes,
                st.session_state.annotations,
                zoom,
                st.session_state.annotation_overlays,
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

        show_page_slider_below_pdf(page_num, page_count, "plain_text")

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
