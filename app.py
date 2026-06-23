import base64
import gc
import html
import re
from io import BytesIO

import fitz
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates


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

/* XLSX column-markup scroll area.
   The ruler and PDF preview live in one independent scroll window.
   The ruler is sticky at the top of that window during vertical scroll,
   and it scrolls horizontally with the PDF so all markers stay aligned. */
.xlsx-scroll-window {
    width: 100%;
    max-width: 100%;
    height: 720px;
    overflow: auto;
    border: 1px solid #cccccc;
    background: #ffffff;
    padding: 0;
    margin: 0;
}

.xlsx-scroll-content {
    position: relative;
    display: block;
    max-width: none !important;
    background: #ffffff;
}

.xlsx-sticky-ruler {
    position: -webkit-sticky;
    position: sticky;
    top: 0;
    z-index: 1000;
    background: #ffffff;
    border-bottom: 1px solid #777777;
}

.xlsx-scroll-content img {
    display: block;
    max-width: none !important;
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
)

uploaded = st.file_uploader(
    "Upload PDF",
    type=["pdf"],
    key=f"pdf_upload_{st.session_state.uploader_key}",
)

if uploaded is None:
    st.stop()

pdf_bytes = uploaded.getvalue()

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


def make_clickable_ruler_html(page_num, width, height, columns):
    """Build a sticky clickable ruler inside the PDF scroll window."""
    ticks = []
    labels = []
    markers = []
    click_zones = []

    for x in range(0, width, 10):
        tick_height = 25 if x % 50 == 0 else 10
        ticks.append(
            f'<div style="position:absolute;left:{x}px;top:0;width:1px;height:{tick_height}px;background:#000;"></div>'
        )

        if x % 50 == 0:
            labels.append(
                f'<div style="position:absolute;left:{x + 3}px;top:30px;font-size:14px;color:#000;">{x}</div>'
            )

    # Five-pixel click zones allow accurate multiple column placement/removal.
    # The links update Streamlit query params, which are handled below.
    for x in range(0, width, 5):
        click_zones.append(
            f'<a href="?col_click={page_num}_{x}" '
            f'title="Set/remove column at {x}" '
            f'style="position:absolute;left:{x}px;top:0;width:5px;height:{height}px;display:block;text-decoration:none;z-index:2000;"></a>'
        )

    for x in sorted([int(v) for v in columns]):
        markers.append(
            f'<div style="position:absolute;left:{x}px;top:0;width:4px;height:{height}px;background:#0000ff;z-index:1500;"></div>'
        )
        markers.append(
            f'<div style="position:absolute;left:{x - 6}px;top:{height - 18}px;width:12px;height:12px;border-radius:50%;background:#0000ff;z-index:1501;"></div>'
        )

    return f"""
    <div class="xlsx-sticky-ruler" style="width:{width}px;height:{height}px;">
        <div style="position:absolute;left:0;top:0;width:{width - 1}px;height:{height - 1}px;border:1px solid #505050;"></div>
        {''.join(ticks)}
        {''.join(labels)}
        {''.join(markers)}
        {''.join(click_zones)}
    </div>
    """


def show_scrollable_clickable_xlsx_markup(page_num, preview_image, columns, ruler_height=70, caption=None):
    """Render ruler and PDF in one independent scroll window.

    The ruler is inside the PDF scroll window, remains visible at the top
    during vertical scrolling, scrolls left/right with the PDF, and accepts
    multiple add/remove column clicks.
    """
    preview_b64 = image_to_base64_png(preview_image)
    content_width = preview_image.width
    content_height = preview_image.height + ruler_height

    ruler_html = make_clickable_ruler_html(
        page_num=page_num,
        width=content_width,
        height=ruler_height,
        columns=columns,
    )

    st.markdown(
        f"""
        <div class="xlsx-scroll-window">
            <div class="xlsx-scroll-content" style="width:{content_width}px;min-height:{content_height}px;">
                {ruler_html}
                <img
                    src="data:image/png;base64,{preview_b64}"
                    width="{preview_image.width}"
                    height="{preview_image.height}"
                    style="display:block;max-width:none;"
                >
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if caption:
        st.caption(caption)

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

        # Handle ruler clicks from inside the independent PDF scroll window.
        # Query params are cleared immediately after processing so the same marker
        # can be clicked again later to remove it.
        query_params = st.query_params
        col_click = query_params.get("col_click")

        if col_click:
            try:
                clicked_page_str, clicked_x_str = str(col_click).split("_", 1)
                clicked_page = int(clicked_page_str)
                clicked_x = int(float(clicked_x_str))

                if clicked_page == page_num:
                    st.session_state.columns[page_num] = toggle_column(
                        current_columns,
                        clicked_x,
                    )
                    st.query_params.clear()
                    st.rerun()
            except Exception:
                st.query_params.clear()

        current_columns = st.session_state.columns.get(page_num, [])

        preview = draw_column_overlay(
            page_image,
            current_columns,
        )

        show_scrollable_clickable_xlsx_markup(
            page_num=page_num,
            preview_image=preview,
            columns=current_columns,
            caption=(
                f"Page {page_num + 1}: click the ruler inside the PDF scroll window to add/remove multiple column boundaries. The ruler stays visible at the top and scrolls left/right with the PDF."
            ),
        )

        if st.button("Generate XLSX"):
            source_doc = None
            output_xlsx = BytesIO()

            try:
                source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

                with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
                    wrote_sheet = False

                    for pnum in range(len(source_doc)):
                        p = source_doc[pnum]
                        raw_columns = st.session_state.columns.get(pnum, [])

                        if not raw_columns:
                            continue

                        pdf_columns = sorted([x / zoom for x in raw_columns])
                        boundaries = [0] + pdf_columns + [p.rect.width]

                        words = p.get_text("words")
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
                            df = pd.DataFrame(table_rows)
                            df = convert_numeric_cells(df)

                            df.to_excel(
                                writer,
                                sheet_name=f"Page_{pnum + 1}",
                                index=False,
                                header=False,
                            )

                            wrote_sheet = True

                    if not wrote_sheet:
                        pd.DataFrame(
                            [["No columns marked or no extractable text found."]]
                        ).to_excel(
                            writer,
                            sheet_name="Result",
                            index=False,
                            header=False,
                        )

                output_xlsx.seek(0)
                output_bytes = output_xlsx.getvalue()

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
            "Scanned image-only PDFs may produce little or no text unless OCR is added later."
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
