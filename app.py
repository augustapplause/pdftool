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


st.set_page_config(page_title="PDF Tools", layout="wide")

st.title("PDF Tools")
st.caption("Version 1.1")

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

APP_VERSION = "1.0"
ZOOM = 1.5

MODE_REDACT = "redact"
MODE_XLSX = "xlsx"
MODE_TEXT = "text"

MODE_LABELS = {
    MODE_REDACT: "Redact PDF",
    MODE_XLSX: "Convert PDF to XLSX",
    MODE_TEXT: "Convert PDF to Plain Text",
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
                        rect = fitz.Rect(
                            rect_data["x"] / ZOOM,
                            rect_data["y"] / ZOOM,
                            (rect_data["x"] + rect_data["w"]) / ZOOM,
                            (rect_data["y"] + rect_data["h"]) / ZOOM,
                        )
                        page.add_redact_annot(rect, fill=(0, 0, 0))
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

        st.info("Click the ruler to add or remove column boundaries.")
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

                    for pnum in range(len(source_doc)):
                        raw_columns = columns.get(pnum, [])
                        if not raw_columns:
                            continue

                        page = source_doc[pnum]
                        pdf_columns = sorted(x / ZOOM for x in raw_columns)
                        boundaries = [0] + pdf_columns + [page.rect.width]
                        rows = group_words_into_rows(page.get_text("words"))
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
            finally:
                if source_doc is not None:
                    source_doc.close()
                output_xlsx.close()
                gc.collect()

            st.download_button(
                "Download XLSX",
                data=output_bytes,
                file_name="extracted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="xlsx_download",
            )

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
else:
    st.info("Choose a function from the sidebar.")

