import base64
import gc
import os
import html
import re
import tempfile
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


st.set_page_config(page_title="PDF Redactor / PDF to XLSX", layout="wide")

st.title("PDF Redactor / PDF to XLSX")

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
    ["Redact PDF", "Convert PDF to XLSX", "Convert PDF to Plain Text"],
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

page_num = st.selectbox(
    "Select Page",
    list(range(page_count)),
    format_func=lambda x: f"Page {x + 1}",
)

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
    """Display saved redaction and column-marker counts for every page."""
    st.subheader("Document Status")

    redaction_lines = get_page_count_summary(
        st.session_state.redactions,
        page_count,
        "redaction",
    )

    column_lines = get_page_count_summary(
        st.session_state.columns,
        page_count,
        "column marker",
    )

    status_col1, status_col2 = st.columns(2)

    with status_col1:
        st.markdown("**Redactions**")
        for line in redaction_lines:
            st.write(line)

    with status_col2:
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


def has_enough_selectable_words(words, minimum_words=5):
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
    """Convert EasyOCR output into PyMuPDF-like word tuples."""
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

    ocr_results = reader.readtext(np.array(image), detail=1, paragraph=False)

    image.close()
    del image
    gc.collect()

    return easyocr_results_to_words(ocr_results, zoom)


def get_words_with_ocr_fallback(page, pnum, zoom, status_box=None):
    """Try selectable text first, then OCR only if little/no text is found."""
    words = page.get_text("words")

    if has_enough_selectable_words(words):
        if status_box is not None:
            status_box.write(f"Page {pnum + 1}: selectable text found ({len(words)} words).")
        return words, "selectable"

    if status_box is not None:
        status_box.warning(f"Page {pnum + 1}: little/no selectable text found. Running OCR fallback...")

    words = ocr_page_words(page, zoom)

    if status_box is not None:
        if words:
            status_box.success(f"Page {pnum + 1}: OCR complete ({len(words)} text items found).")
        else:
            status_box.warning(f"Page {pnum + 1}: OCR complete, but no text was found.")

    return words, "ocr"


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
        st.session_state.last_ruler_click.pop(page_num, None)
        st.session_state.last_redaction_click.pop(page_num, None)
        st.session_state.redaction_first_corner.pop(page_num, None)
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
            status_box = st.empty()
            progress_bar = st.progress(0)

            try:
                source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                total_pages = len(source_doc)

                status_box.info("Starting XLSX conversion...")

                with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
                    wrote_sheet = False
                    pages_with_columns = 0
                    pages_with_rows = 0
                    pages_using_ocr = 0

                    for pnum in range(total_pages):
                        progress_bar.progress((pnum + 1) / total_pages)

                        p = source_doc[pnum]
                        raw_columns = st.session_state.columns.get(pnum, [])

                        if not raw_columns:
                            status_box.write(f"Page {pnum + 1}: skipped because no column markers are set.")
                            continue

                        pages_with_columns += 1

                        pdf_columns = sorted([x / zoom for x in raw_columns])
                        boundaries = [0] + pdf_columns + [p.rect.width]

                        try:
                            words, extraction_method = get_words_with_ocr_fallback(
                                p,
                                pnum,
                                zoom,
                                status_box=status_box,
                            )
                        except Exception as e:
                            status_box.error(f"Page {pnum + 1}: OCR/text extraction failed: {e}")
                            words = []
                            extraction_method = "failed"

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
                            status_box.success(
                                f"Page {pnum + 1}: wrote {len(table_rows)} row(s) using {extraction_method} extraction."
                            )
                        else:
                            status_box.warning(
                                f"Page {pnum + 1}: no rows were created after applying the column markers."
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

                status_box.success(
                    "XLSX conversion complete. "
                    f"Pages with column markers: {pages_with_columns}. "
                    f"Pages written: {pages_with_rows}. "
                    f"Pages using OCR fallback: {pages_using_ocr}."
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
