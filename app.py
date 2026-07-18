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
import pytesseract
from PIL import Image, ImageDraw, ImageOps
from pytesseract import Output
from streamlit_image_coordinates import streamlit_image_coordinates


st.set_page_config(page_title="PDF Tools", layout="wide")

st.title("PDF Tools")
st.caption("Version 1.13")

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


def add_redaction_click(redactions, first_corners, page_num, clicked_x, clicked_y):
    """Add one corner or complete a redaction rectangle in mode-local state."""
    first_corner = first_corners.get(page_num)

    if first_corner is None:
        first_corners[page_num] = {
            "x": clicked_x,
            "y": clicked_y,
        }
        return

    x0 = min(first_corner["x"], clicked_x)
    y0 = min(first_corner["y"], clicked_y)
    x1 = max(first_corner["x"], clicked_x)
    y1 = max(first_corner["y"], clicked_y)

    if abs(x1 - x0) >= 5 and abs(y1 - y0) >= 5:
        redactions.setdefault(page_num, []).append(
            {
                "x": x0,
                "y": y0,
                "w": x1 - x0,
                "h": y1 - y0,
            }
        )

    first_corners.pop(page_num, None)


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



def extract_ocr_words_by_columns(page, boundaries, render_zoom=4.0, languages="eng+fra"):
    """OCR each user-defined column independently.

    The ruler boundaries are converted to rendered-image coordinates. Each
    column is cropped, enlarged, contrast-enhanced, and sent to Tesseract as
    its own text block. Returned coordinates are translated back to PDF page
    coordinates so the normal row-grouping pipeline can combine the columns.
    """
    pix = page.get_pixmap(
        matrix=fitz.Matrix(render_zoom, render_zoom),
        alpha=False,
    )
    source_image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    scale_x = pix.width / page.rect.width
    scale_y = pix.height / page.rect.height
    words = []

    try:
        for column_index in range(len(boundaries) - 1):
            pdf_left = max(0.0, float(boundaries[column_index]))
            pdf_right = min(float(page.rect.width), float(boundaries[column_index + 1]))
            if pdf_right <= pdf_left:
                continue

            pixel_left = max(0, int(round(pdf_left * scale_x)))
            pixel_right = min(pix.width, int(round(pdf_right * scale_x)))
            if pixel_right - pixel_left < 3:
                continue

            crop = source_image.crop((pixel_left, 0, pixel_right, pix.height))
            gray = ImageOps.autocontrast(ImageOps.grayscale(crop))

            # Enlarging narrow columns makes isolated one- and two-digit values
            # less likely to be discarded as noise by Tesseract.
            enlargement = 1.5
            enlarged = gray.resize(
                (max(1, int(gray.width * enlargement)), max(1, int(gray.height * enlargement))),
                Image.Resampling.LANCZOS,
            )
            bordered = ImageOps.expand(enlarged, border=16, fill=255)

            try:
                data = pytesseract.image_to_data(
                    bordered,
                    lang=languages,
                    output_type=Output.DICT,
                    config="--oem 3 --psm 6 -c preserve_interword_spaces=1",
                )
            finally:
                bordered.close()
                enlarged.close()
                gray.close()
                crop.close()

            for index, raw_text in enumerate(data.get("text", [])):
                text = str(raw_text).strip()
                if not text:
                    continue

                try:
                    confidence = float(data["conf"][index])
                except (TypeError, ValueError, KeyError, IndexError):
                    confidence = -1

                # Keep low-confidence short values because narrow numeric
                # columns frequently contain isolated digits.
                if confidence < 10:
                    continue

                local_left = (float(data["left"][index]) - 16) / enlargement
                local_top = (float(data["top"][index]) - 16) / enlargement
                local_width = float(data["width"][index]) / enlargement
                local_height = float(data["height"][index]) / enlargement

                left = (pixel_left + max(0.0, local_left)) / scale_x
                top = max(0.0, local_top) / scale_y
                width = local_width / scale_x
                height = local_height / scale_y

                words.append(
                    (
                        left,
                        top,
                        left + width,
                        top + height,
                        text,
                        column_index,
                        int(data.get("par_num", [0])[index]),
                        int(data.get("line_num", [0])[index]),
                    )
                )
    finally:
        source_image.close()

    return words


def get_page_words_with_ocr_fallback(page, boundaries):
    """Use native PDF words first, then column-aware OCR when none exist."""
    native_words = page.get_text("words")
    if native_words:
        return native_words, False

    return extract_ocr_words_by_columns(page, boundaries), True


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

    Uses DataFrame.map on newer Pandas releases and falls back to a column-wise
    Series.map implementation for compatibility with older releases.
    """
    dataframe_map = getattr(df, "map", None)

    if callable(dataframe_map):
        return dataframe_map(clean_numeric_string)

    return df.apply(lambda column: column.map(clean_numeric_string))


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

# ============================================================
# Version 1.0 architecture and session isolation
# ============================================================

APP_VERSION = "1.13"
ZOOM = 1.5

MODE_REDACT = "redact"
MODE_XLSX = "xlsx"
MODE_TEXT = "text"
MODE_ROTATE = "rotate"
MODE_ANNOTATE = "annotate"

MODE_LABELS = {
    MODE_REDACT: "Redact PDF",
    MODE_XLSX: "Convert PDF to XLSX",
    MODE_TEXT: "Convert PDF to Plain Text",
    MODE_ROTATE: "Rotate PDF",
    MODE_ANNOTATE: "Annotate PDF",
}


def switch_mode(new_mode):
    """Erase all uploaded files and working state when changing tools."""
    if st.session_state.get("active_mode") != new_mode:
        st.session_state.clear()
        st.session_state["active_mode"] = new_mode
        st.session_state["uploader_nonce"] = 0
        gc.collect()
        st.rerun()


def reset_current_mode():
    """Clear the current tool's PDF and state while keeping that tool selected."""
    active_mode = st.session_state.get("active_mode")
    next_nonce = st.session_state.get("uploader_nonce", 0) + 1
    st.session_state.clear()
    st.session_state["active_mode"] = active_mode
    st.session_state["uploader_nonce"] = next_nonce
    gc.collect()
    st.rerun()


def upload_pdf_for_mode(mode):
    """Prompt for and retain one PDF only within the currently selected mode."""
    bytes_key = f"{mode}_pdf_bytes"
    name_key = f"{mode}_pdf_name"
    uploader_nonce = st.session_state.get("uploader_nonce", 0)

    uploaded = st.file_uploader(
        "Upload PDF",
        type=["pdf"],
        key=f"{mode}_pdf_upload_{uploader_nonce}",
    )

    if uploaded is not None:
        st.session_state[bytes_key] = uploaded.getvalue()
        st.session_state[name_key] = uploaded.name

    return st.session_state.get(bytes_key)


def get_pdf_page_count(pdf_bytes):
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return len(doc)
    except Exception as exc:
        st.error(f"Unable to open PDF: {exc}")
        return None
    finally:
        if doc is not None:
            doc.close()
        gc.collect()


def render_pdf_page(pdf_bytes, page_num, zoom=ZOOM):
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    finally:
        if doc is not None:
            doc.close()
        gc.collect()


def show_mode_document_status(mapping, page_count, item_label):
    st.subheader("Document Status")
    for pnum in range(page_count):
        count = len(mapping.get(pnum, []))
        label = item_label if count == 1 else f"{item_label}s"
        st.write(f"Page {pnum + 1}: {count} {label}")


def prepare_mode_pdf(mode):
    """Upload, validate, and select a page for one independent tool."""
    pdf_bytes = upload_pdf_for_mode(mode)
    if pdf_bytes is None:
        st.info(f"Upload a PDF to begin: {MODE_LABELS[mode]}.")
        return None

    page_count = get_pdf_page_count(pdf_bytes)
    if page_count is None:
        return None

    st.success(f"Loaded PDF with {page_count} page(s).")
    page_num = st.selectbox(
        "Select Page",
        list(range(page_count)),
        format_func=lambda value: f"Page {value + 1}",
        key=f"{mode}_page_num",
    )
    page_image = render_pdf_page(pdf_bytes, page_num)
    return pdf_bytes, page_count, page_num, page_image


def render_redact_tool():
    st.header("Redact PDF")
    prepared = prepare_mode_pdf(MODE_REDACT)
    if prepared is None:
        return

    pdf_bytes, page_count, page_num, page_image = prepared
    redactions = st.session_state.setdefault("redact_redactions", {})
    first_corners = st.session_state.setdefault("redact_first_corners", {})
    last_clicks = st.session_state.setdefault("redact_last_clicks", {})
    redactions.setdefault(page_num, [])

    show_mode_document_status(redactions, page_count, "redaction")
    left_col, right_col = st.columns([4, 1])

    with right_col:
        st.subheader("Page Tools")
        if st.button("Clear Current Page", key="redact_clear_page"):
            redactions[page_num] = []
            first_corners.pop(page_num, None)
            last_clicks.pop(page_num, None)
            gc.collect()
            st.rerun()

        st.info("Click two opposite corners to create each redaction rectangle.")
        st.write(f"Saved redaction rectangles: {len(redactions[page_num])}")

        if first_corners.get(page_num):
            st.warning("First corner selected. Click the opposite corner.")

        if st.button("Undo Last Redaction", key="redact_undo"):
            if redactions[page_num]:
                redactions[page_num].pop()
                gc.collect()
                st.rerun()

        if st.button("Cancel Current Rectangle", key="redact_cancel"):
            first_corners.pop(page_num, None)
            gc.collect()
            st.rerun()

    with left_col:
        st.subheader("Redaction Markup")
        preview = draw_redaction_overlay(
            page_image,
            redactions[page_num],
            first_corners.get(page_num),
        )

        click = streamlit_image_coordinates(
            preview,
            width=page_image.width,
            key=f"redact_image_page_{page_num}",
        )

        if click is not None:
            clicked_x = int(click["x"])
            clicked_y = int(click["y"])
            click_signature = f"{clicked_x}_{clicked_y}"

            if last_clicks.get(page_num) != click_signature:
                last_clicks[page_num] = click_signature
                add_redaction_click(
                    redactions,
                    first_corners,
                    page_num,
                    clicked_x,
                    clicked_y,
                )
                st.rerun()

        st.caption("Click two opposite corners for each redaction box. Saved boxes stay visible.")

        if st.button("Generate Redacted PDF", key="redact_generate"):
            redacted_doc = None
            output_pdf = BytesIO()
            try:
                redacted_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                for pnum, rects in redactions.items():
                    page = redacted_doc[pnum]
                    for rect_data in rects:
                        # Click coordinates come from the rendered, rotated page view.
                        # PDF annotations use the page's underlying unrotated coordinate
                        # system, so map the displayed rectangle back before redacting.
                        display_rect = fitz.Rect(
                            rect_data["x"] / ZOOM,
                            rect_data["y"] / ZOOM,
                            (rect_data["x"] + rect_data["w"]) / ZOOM,
                            (rect_data["y"] + rect_data["h"]) / ZOOM,
                        ).normalize()

                        pdf_rect = (display_rect * page.derotation_matrix).normalize()
                        pdf_rect = pdf_rect & page.cropbox

                        if not pdf_rect.is_empty and not pdf_rect.is_infinite:
                            page.add_redact_annot(pdf_rect, fill=(0, 0, 0))
                    page.apply_redactions()

                redacted_doc.save(output_pdf)
                output_bytes = output_pdf.getvalue()
            finally:
                if redacted_doc is not None:
                    redacted_doc.close()
                output_pdf.close()
                gc.collect()

            st.download_button(
                "Download Redacted PDF",
                data=output_bytes,
                file_name="redacted.pdf",
                mime="application/pdf",
                key="redact_download",
            )

    del page_image
    gc.collect()


def render_xlsx_tool():
    st.header("Convert PDF to XLSX")
    prepared = prepare_mode_pdf(MODE_XLSX)
    if prepared is None:
        return

    pdf_bytes, page_count, page_num, page_image = prepared
    columns = st.session_state.setdefault("xlsx_columns", {})
    last_clicks = st.session_state.setdefault("xlsx_last_ruler_clicks", {})
    columns.setdefault(page_num, [])

    show_mode_document_status(columns, page_count, "column marker")
    left_col, right_col = st.columns([4, 1])

    with right_col:
        st.subheader("Page Tools")
        if st.button("Clear Current Page", key="xlsx_clear_page"):
            columns[page_num] = []
            last_clicks.pop(page_num, None)
            gc.collect()
            st.rerun()

        st.info(
            "Click the ruler to add or remove column boundaries. "
            "Pages without selectable text will be OCR-processed automatically, one marked column at a time."
        )
        st.write("Saved column points:")
        st.write(columns[page_num])

    with left_col:
        st.subheader("Column Markup")
        preview = draw_column_overlay(page_image, columns[page_num])
        click_result = show_scrollable_clickable_xlsx_markup(
            page_num=page_num,
            preview_image=preview,
            columns=columns[page_num],
            caption=(
                f"Page {page_num + 1}: click the ruler inside the PDF scroll window "
                "to add or remove column boundaries."
            ),
        )

        if click_result is not None:
            try:
                clicked_page = int(click_result.get("page_num"))
                clicked_x = int(float(click_result.get("x")))
                click_nonce = str(click_result.get("nonce", ""))

                if clicked_page == page_num and last_clicks.get(page_num) != click_nonce:
                    columns[page_num] = toggle_column(columns[page_num], clicked_x)
                    last_clicks[page_num] = click_nonce
                    st.rerun()
            except Exception as exc:
                st.warning(f"Column click could not be processed: {exc}")

        if st.button("Generate XLSX", key="xlsx_generate"):
            source_doc = None
            output_xlsx = BytesIO()
            try:
                source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
                    # Create a visible worksheet immediately. If an exception occurs
                    # during extraction, openpyxl can still save/close the workbook
                    # without raising "At least one sheet must be visible" and masking
                    # the real extraction error.
                    pd.DataFrame(
                        [["No columns marked or no extractable text found."]]
                    ).to_excel(
                        writer,
                        sheet_name="Result",
                        index=False,
                        header=False,
                    )

                    wrote_sheet = False
                    ocr_pages = []

                    for pnum in range(len(source_doc)):
                        raw_columns = columns.get(pnum, [])
                        if not raw_columns:
                            continue

                        page = source_doc[pnum]
                        pdf_columns = sorted(x / ZOOM for x in raw_columns)
                        boundaries = [0] + pdf_columns + [page.rect.width]
                        try:
                            page_words, used_ocr = get_page_words_with_ocr_fallback(page, boundaries)
                        except pytesseract.TesseractNotFoundError as exc:
                            raise RuntimeError(
                                "OCR was required, but the Tesseract system package is not installed. "
                                "Deploy packages.txt alongside requirements.txt."
                            ) from exc
                        except pytesseract.TesseractError as exc:
                            raise RuntimeError(
                                f"OCR failed on page {pnum + 1}: {exc}"
                            ) from exc

                        if used_ocr:
                            ocr_pages.append(pnum + 1)

                        rows = group_words_into_rows(
                            page_words,
                            y_tolerance=6 if used_ocr else 4,
                        )
                        table_rows = []

                        for row in rows:
                            cells = [""] * (len(boundaries) - 1)
                            for word in sorted(row["words"], key=lambda item: item[0]):
                                col_index = assign_word_to_column(word, boundaries)
                                if col_index is not None:
                                    cells[col_index] = (cells[col_index] + " " + word[4]).strip()
                            if any(cell.strip() for cell in cells):
                                table_rows.append(cells)

                        if table_rows:
                            df = convert_numeric_cells(pd.DataFrame(table_rows))
                            df.to_excel(
                                writer,
                                sheet_name=f"Page_{pnum + 1}",
                                index=False,
                                header=False,
                            )
                            wrote_sheet = True

                    if wrote_sheet and "Result" in writer.book.sheetnames:
                        writer.book.remove(writer.book["Result"])

                output_bytes = output_xlsx.getvalue()
                st.session_state["xlsx_last_ocr_pages"] = ocr_pages
            finally:
                if source_doc is not None:
                    source_doc.close()
                output_xlsx.close()
                gc.collect()

            ocr_pages = st.session_state.get("xlsx_last_ocr_pages", [])
            if ocr_pages:
                page_list = ", ".join(str(number) for number in ocr_pages)
                st.info(f"Column-aware OCR was automatically used on page(s): {page_list}.")
            else:
                st.success("The XLSX was generated from the PDF text layer; OCR was not needed.")

            st.download_button(
                "Download XLSX",
                data=output_bytes,
                file_name="extracted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="xlsx_download",
            )

    del page_image
    gc.collect()


def render_rotate_tool():
    """Rotate individual PDF pages and create a new downloadable PDF.

    This workflow owns its uploader and all rotation state through MODE_ROTATE
    keys. It does not read or modify state belonging to the other tools.
    """
    st.header("Rotate PDF")
    prepared = prepare_mode_pdf(MODE_ROTATE)
    if prepared is None:
        return

    pdf_bytes, page_count, page_num, page_image = prepared
    rotations = st.session_state.setdefault("rotate_page_rotations", {})
    rotations.setdefault(page_num, 0)

    st.subheader("Rotation Controls")
    left_button_col, right_button_col, spacer_col = st.columns([1, 1, 3])

    with left_button_col:
        if st.button("90 degrees left", key="rotate_left", use_container_width=True):
            rotations[page_num] = (rotations[page_num] - 90) % 360
            gc.collect()
            st.rerun()

    with right_button_col:
        if st.button("90 degrees right", key="rotate_right", use_container_width=True):
            rotations[page_num] = (rotations[page_num] + 90) % 360
            gc.collect()
            st.rerun()

    current_rotation = rotations[page_num]
    if current_rotation:
        rotated_preview = page_image.rotate(
            -current_rotation,
            expand=True,
            resample=Image.Resampling.BICUBIC,
        )
    else:
        rotated_preview = page_image

    st.caption(
        f"Page {page_num + 1} of {page_count} - current rotation: "
        f"{current_rotation} degrees clockwise"
    )

    # streamlit_image_coordinates provides the same large, scrollable page-style
    # display used by the redaction workflow. Click results are intentionally ignored.
    streamlit_image_coordinates(
        rotated_preview,
        width=rotated_preview.width,
        key=f"rotate_preview_page_{page_num}_{current_rotation}",
    )

    rotated_pages = {
        pnum: angle
        for pnum, angle in rotations.items()
        if angle % 360 != 0
    }

    if rotated_pages:
        summary = ", ".join(
            f"Page {pnum + 1}: {angle % 360} degrees"
            for pnum, angle in sorted(rotated_pages.items())
        )
        st.info(f"Saved rotations - {summary}")
    else:
        st.info("No page rotations have been applied yet.")

    if st.button("Generate Rotated PDF", key="rotate_generate"):
        rotated_doc = None
        output_pdf = BytesIO()

        try:
            rotated_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

            for pnum in range(len(rotated_doc)):
                angle = rotations.get(pnum, 0) % 360
                if angle:
                    page = rotated_doc[pnum]
                    page.set_rotation((page.rotation + angle) % 360)

            rotated_doc.save(output_pdf)
            output_bytes = output_pdf.getvalue()
        finally:
            if rotated_doc is not None:
                rotated_doc.close()
            output_pdf.close()
            gc.collect()

        original_name = st.session_state.get("rotate_pdf_name", "document.pdf")
        base_name = os.path.splitext(original_name)[0] or "document"

        st.download_button(
            "Download Rotated PDF",
            data=output_bytes,
            file_name=f"{base_name}_rotated.pdf",
            mime="application/pdf",
            key="rotate_download",
        )

    if rotated_preview is not page_image:
        del rotated_preview
    del page_image
    gc.collect()


def render_text_tool():
    st.header("Convert PDF to Plain Text")
    prepared = prepare_mode_pdf(MODE_TEXT)
    if prepared is None:
        return

    pdf_bytes, _page_count, page_num, page_image = prepared
    st.write(
        "This extracts selectable text from every page and saves it as a plain `.txt` file. "
        "Scanned image-only PDFs may produce little or no text because OCR is not enabled."
    )
    st.image(page_image, caption=f"Page {page_num + 1} preview", use_column_width=False)

    if st.button("Generate Plain Text File", key="text_generate"):
        output_text_bytes = extract_plain_text_from_pdf(pdf_bytes)
        st.download_button(
            "Download Plain Text File",
            data=output_text_bytes,
            file_name="extracted_text.txt",
            mime="text/plain",
            key="text_download",
        )

    del page_image
    gc.collect()



# ============================================================
# Independent PDF annotation tool (no Fabric.js)
# ============================================================


def get_annotation_component():
    """Create a lightweight SVG annotation editor as a local Streamlit component."""
    component_dir = os.path.join(tempfile.gettempdir(), "pdf_annotation_component_v5")
    os.makedirs(component_dir, exist_ok=True)
    index_path = os.path.join(component_dir, "index.html")

    component_html = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
html, body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; font-family:Arial,sans-serif; background:#fff; }
#palette { height:44px; display:flex; align-items:center; gap:7px; padding:5px 8px; box-sizing:border-box; background:#f7f7f7; border:1px solid #bbb; border-bottom:0; }
.swatch { width:25px; height:25px; border-radius:4px; border:2px solid #fff; box-shadow:0 0 0 1px #777; cursor:pointer; padding:0; }
.swatch.active { box-shadow:0 0 0 3px #1677ff; }
#more-label { display:flex; align-items:center; gap:6px; margin-left:5px; font-size:13px; color:#333; }
#spectrum-wrap { display:flex; align-items:center; gap:6px; margin-left:5px; }
#spectrum { width:190px; height:25px; border:1px solid #777; border-radius:4px; cursor:crosshair; touch-action:none; background:linear-gradient(90deg,#ff0000 0%,#ffff00 16.66%,#00ff00 33.33%,#00ffff 50%,#0000ff 66.66%,#ff00ff 83.33%,#ff0000 100%); }
#spectrum-knob { width:12px; height:25px; margin-left:-6px; border:2px solid #fff; box-shadow:0 0 0 1px #222; border-radius:3px; pointer-events:none; box-sizing:border-box; }
#scroll { width:100%; height:676px; overflow:auto; border:1px solid #bbb; box-sizing:border-box; background:#ddd; }
#stage { position:relative; background:#fff; }
#page { display:block; max-width:none; user-select:none; -webkit-user-drag:none; }
#overlay { position:absolute; left:0; top:0; touch-action:none; z-index:2; }
.inline-text { position:absolute; z-index:5; min-width:2px; min-height:1.2em; border:0; outline:none; padding:0; margin:0; overflow:hidden; resize:none; background:transparent; line-height:1.15; white-space:pre; caret-color:#1677ff; box-shadow:none; }
.inline-text::placeholder { color:transparent; }
.object { cursor:pointer; }
.selected-outline { fill:none; stroke:#1677ff; stroke-width:1.5; stroke-dasharray:6 4; pointer-events:none; }
.handle { fill:#fff; stroke:#1677ff; stroke-width:2; cursor:nwse-resize; }
.rotate-line { stroke:#1677ff; stroke-width:1.5; pointer-events:none; }
.rotate-handle { fill:#fff; stroke:#1677ff; stroke-width:2; cursor:grab; }
.endpoint { fill:#fff; stroke:#1677ff; stroke-width:2; cursor:crosshair; }
</style>
</head>
<body>
<div id="palette"></div>
<div id="scroll"><div id="stage"><img id="page"><svg id="overlay"></svg></div></div>
<script>
const NS='http://www.w3.org/2000/svg';
const DEFAULT_COLORS=['#000000','#ffffff','#d00000','#e56b00','#f2c500','#008a2e','#00a7b5','#0057d9','#7a32a8','#7a4a20','#777777'];
let args={}, objects=[], selectedId=null, action=null, draft=null, activeColor='#000000', undoStack=[], activeEditor=null, suppressNextTextClick=false;
const stage=document.getElementById('stage'), img=document.getElementById('page'), svg=document.getElementById('overlay'), palette=document.getElementById('palette');
function msg(type,data){ window.parent.postMessage(Object.assign({isStreamlitMessage:true,type:type},data),'*'); }
function ready(){ msg('streamlit:componentReady',{apiVersion:1}); msg('streamlit:setFrameHeight',{height:735}); }
function emit(){ msg('streamlit:setComponentValue',{value:{annotations:objects,selected_id:selectedId,active_color:activeColor,undo_stack:undoStack,nonce:Date.now()+'_'+Math.random().toString(36).slice(2)},dataType:'json'}); }
function snapshot(){ return JSON.parse(JSON.stringify(objects)); }
function pushHistory(){ undoStack.push(snapshot()); if(undoStack.length>30) undoStack.shift(); }
function el(name, attrs={}){ const n=document.createElementNS(NS,name); Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v))); return n; }
function byId(id){ return objects.find(o=>o.id===id); }
function point(e){ const r=svg.getBoundingClientRect(); return {x:e.clientX-r.left,y:e.clientY-r.top}; }
function thickness(){ return Number(args.thickness||2); }
function rid(){ return 'a_'+Date.now()+'_'+Math.random().toString(36).slice(2); }
function bbox(o){
 if(o.type==='line'||o.type==='arrow'){ return {x:Math.min(o.x1,o.x2),y:Math.min(o.y1,o.y2),w:Math.max(1,Math.abs(o.x2-o.x1)),h:Math.max(1,Math.abs(o.y2-o.y1))}; }
 if(o.type==='pen'){ const xs=o.points.map(p=>p.x), ys=o.points.map(p=>p.y); return {x:Math.min(...xs),y:Math.min(...ys),w:Math.max(1,Math.max(...xs)-Math.min(...xs)),h:Math.max(1,Math.max(...ys)-Math.min(...ys))}; }
 return {x:o.x,y:o.y,w:Math.max(1,o.w),h:Math.max(1,o.h)};
}
function center(o){ const b=bbox(o); return {x:b.x+b.w/2,y:b.y+b.h/2}; }
function transform(o){ const c=center(o); return `rotate(${Number(o.rotation||0)} ${c.x} ${c.y})`; }
function arrowPolygon(o){
 const dx=o.x2-o.x1,dy=o.y2-o.y1,len=Math.max(0.001,Math.hypot(dx,dy)),ux=dx/len,uy=dy/len;
 const t=Math.max(1,Number(o.thickness||2));
 const headLength=Math.min(len*0.65,Math.max(10,t*5.2));
 const headHalfWidth=Math.max(5,t*2.8);
 const shaftHalfWidth=t*0.75;
 const px=-uy,py=ux;
 const bx=o.x2-ux*headLength,by=o.y2-uy*headLength;
 return [
   `${o.x1+px*shaftHalfWidth},${o.y1+py*shaftHalfWidth}`,
   `${bx+px*shaftHalfWidth},${by+py*shaftHalfWidth}`,
   `${bx+px*headHalfWidth},${by+py*headHalfWidth}`,
   `${o.x2},${o.y2}`,
   `${bx-px*headHalfWidth},${by-py*headHalfWidth}`,
   `${bx-px*shaftHalfWidth},${by-py*shaftHalfWidth}`,
   `${o.x1-px*shaftHalfWidth},${o.y1-py*shaftHalfWidth}`
 ].join(' ');
}
function objectNode(o){
 const g=el('g',{'class':'object','data-id':o.id,transform:transform(o)});
 const common={stroke:o.color||'#000', 'stroke-width':o.thickness||2, fill:'none','vector-effect':'non-scaling-stroke'};
 let n;
 if(o.type==='rectangle') n=el('rect',Object.assign({x:o.x,y:o.y,width:o.w,height:o.h},common));
 else if(o.type==='circle') n=el('ellipse',Object.assign({cx:o.x+o.w/2,cy:o.y+o.h/2,rx:Math.abs(o.w/2),ry:Math.abs(o.h/2)},common));
 else if(o.type==='line') n=el('line',Object.assign({x1:o.x1,y1:o.y1,x2:o.x2,y2:o.y2,'stroke-linecap':'round'},common));
 else if(o.type==='arrow'){
   n=el('polygon',{points:arrowPolygon(o),fill:o.color||'#000',stroke:o.color||'#000','stroke-width':Math.max(0.5,Number(o.thickness||2)*0.12),'stroke-linejoin':'round'});
 }
 else if(o.type==='pen') n=el('polyline',Object.assign({points:o.points.map(p=>`${p.x},${p.y}`).join(' '),'stroke-linecap':'round','stroke-linejoin':'round'},common));
 else if(o.type==='text'){
   n=el('text',{x:o.x,y:o.y+o.font_size,fill:o.color||'#000','font-family':o.font==='Arial'?'Arial':'sans-serif','font-size':o.font_size,'font-weight':o.bold?'bold':'normal','font-style':o.italic?'italic':'normal','text-decoration':o.underline?'underline':'none'});
   String(o.text||'').split('\n').forEach((line,index)=>{const t=el('tspan',{x:o.x,dy:index===0?0:o.font_size*1.2});t.textContent=line;n.appendChild(t);});
 }
 if(n) g.appendChild(n);
 g.addEventListener('pointerdown', e=>{ if(args.tool!=='select') return; e.stopPropagation(); selectedId=o.id; const p=point(e); action={kind:'move',id:o.id,start:p,original:JSON.parse(JSON.stringify(o)),historySaved:false}; svg.setPointerCapture(e.pointerId); render(); });
 return g;
}
function selectionNodes(o){
 const out=[], b=bbox(o), c=center(o), pad=5;
 out.push(el('rect',{x:b.x-pad,y:b.y-pad,width:b.w+2*pad,height:b.h+2*pad,class:'selected-outline',transform:transform(o)}));
 if(o.type==='line'||o.type==='arrow'){
   [['p1',o.x1,o.y1],['p2',o.x2,o.y2]].forEach(([kind,x,y])=>{ const h=el('circle',{cx:x,cy:y,r:6,class:'endpoint'}); h.addEventListener('pointerdown',e=>{e.stopPropagation();action={kind,id:o.id,original:JSON.parse(JSON.stringify(o)),historySaved:false};svg.setPointerCapture(e.pointerId)}); out.push(h); });
 } else {
   const corners=[['nw',b.x,b.y],['ne',b.x+b.w,b.y],['sw',b.x,b.y+b.h],['se',b.x+b.w,b.y+b.h]];
   corners.forEach(([kind,x,y])=>{ const h=el('rect',{x:x-5,y:y-5,width:10,height:10,class:'handle'}); h.addEventListener('pointerdown',e=>{e.stopPropagation();action={kind:'resize',corner:kind,id:o.id,original:JSON.parse(JSON.stringify(o)),start:point(e),historySaved:false};svg.setPointerCapture(e.pointerId)}); out.push(h); });
 }
 const ry=b.y-28; out.push(el('line',{x1:c.x,y1:b.y-pad,x2:c.x,y2:ry+6,class:'rotate-line'}));
 const rh=el('circle',{cx:c.x,cy:ry,r:7,class:'rotate-handle'}); rh.addEventListener('pointerdown',e=>{e.stopPropagation();action={kind:'rotate',id:o.id,center:c,original:JSON.parse(JSON.stringify(o)),historySaved:false};svg.setPointerCapture(e.pointerId)}); out.push(rh);
 return out;
}
function hsvToHex(h,s=1,v=1){
 const c=v*s,x=c*(1-Math.abs((h/60)%2-1)),m=v-c; let r=0,g=0,b=0;
 if(h<60){r=c;g=x;} else if(h<120){r=x;g=c;} else if(h<180){g=c;b=x;} else if(h<240){g=x;b=c;} else if(h<300){r=x;b=c;} else {r=c;b=x;}
 const hex=n=>Math.round((n+m)*255).toString(16).padStart(2,'0'); return '#'+hex(r)+hex(g)+hex(b);
}
function renderPalette(){
 palette.innerHTML='';
 DEFAULT_COLORS.forEach(c=>{ const b=document.createElement('button');b.className='swatch'+(c.toLowerCase()===activeColor.toLowerCase()?' active':'');b.style.background=c;b.title=c;b.addEventListener('click',()=>setColor(c,true));palette.appendChild(b); });
 const wrap=document.createElement('div');wrap.id='spectrum-wrap';
 const label=document.createElement('span');label.id='more-label';label.textContent='More colours';wrap.appendChild(label);
 const spectrum=document.createElement('div');spectrum.id='spectrum';
 const knob=document.createElement('div');knob.id='spectrum-knob';knob.style.position='absolute';
 spectrum.style.position='relative';spectrum.appendChild(knob);
 let dragging=false;
 const choose=e=>{ const r=spectrum.getBoundingClientRect(); const x=Math.max(0,Math.min(r.width,e.clientX-r.left)); knob.style.left=x+'px'; setColor(hsvToHex((x/r.width)*360),false,false); };
 spectrum.addEventListener('pointerdown',e=>{e.preventDefault();e.stopPropagation();dragging=true;if(selectedId)pushHistory();spectrum.setPointerCapture(e.pointerId);choose(e);});
 spectrum.addEventListener('pointermove',e=>{if(dragging)choose(e);});
 spectrum.addEventListener('pointerup',e=>{if(!dragging)return;choose(e);dragging=false;renderPalette();emit();});
 spectrum.addEventListener('pointercancel',()=>{dragging=false;renderPalette();emit();});
 wrap.appendChild(spectrum);palette.appendChild(wrap);
}
function setColor(c,commit=true,refreshPalette=true){ activeColor=c; const o=byId(selectedId); if(o){ if(commit)pushHistory(); o.color=c; if(commit)emit(); } render(!refreshPalette); }
function render(skipPalette=false){
 svg.innerHTML='';
 objects.forEach(o=>svg.appendChild(objectNode(o)));
 if(draft) svg.appendChild(objectNode(draft));
 const sel=byId(selectedId); if(sel) selectionNodes(sel).forEach(n=>svg.appendChild(n));
 if(!skipPalette) renderPalette();
}
function beginInlineText(p){
 if(activeEditor){ activeEditor.finish(true); }
 const input=document.createElement('textarea');
 input.className='inline-text';
 input.rows=1;
 input.spellcheck=false;
 input.setAttribute('aria-label','Enter annotation text');
 const fs=Number(args.font_size||18);
 input.style.left=p.x+'px';
 input.style.top=p.y+'px';
 input.style.fontSize=fs+'px';
 input.style.fontFamily=args.font==='Arial'?'Arial':'sans-serif';
 input.style.fontWeight=args.bold?'bold':'normal';
 input.style.fontStyle=args.italic?'italic':'normal';
 input.style.textDecoration=args.underline?'underline':'none';
 input.style.color=activeColor;
 input.style.width='2px';
 input.style.height=Math.ceil(fs*1.3)+'px';
 stage.appendChild(input);
 let finished=false;
 function measure(){
   const value=input.value||'';
   const lines=value.split('\\n');
   const longest=lines.reduce((a,b)=>a.length>=b.length?a:b,'');
   input.style.width=Math.max(2,Math.ceil(longest.length*fs*0.68)+4)+'px';
   input.style.height=Math.max(Math.ceil(fs*1.3),Math.ceil(lines.length*fs*1.2))+'px';
 }
 function finish(save){
   if(finished)return;
   finished=true;
   const text=input.value;
   input.remove();
   activeEditor=null;
   if(save&&text.trim()){
     pushHistory();
     const lines=text.split('\\n');
     const longest=lines.reduce((a,b)=>a.length>=b.length?a:b,'');
     const w=Math.max(8,longest.length*fs*0.68);
     const h=Math.max(fs*1.35,lines.length*fs*1.2);
     const o={id:rid(),type:'text',x:p.x,y:p.y,w,h,rotation:0,text,font:args.font||'Arial',font_size:fs,bold:!!args.bold,italic:!!args.italic,underline:!!args.underline,color:activeColor,thickness:thickness()};
     objects.push(o);selectedId=o.id;suppressNextTextClick=false;render();emit();
   } else { render(); }
 }
 activeEditor={input,finish};
 input.addEventListener('pointerdown',e=>e.stopPropagation());
 input.addEventListener('input',measure);
 input.addEventListener('keydown',e=>{
   e.stopPropagation();
   if(e.key==='Escape'){e.preventDefault();finish(false);}
   else if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();finish(true);}
   else if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();finish(true);}
 });
 input.addEventListener('blur',()=>setTimeout(()=>{if(activeEditor&&activeEditor.input===input)finish(true);},0));
 measure();
 requestAnimationFrame(()=>{input.focus({preventScroll:true});input.setSelectionRange(0,0);});
}
svg.addEventListener('pointerdown',e=>{
 const p=point(e);
 if(activeEditor){ activeEditor.finish(true); suppressNextTextClick=true; }
 if(args.tool==='select'){ if(e.target===svg){selectedId=null;render();emit();} return; }
 if(args.tool==='text'){ return; }
 if(['rectangle','circle','line','arrow'].includes(args.tool)){
   if(args.tool==='line'||args.tool==='arrow') draft={id:rid(),type:args.tool,x1:p.x,y1:p.y,x2:p.x,y2:p.y,rotation:0,color:activeColor,thickness:thickness()};
   else draft={id:rid(),type:args.tool,x:p.x,y:p.y,w:1,h:1,rotation:0,color:activeColor,thickness:thickness()};
   pushHistory(); action={kind:'draw',start:p,historySaved:true}; svg.setPointerCapture(e.pointerId); render(); return;
 }
 if(args.tool==='pen'){ pushHistory();draft={id:rid(),type:'pen',points:[p],rotation:0,color:activeColor,thickness:thickness()};action={kind:'pen',historySaved:true};svg.setPointerCapture(e.pointerId);render(); }
});

svg.addEventListener('click',e=>{
 if(args.tool!=='text'||e.target!==svg) return;
 if(suppressNextTextClick){ suppressNextTextClick=false; return; }
 const p=point(e);
 beginInlineText(p);
});
svg.addEventListener('pointermove',e=>{
 if(!action) return; const p=point(e);
 if(action.kind==='draw'&&draft){ if(draft.type==='line'||draft.type==='arrow'){draft.x2=p.x;draft.y2=p.y;} else {draft.x=Math.min(action.start.x,p.x);draft.y=Math.min(action.start.y,p.y);draft.w=Math.abs(p.x-action.start.x);draft.h=Math.abs(p.y-action.start.y);} render(); }
 else if(action.kind==='pen'&&draft){draft.points.push(p);render();}
 else { const o=byId(action.id); if(!o)return; if(!action.historySaved){pushHistory();action.historySaved=true;}
   if(action.kind==='move'){ const dx=p.x-action.start.x,dy=p.y-action.start.y,orig=action.original; if(o.type==='line'||o.type==='arrow'){o.x1=orig.x1+dx;o.y1=orig.y1+dy;o.x2=orig.x2+dx;o.y2=orig.y2+dy;} else if(o.type==='pen'){o.points=orig.points.map(q=>({x:q.x+dx,y:q.y+dy}));} else{o.x=orig.x+dx;o.y=orig.y+dy;} }
   else if(action.kind==='p1'){o.x1=p.x;o.y1=p.y;if(o.type==='arrow'){const ol=Math.max(1,Math.hypot(action.original.x2-action.original.x1,action.original.y2-action.original.y1)),nl=Math.max(1,Math.hypot(o.x2-o.x1,o.y2-o.y1));o.thickness=Math.max(0.5,Number(action.original.thickness||2)*(nl/ol));}} else if(action.kind==='p2'){o.x2=p.x;o.y2=p.y;if(o.type==='arrow'){const ol=Math.max(1,Math.hypot(action.original.x2-action.original.x1,action.original.y2-action.original.y1)),nl=Math.max(1,Math.hypot(o.x2-o.x1,o.y2-o.y1));o.thickness=Math.max(0.5,Number(action.original.thickness||2)*(nl/ol));}}
   else if(action.kind==='rotate'){o.rotation=Math.atan2(p.y-action.center.y,p.x-action.center.x)*180/Math.PI+90;}
   else if(action.kind==='resize'){ const orig=action.original,b=bbox(orig); let x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h; if(action.corner.includes('n'))y1=p.y; if(action.corner.includes('s'))y2=p.y; if(action.corner.includes('w'))x1=p.x; if(action.corner.includes('e'))x2=p.x; const nx=Math.min(x1,x2),ny=Math.min(y1,y2),nw=Math.max(5,Math.abs(x2-x1)),nh=Math.max(5,Math.abs(y2-y1)); if(o.type==='pen'){const sx=nw/b.w,sy=nh/b.h;o.points=orig.points.map(q=>({x:nx+(q.x-b.x)*sx,y:ny+(q.y-b.y)*sy}));} else if(o.type==='text'){const scale=Math.max(0.2,Math.max(nw/b.w,nh/b.h));o.x=nx;o.y=ny;o.font_size=Math.max(4,orig.font_size*scale);o.w=Math.max(20,orig.w*scale);o.h=Math.max(5,orig.h*scale);} else{o.x=nx;o.y=ny;o.w=nw;o.h=nh;} }
   render();
 }
});
svg.addEventListener('pointerup',e=>{ if(!action)return; if((action.kind==='draw'||action.kind==='pen')&&draft){objects.push(draft);selectedId=draft.id;draft=null;} action=null;render();emit(); });
window.addEventListener('keydown',e=>{
 if(activeEditor) return;
 const key=e.key.toLowerCase();
 if((e.key==='Delete'||e.key==='Backspace')&&selectedId){e.preventDefault();pushHistory();objects=objects.filter(o=>o.id!==selectedId);selectedId=null;render();emit();}
 else if(e.key==='Escape'){selectedId=null;render();emit();}
 else if((e.ctrlKey||e.metaKey)&&key==='d'&&selectedId){e.preventDefault();const original=byId(selectedId);if(original){pushHistory();const copy=JSON.parse(JSON.stringify(original));copy.id=rid();if(copy.type==='line'||copy.type==='arrow'){copy.x1+=12;copy.y1+=12;copy.x2+=12;copy.y2+=12;}else if(copy.type==='pen'){copy.points=copy.points.map(p=>({x:p.x+12,y:p.y+12}));}else{copy.x+=12;copy.y+=12;}objects.push(copy);selectedId=copy.id;render();emit();}}
 else if((e.ctrlKey||e.metaKey)&&key==='z'){e.preventDefault();if(undoStack.length){objects=undoStack.pop();selectedId=null;render();emit();}}
});
window.addEventListener('message',event=>{ if(!event.data||event.data.type!=='streamlit:render')return; args=event.data.args||{}; objects=JSON.parse(JSON.stringify(args.annotations||[])); selectedId=args.selected_id||null; activeColor=args.active_color||args.color||'#000000'; undoStack=JSON.parse(JSON.stringify(args.undo_stack||[])); img.src='data:image/png;base64,'+args.preview_b64; img.width=args.width;img.height=args.height;stage.style.width=args.width+'px';stage.style.height=args.height+'px';svg.setAttribute('width',args.width);svg.setAttribute('height',args.height);svg.setAttribute('viewBox',`0 0 ${args.width} ${args.height}`);render(); });
ready();
</script>
</body>
</html>
"""
    with open(index_path, "w", encoding="utf-8") as component_file:
        component_file.write(component_html)
    return components.declare_component("pdf_annotation_component_v5", path=component_dir)


def show_annotation_editor(page_num, page_image, annotations, selected_id, undo_stack, controls):
    component = get_annotation_component()
    return component(
        page_num=page_num,
        width=page_image.width,
        height=page_image.height,
        preview_b64=image_to_base64_png(page_image),
        annotations=annotations,
        selected_id=selected_id,
        undo_stack=undo_stack,
        **controls,
        key=f"annotation_editor_v5_page_{page_num}",
        default=None,
    )


def annotation_color_to_pdf(value):
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))


def rotated_points_for_annotation(annotation):
    """Return display-coordinate points after applying the object's own rotation."""
    kind = annotation["type"]
    angle = float(annotation.get("rotation", 0))
    if kind in {"line", "arrow"}:
        points = [(annotation["x1"], annotation["y1"]), (annotation["x2"], annotation["y2"])]
    elif kind == "pen":
        points = [(point["x"], point["y"]) for point in annotation.get("points", [])]
    else:
        x, y = annotation["x"], annotation["y"]
        w, h = annotation.get("w", 1), annotation.get("h", 1)
        points = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if not points or angle % 360 == 0:
        return points
    xs, ys = zip(*points)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    radians = angle * 3.141592653589793 / 180
    cosine, sine = __import__("math").cos(radians), __import__("math").sin(radians)
    return [
        (cx + (x - cx) * cosine - (y - cy) * sine,
         cy + (x - cx) * sine + (y - cy) * cosine)
        for x, y in points
    ]


def display_point_to_pdf(page, x, y):
    point = fitz.Point(float(x) / ZOOM, float(y) / ZOOM)
    return point * page.derotation_matrix


def insert_rotated_text(page, item, color):
    """Insert crisp vector text matching the editor's displayed position and angle."""
    text = str(item.get("text", ""))
    if not text:
        return

    font_size = max(4, float(item.get("font_size", 18)) / ZOOM)
    bold = bool(item.get("bold"))
    italic = bool(item.get("italic"))
    font_map = {
        (False, False): "helv",
        (True, False): "hebo",
        (False, True): "heit",
        (True, True): "hebi",
    }
    font_name = font_map[(bold, italic)]

    display_angle = float(item.get("rotation", 0))
    radians = display_angle * 3.141592653589793 / 180
    cosine = __import__("math").cos(radians)
    sine = __import__("math").sin(radians)

    # The SVG text baseline starts at y + font_size. Rotate that baseline around
    # the same object centre used by the editor, then map it through page rotation.
    cx = float(item["x"]) + float(item.get("w", 1)) / 2
    cy = float(item["y"]) + float(item.get("h", 1)) / 2
    bx = float(item["x"])
    by = float(item["y"]) + float(item.get("font_size", 18))
    rotated_bx = cx + (bx - cx) * cosine - (by - cy) * sine
    rotated_by = cy + (bx - cx) * sine + (by - cy) * cosine
    baseline = display_point_to_pdf(page, rotated_bx, rotated_by)

    # Determine the equivalent angle in the page's underlying coordinate system.
    direction_display = (rotated_bx + cosine * ZOOM, rotated_by + sine * ZOOM)
    direction_pdf = display_point_to_pdf(page, *direction_display)
    pdf_angle = __import__("math").degrees(
        __import__("math").atan2(direction_pdf.y - baseline.y, direction_pdf.x - baseline.x)
    )

    # Insert with page rotation temporarily cleared. PyMuPDF page content is
    # stored in the page's unrotated coordinate system; leaving /Rotate active
    # while applying an arbitrary morph can make the saved text appear reversed
    # or upside down in some rotated PDFs. The calculated baseline is already in unrotated page coordinates.
    # PyMuPDF morph rotation uses the opposite sign from the SVG editor's
    # clockwise screen-coordinate rotation, so the angle is negated, so insert them with rotation = 0
    # and restore the page rotation immediately afterwards.
    original_page_rotation = int(page.rotation or 0)
    try:
        if original_page_rotation:
            page.set_rotation(0)

        page.insert_text(
            baseline,
            text,
            fontsize=font_size,
            fontname=font_name,
            color=color,
            morph=(baseline, fitz.Matrix(-pdf_angle)),
            overlay=True,
        )
    finally:
        if original_page_rotation:
            page.set_rotation(original_page_rotation)

    if item.get("underline"):
        width_display = max(float(item.get("w", 40)), len(text) * float(item.get("font_size", 18)) * 0.55)
        ux, uy = cosine, sine
        underline_offset = max(2, float(item.get("font_size", 18)) * 0.12)
        px, py = -sine, cosine
        start_display = (rotated_bx + px * underline_offset, rotated_by + py * underline_offset)
        end_display = (
            start_display[0] + ux * width_display,
            start_display[1] + uy * width_display,
        )
        start = display_point_to_pdf(page, *start_display)
        end = display_point_to_pdf(page, *end_display)
        underline = page.add_line_annot(start, end)
        underline.set_colors(stroke=color)
        underline.set_border(width=max(0.5, float(item.get("thickness", 2)) / (ZOOM * 2)))
        underline.update()


def apply_annotations_to_page(page, annotations):
    """Create independent PDF annotation objects from the editor's object model."""
    for item in annotations:
        kind = item.get("type")
        color = annotation_color_to_pdf(item.get("color", "#000000"))
        thickness = max(0.5, float(item.get("thickness", 2)) / ZOOM)
        annot = None

        if kind in {"rectangle", "circle"}:
            display_points = rotated_points_for_annotation(item)
            pdf_points = [display_point_to_pdf(page, x, y) for x, y in display_points]
            if kind == "rectangle" and abs(float(item.get("rotation", 0))) % 90 > 0.01:
                annot = page.add_polygon_annot(pdf_points)
            else:
                rect = fitz.Rect(pdf_points[0], pdf_points[2]).normalize()
                annot = page.add_rect_annot(rect) if kind == "rectangle" else page.add_circle_annot(rect)

        elif kind in {"line", "arrow"}:
            points = rotated_points_for_annotation(item)

            if kind == "line":
                start = display_point_to_pdf(page, *points[0])
                end = display_point_to_pdf(page, *points[1])
                annot = page.add_line_annot(start, end)

            else:
                # Export the entire arrow as one filled polygon. The shaft
                # width, head length, and head width all derive from the same
                # user-selected thickness, so they resize as one object.
                (x1, y1), (x2, y2) = points
                dx, dy = x2 - x1, y2 - y1
                length = max(0.001, (dx * dx + dy * dy) ** 0.5)
                ux, uy = dx / length, dy / length
                display_thickness = max(1.0, float(item.get("thickness", 2)))
                head_length = min(length * 0.65, max(10.0, display_thickness * 5.2))
                head_half_width = max(5.0, display_thickness * 2.8)
                shaft_half_width = display_thickness * 0.75
                base_x = x2 - ux * head_length
                base_y = y2 - uy * head_length
                perp_x, perp_y = -uy, ux

                arrow_display = [
                    (x1 + perp_x * shaft_half_width, y1 + perp_y * shaft_half_width),
                    (base_x + perp_x * shaft_half_width, base_y + perp_y * shaft_half_width),
                    (base_x + perp_x * head_half_width, base_y + perp_y * head_half_width),
                    (x2, y2),
                    (base_x - perp_x * head_half_width, base_y - perp_y * head_half_width),
                    (base_x - perp_x * shaft_half_width, base_y - perp_y * shaft_half_width),
                    (x1 - perp_x * shaft_half_width, y1 - perp_y * shaft_half_width),
                ]
                arrow_pdf = [display_point_to_pdf(page, x, y) for x, y in arrow_display]
                arrow = page.add_polygon_annot(arrow_pdf)
                arrow.set_colors(stroke=color, fill=color)
                arrow.set_border(width=max(0.25, thickness * 0.12))
                arrow.update()
                annot = None

        elif kind == "pen":
            points = rotated_points_for_annotation(item)
            if len(points) >= 2:
                stroke = [tuple(display_point_to_pdf(page, x, y)) for x, y in points]
                annot = page.add_ink_annot([stroke])

        elif kind == "text":
            insert_rotated_text(page, item, color)

        if annot is not None:
            try:
                annot.set_colors(stroke=color)
            except Exception:
                pass
            try:
                annot.set_border(width=thickness)
            except Exception:
                pass
            annot.update()


def render_annotate_tool():
    st.header("Annotate PDF")
    pdf_bytes = upload_pdf_for_mode(MODE_ANNOTATE)
    if pdf_bytes is None:
        st.info(f"Upload a PDF to begin: {MODE_LABELS[MODE_ANNOTATE]}.")
        return

    page_count = get_pdf_page_count(pdf_bytes)
    if page_count is None:
        return
    st.success(f"Loaded PDF with {page_count} page(s).")

    current_page = min(int(st.session_state.get("annotate_page_num", 0)), page_count - 1)
    st.session_state["annotate_page_num"] = current_page

    def previous_annotation_page():
        st.session_state["annotate_page_num"] = max(0, int(st.session_state.get("annotate_page_num", 0)) - 1)

    def next_annotation_page():
        st.session_state["annotate_page_num"] = min(page_count - 1, int(st.session_state.get("annotate_page_num", 0)) + 1)

    nav_left, nav_select, nav_right = st.columns([1, 3, 1])
    with nav_left:
        st.button("← Previous page", on_click=previous_annotation_page, disabled=current_page <= 0, use_container_width=True, key="annotate_previous_page")
    with nav_select:
        page_num = st.selectbox(
            "Jump to page",
            list(range(page_count)),
            format_func=lambda value: f"Page {value + 1} of {page_count}",
            key="annotate_page_num",
        )
    with nav_right:
        st.button("Next page →", on_click=next_annotation_page, disabled=current_page >= page_count - 1, use_container_width=True, key="annotate_next_page")

    page_image = render_pdf_page(pdf_bytes, page_num)
    pages = st.session_state.setdefault("annotate_annotations", {})
    pages.setdefault(page_num, [])
    selected_by_page = st.session_state.setdefault("annotate_selected", {})
    last_nonce = st.session_state.setdefault("annotate_last_nonce", {})
    undo_by_page = st.session_state.setdefault("annotate_undo", {})
    undo_by_page.setdefault(page_num, [])
    active_color = st.session_state.get("annotate_active_color", "#000000")

    st.subheader("Annotation Controls")
    tool_col, font_col, thickness_col = st.columns([2, 1, 2])
    with tool_col:
        tool = st.selectbox("Tool", ["select", "text", "rectangle", "circle", "line", "arrow", "pen"], format_func=lambda value: value.title(), key="annotate_tool")
    with font_col:
        font = st.selectbox("Font", ["Arial", "Sans Serif"], key="annotate_font")
    with thickness_col:
        thickness = st.slider("Line / pen thickness", 1, 12, 2, key="annotate_thickness")

    text_col1, text_col2, text_col3, text_col4 = st.columns(4)
    with text_col1:
        font_size = st.slider("Text size", 8, 72, 18, key="annotate_font_size")
    with text_col2:
        bold = st.checkbox("Bold", key="annotate_bold")
    with text_col3:
        italic = st.checkbox("Italic", key="annotate_italic")
    with text_col4:
        underline = st.checkbox("Underline", key="annotate_underline")

    action_col1, action_col2, _ = st.columns([1, 1, 3])
    with action_col1:
        if st.button("Delete Selected", key="annotate_delete_selected", use_container_width=True):
            selected_id = selected_by_page.get(page_num)
            if selected_id:
                undo_by_page[page_num].append([dict(item) for item in pages[page_num]])
                pages[page_num] = [item for item in pages[page_num] if item.get("id") != selected_id]
                selected_by_page[page_num] = None
                st.rerun()
    with action_col2:
        if st.button("Clear Current Page", key="annotate_clear_page", use_container_width=True):
            undo_by_page[page_num].append([dict(item) for item in pages[page_num]])
            pages[page_num] = []
            selected_by_page[page_num] = None
            st.rerun()

    result = show_annotation_editor(
        page_num,
        page_image,
        pages[page_num],
        selected_by_page.get(page_num),
        undo_by_page[page_num],
        {
            "tool": tool,
            "color": active_color,
            "active_color": active_color,
            "font": font,
            "thickness": thickness,
            "font_size": font_size,
            "bold": bold,
            "italic": italic,
            "underline": underline,
        },
    )
    st.caption(
        "Type text directly at the clicked point. Select one object to move, resize, or rotate it. "
        "Shortcuts: Delete, Esc, Ctrl+D, and Ctrl+Z. Use the page controls above to move through a multipage PDF."
    )

    if result is not None:
        nonce = str(result.get("nonce", ""))
        if nonce and last_nonce.get(page_num) != nonce:
            pages[page_num] = result.get("annotations", [])
            selected_by_page[page_num] = result.get("selected_id")
            undo_by_page[page_num] = result.get("undo_stack", [])
            st.session_state["annotate_active_color"] = result.get("active_color", active_color)
            last_nonce[page_num] = nonce
            st.rerun()

    counts = ", ".join(f"Page {index + 1}: {len(pages.get(index, []))}" for index in range(page_count))
    st.info(f"Annotations saved - {counts}")

    if st.button("Generate Annotated PDF", key="annotate_generate"):
        output = BytesIO()
        document = None
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            for index in range(len(document)):
                apply_annotations_to_page(document[index], pages.get(index, []))
            document.save(output)
            output_bytes = output.getvalue()
        finally:
            if document is not None:
                document.close()
            output.close()
            gc.collect()
        original_name = st.session_state.get("annotate_pdf_name", "document.pdf")
        base_name = os.path.splitext(original_name)[0] or "document"
        st.download_button("Download Annotated PDF", data=output_bytes, file_name=f"{base_name}_annotated.pdf", mime="application/pdf", key="annotate_download")

    del page_image
    gc.collect()


# ============================================================
# Sidebar navigation
# ============================================================

with st.sidebar:
    st.header("Functions")

    if st.button("Redact PDF", use_container_width=True):
        switch_mode(MODE_REDACT)

    if st.button("Convert PDF to XLSX", use_container_width=True):
        switch_mode(MODE_XLSX)

    if st.button("Convert PDF to Plain Text", use_container_width=True):
        switch_mode(MODE_TEXT)

    if st.button("Rotate PDF", use_container_width=True):
        switch_mode(MODE_ROTATE)

    if st.button("Annotate PDF", use_container_width=True):
        switch_mode(MODE_ANNOTATE)

    st.divider()
    active_mode = st.session_state.get("active_mode")
    if active_mode:
        st.caption(f"Selected: {MODE_LABELS[active_mode]}")
        if st.button("Clear PDF and reset function", use_container_width=True):
            reset_current_mode()
    else:
        st.caption("Select a function to begin.")


active_mode = st.session_state.get("active_mode")

if active_mode == MODE_REDACT:
    render_redact_tool()
elif active_mode == MODE_XLSX:
    render_xlsx_tool()
elif active_mode == MODE_TEXT:
    render_text_tool()
elif active_mode == MODE_ROTATE:
    render_rotate_tool()
elif active_mode == MODE_ANNOTATE:
    render_annotate_tool()
else:
    st.info("Choose a function from the sidebar.")

