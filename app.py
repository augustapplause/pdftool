import base64
import gc
import hashlib
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
st.caption("Version 1.23")

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
    """OCR each user-defined column independently with padded crops.

    A small amount of image content is included outside each true column edge
    so characters touching a ruler boundary are not clipped. OCR results are
    retained only when their centre falls inside the actual user-defined
    column, preventing neighbouring-column values from leaking in.
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(render_zoom, render_zoom), alpha=False)
    source_image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    scale_x = pix.width / page.rect.width
    scale_y = pix.height / page.rect.height
    words = []
    crop_padding_px = max(10, int(round(render_zoom * 3)))

    try:
        for column_index in range(len(boundaries) - 1):
            pdf_left = max(0.0, float(boundaries[column_index]))
            pdf_right = min(float(page.rect.width), float(boundaries[column_index + 1]))
            if pdf_right <= pdf_left:
                continue

            true_pixel_left = max(0, int(round(pdf_left * scale_x)))
            true_pixel_right = min(pix.width, int(round(pdf_right * scale_x)))
            pixel_left = max(0, true_pixel_left - crop_padding_px)
            pixel_right = min(pix.width, true_pixel_right + crop_padding_px)
            if pixel_right - pixel_left < 3:
                continue

            crop = source_image.crop((pixel_left, 0, pixel_right, pix.height))
            gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
            enlargement = 1.75 if gray.width < 450 else 1.5
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
                if confidence < 10:
                    continue

                local_left = (float(data["left"][index]) - 16) / enlargement
                local_top = (float(data["top"][index]) - 16) / enlargement
                local_width = float(data["width"][index]) / enlargement
                local_height = float(data["height"][index]) / enlargement

                left_px = pixel_left + max(0.0, local_left)
                right_px = left_px + local_width
                centre_px = (left_px + right_px) / 2
                if not (true_pixel_left <= centre_px < true_pixel_right):
                    continue

                left = left_px / scale_x
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


def build_table_rows_with_geometry(words, boundaries, y_tolerance):
    """Build cell rows while preserving row centres for targeted OCR recovery."""
    grouped_rows = group_words_into_rows(words, y_tolerance=y_tolerance)
    table_rows = []
    row_centres = []

    for row in grouped_rows:
        cells = [""] * (len(boundaries) - 1)
        for word in sorted(row["words"], key=lambda item: item[0]):
            col_index = assign_word_to_column(word, boundaries)
            if col_index is not None:
                cells[col_index] = (cells[col_index] + " " + word[4]).strip()
        if any(cell.strip() for cell in cells):
            table_rows.append(cells)
            row_centres.append(float(row["y"]))

    return table_rows, row_centres


def _looks_numeric_ocr(value):
    text = str(value or "").strip()
    if not text:
        return False
    text = text.replace(" ", "").replace("$", "").replace("€", "").replace("£", "")
    return bool(re.fullmatch(r"\(?-?\d[\d,]*(?:\.\d+)?%?\)?", text))


def _numeric_columns(table_rows):
    """Infer which columns predominantly contain numeric values."""
    if not table_rows:
        return set()
    numeric = set()
    width = max(len(row) for row in table_rows)
    for col_index in range(width):
        values = [row[col_index].strip() for row in table_rows if col_index < len(row) and row[col_index].strip()]
        if len(values) >= 2 and sum(_looks_numeric_ocr(v) for v in values) / len(values) >= 0.55:
            numeric.add(col_index)
    return numeric


def create_ocr_page_cache(page, render_zoom=5.0):
    """Render an OCR page once and cache reusable cell crops."""
    pix = page.get_pixmap(matrix=fitz.Matrix(render_zoom, render_zoom), alpha=False)
    image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    return {
        "pix": pix,
        "image": image,
        "scale_x": pix.width / page.rect.width,
        "scale_y": pix.height / page.rect.height,
        "crops": {},
    }


def get_cached_ocr_crop(cache, box):
    """Return a cached crop for an integer pixel box without recreating it."""
    key = tuple(int(value) for value in box)
    crop = cache["crops"].get(key)
    if crop is None:
        crop = cache["image"].crop(key)
        cache["crops"][key] = crop
    return crop


def close_ocr_page_cache(cache):
    """Release all cached OCR images."""
    if not cache:
        return
    for crop in cache.get("crops", {}).values():
        crop.close()
    cache.get("crops", {}).clear()
    image = cache.get("image")
    if image is not None:
        image.close()


def _blank_cell_is_surrounded(table_rows, row_index, col_index):
    """Select only blanks strongly supported by neighbouring populated cells."""
    row = table_rows[row_index]
    left = col_index > 0 and bool(str(row[col_index - 1]).strip())
    right = col_index + 1 < len(row) and bool(str(row[col_index + 1]).strip())
    above = row_index > 0 and col_index < len(table_rows[row_index - 1]) and bool(
        str(table_rows[row_index - 1][col_index]).strip()
    )
    below = row_index + 1 < len(table_rows) and col_index < len(table_rows[row_index + 1]) and bool(
        str(table_rows[row_index + 1][col_index]).strip()
    )
    return (left and right) or (above and below)


def _ocr_cell_candidate(image, languages, numeric_only, expected_decimal_places=None):
    """Run selective single-cell OCR and stop at the first strong valid result."""
    whitelist = "0123456789.,()%-$" if numeric_only else ""
    configs = ["--oem 3 --psm 7", "--oem 3 --psm 8", "--oem 3 --psm 13"]
    if whitelist:
        configs = [f"{config} -c tessedit_char_whitelist={whitelist}" for config in configs]

    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    enlarged = gray.resize((max(1, gray.width * 4), max(1, gray.height * 4)), Image.Resampling.LANCZOS)
    thresholded = None
    candidates = []

    try:
        # Try the clean enlarged crop first. Only build the thresholded variant
        # when the first pass cannot produce a strong valid result.
        variants = [enlarged]
        for variant_index in range(2):
            if variant_index == 1:
                thresholded = enlarged.point(lambda px: 255 if px > 175 else 0)
                variants = [thresholded]

            variant = variants[0]
            bordered = ImageOps.expand(variant, border=24, fill=255)
            try:
                for config in configs:
                    data = pytesseract.image_to_data(
                        bordered,
                        lang=languages,
                        output_type=Output.DICT,
                        config=config,
                    )
                    tokens = []
                    confidences = []
                    for idx, raw in enumerate(data.get("text", [])):
                        token = str(raw).strip()
                        if not token:
                            continue
                        try:
                            conf = float(data["conf"][idx])
                        except (TypeError, ValueError, KeyError, IndexError):
                            conf = -1
                        if conf >= 0:
                            tokens.append(token)
                            confidences.append(conf)

                    candidate = " ".join(tokens).strip()
                    if not candidate:
                        continue
                    if numeric_only:
                        candidate = candidate.replace(" ", "")
                        if not _looks_numeric_ocr(candidate):
                            continue

                    score = sum(confidences) / len(confidences) if confidences else 0
                    exact_decimal = False
                    if numeric_only and re.search(r"\d", candidate):
                        score += 8
                    if numeric_only and expected_decimal_places is not None:
                        exact_decimal = bool(re.fullmatch(
                            rf"\(?-?\d[\d,]*\.\d{{{expected_decimal_places}}}%?\)?",
                            candidate,
                        ))
                        if exact_decimal:
                            score += 30
                        elif "." not in candidate and re.fullmatch(r"\d{5,}", candidate):
                            score -= 10

                    # Stop immediately when OCR is already trustworthy. This
                    # avoids running every preprocessing/configuration variant.
                    if (exact_decimal and score >= 75) or (
                        expected_decimal_places is None and score >= 82
                    ):
                        return candidate
                    candidates.append((score, candidate))
            finally:
                bordered.close()
    finally:
        if thresholded is not None:
            thresholded.close()
        enlarged.close()
        gray.close()

    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return candidates[0][1]

def recover_blank_ocr_cells(
    page,
    boundaries,
    table_rows,
    row_centres,
    render_zoom=5.0,
    languages="eng+fra",
    ocr_cache=None,
):
    """Re-OCR suspicious blank cells using tight row-and-column crops.

    Rows are derived from values already detected across the table. A blank
    cell in an otherwise populated row is cropped independently and tested
    with multiple preprocessing and page-segmentation modes.
    """
    if not table_rows or not row_centres:
        return table_rows, 0

    owns_cache = ocr_cache is None
    cache = ocr_cache or create_ocr_page_cache(page, render_zoom=render_zoom)
    pix = cache["pix"]
    scale_x = cache["scale_x"]
    scale_y = cache["scale_y"]
    numeric_columns = _numeric_columns(table_rows)
    recovered = 0

    try:
        for row_index, row in enumerate(table_rows):
            populated = sum(bool(str(cell).strip()) for cell in row)
            if populated < 3:
                continue

            centre = row_centres[row_index]
            previous_centre = row_centres[row_index - 1] if row_index > 0 else None
            next_centre = row_centres[row_index + 1] if row_index + 1 < len(row_centres) else None
            if previous_centre is None and next_centre is None:
                half_height = 8.0
            elif previous_centre is None:
                half_height = max(5.0, (next_centre - centre) * 0.48)
            elif next_centre is None:
                half_height = max(5.0, (centre - previous_centre) * 0.48)
            else:
                half_height = max(5.0, min(centre - previous_centre, next_centre - centre) * 0.48)

            top_pdf = max(0.0, centre - half_height)
            bottom_pdf = min(float(page.rect.height), centre + half_height)

            for col_index, value in enumerate(row):
                if str(value).strip():
                    continue
                if not _blank_cell_is_surrounded(table_rows, row_index, col_index):
                    continue
                # The first column is usually descriptive text. Recover it only
                # when the column is inferred as numeric, reducing false text.
                if col_index == 0 and col_index not in numeric_columns:
                    continue

                left_pdf = max(0.0, float(boundaries[col_index]) - 2.0)
                right_pdf = min(float(page.rect.width), float(boundaries[col_index + 1]) + 2.0)
                left_px = max(0, int(round(left_pdf * scale_x)))
                right_px = min(pix.width, int(round(right_pdf * scale_x)))
                top_px = max(0, int(round(top_pdf * scale_y)))
                bottom_px = min(pix.height, int(round(bottom_pdf * scale_y)))
                if right_px - left_px < 4 or bottom_px - top_px < 4:
                    continue

                crop = get_cached_ocr_crop(cache, (left_px, top_px, right_px, bottom_px))
                candidate = _ocr_cell_candidate(
                    crop,
                    languages=languages,
                    numeric_only=col_index in numeric_columns,
                )

                if candidate:
                    row[col_index] = candidate
                    recovered += 1
    finally:
        if owns_cache:
            close_ocr_page_cache(cache)

    return table_rows, recovered


def normalize_ocr_numeric_spacing(value):
    """Remove OCR-inserted whitespace only when the compact result is numeric."""
    if value is None or isinstance(value, (int, float)):
        return value

    text = str(value).strip()
    if not text or re.search(r"[A-Za-z]", text):
        return value

    compact = re.sub(r"\s+", "", text)
    if _looks_numeric_ocr(compact):
        return compact
    return value


def normalize_ocr_table_values(table_rows):
    for row in table_rows:
        for col_index, value in enumerate(row):
            row[col_index] = normalize_ocr_numeric_spacing(value)
    return table_rows


def infer_column_decimal_places(table_rows):
    """Infer the dominant decimal precision for numeric-looking columns."""
    result = {}
    if not table_rows:
        return result

    width = max(len(row) for row in table_rows)
    for col_index in range(width):
        counts = {}
        decimal_values = 0
        for row in table_rows:
            if col_index >= len(row):
                continue
            value = normalize_ocr_numeric_spacing(row[col_index])
            text = str(value or "").strip().replace(",", "")
            match = re.fullmatch(r"\(?-?\d+\.(\d+)%?\)?", text)
            if not match:
                continue
            places = len(match.group(1))
            counts[places] = counts.get(places, 0) + 1
            decimal_values += 1

        if decimal_values >= 4:
            places, count = max(counts.items(), key=lambda item: item[1])
            if count / decimal_values >= 0.70:
                result[col_index] = places

    return result


def _column_numeric_magnitudes(table_rows, col_index):
    """Return positive numeric magnitudes already carrying explicit decimals."""
    values = []
    for row in table_rows:
        if col_index >= len(row):
            continue
        text = str(normalize_ocr_numeric_spacing(row[col_index]) or "").strip()
        text = text.replace(",", "").replace("%", "")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        try:
            if "." in text:
                values.append(abs(float(text)))
        except ValueError:
            continue
    return [value for value in values if value > 0]


def _repair_missing_decimal_by_column_pattern(raw_value, decimal_places, reference_values):
    """Insert a missing decimal only when column magnitudes strongly support it."""
    text = str(raw_value or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+", text) or len(text) <= decimal_places:
        return ""

    proposed_text = text[:-decimal_places] + "." + text[-decimal_places:]
    try:
        original_value = float(text)
        proposed_value = float(proposed_text)
    except ValueError:
        return ""

    if not reference_values:
        return ""

    ordered = sorted(reference_values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median_value = ordered[midpoint]
    else:
        median_value = (ordered[midpoint - 1] + ordered[midpoint]) / 2

    maximum = max(ordered)
    minimum = min(ordered)

    # The unpunctuated value must be a clear magnitude outlier, while the
    # reconstructed value must sit within a generous range of the column.
    original_is_outlier = original_value > max(maximum * 5, median_value * 20)
    proposed_is_plausible = (minimum / 100) <= proposed_value <= max(maximum * 5, median_value * 20)

    return proposed_text if original_is_outlier and proposed_is_plausible else ""


def recover_suspicious_numeric_cells(
    page,
    boundaries,
    table_rows,
    row_centres,
    render_zoom=5.0,
    languages="eng+fra",
    ocr_cache=None,
):
    """Re-OCR populated numeric cells whose decimal punctuation is suspicious."""
    expected_places = infer_column_decimal_places(table_rows)
    if not expected_places or not row_centres:
        return table_rows, 0

    column_reference_values = {
        col_index: _column_numeric_magnitudes(table_rows, col_index)
        for col_index in expected_places
    }

    owns_cache = ocr_cache is None
    cache = ocr_cache or create_ocr_page_cache(page, render_zoom=render_zoom)
    pix = cache["pix"]
    scale_x = cache["scale_x"]
    scale_y = cache["scale_y"]
    corrected = 0

    try:
        for row_index, row in enumerate(table_rows):
            centre = row_centres[row_index]
            previous_centre = row_centres[row_index - 1] if row_index > 0 else None
            next_centre = row_centres[row_index + 1] if row_index + 1 < len(row_centres) else None
            if previous_centre is None and next_centre is None:
                half_height = 8.0
            elif previous_centre is None:
                half_height = max(5.0, (next_centre - centre) * 0.48)
            elif next_centre is None:
                half_height = max(5.0, (centre - previous_centre) * 0.48)
            else:
                half_height = max(5.0, min(centre - previous_centre, next_centre - centre) * 0.48)

            top_pdf = max(0.0, centre - half_height)
            bottom_pdf = min(float(page.rect.height), centre + half_height)

            for col_index, places in expected_places.items():
                if col_index >= len(row):
                    continue
                original = normalize_ocr_numeric_spacing(row[col_index])
                text = str(original or "").strip().replace(",", "")
                if not re.fullmatch(r"\d{5,}", text):
                    continue

                left_pdf = max(0.0, float(boundaries[col_index]) - 2.0)
                right_pdf = min(float(page.rect.width), float(boundaries[col_index + 1]) + 2.0)
                crop_box = (
                    max(0, int(round(left_pdf * scale_x))),
                    max(0, int(round(top_pdf * scale_y))),
                    min(pix.width, int(round(right_pdf * scale_x))),
                    min(pix.height, int(round(bottom_pdf * scale_y))),
                )
                crop = get_cached_ocr_crop(cache, crop_box)
                candidate = _ocr_cell_candidate(
                    crop,
                    languages=languages,
                    numeric_only=True,
                    expected_decimal_places=places,
                )

                candidate = normalize_ocr_numeric_spacing(candidate)
                candidate_text = str(candidate or "").strip().replace(",", "")
                if re.fullmatch(rf"\(?-?\d+\.\d{{{places}}}%?\)?", candidate_text):
                    row[col_index] = candidate
                    corrected += 1
                    continue

                # Tesseract often recognizes every digit correctly but omits a
                # faint decimal point. When both the re-OCR result and original
                # value are unpunctuated, use the column's established decimal
                # format only if the raw integer is an obvious magnitude outlier.
                fallback_source = candidate_text if re.fullmatch(r"\d+", candidate_text) else text
                repaired = _repair_missing_decimal_by_column_pattern(
                    fallback_source,
                    decimal_places=places,
                    reference_values=column_reference_values.get(col_index, []),
                )
                if repaired:
                    row[col_index] = repaired
                    corrected += 1
    finally:
        if owns_cache:
            close_ocr_page_cache(cache)

    return table_rows, corrected


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

APP_VERSION = "1.23"
ZOOM = 1.5

MODE_REDACT = "redact"
MODE_XLSX = "xlsx"
MODE_TEXT = "text"
MODE_ROTATE = "rotate"
MODE_ANNOTATE = "annotate"
MODE_SPLIT = "split"

MODE_LABELS = {
    MODE_REDACT: "Redact PDF",
    MODE_XLSX: "Convert PDF to XLSX",
    MODE_TEXT: "Convert PDF to Plain Text",
    MODE_ROTATE: "Rotate PDF",
    MODE_ANNOTATE: "Annotate PDF",
    MODE_SPLIT: "Split PDF",
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
                    st.session_state["xlsx_recovered_cells"] = {}
                    st.session_state["xlsx_corrected_cells"] = {}

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

                        table_rows, row_centres = build_table_rows_with_geometry(
                            page_words,
                            boundaries,
                            y_tolerance=6 if used_ocr else 4,
                        )

                        if used_ocr and table_rows:
                            ocr_cache = create_ocr_page_cache(page, render_zoom=5.0)
                            try:
                                table_rows, recovered_cells = recover_blank_ocr_cells(
                                    page,
                                    boundaries,
                                    table_rows,
                                    row_centres,
                                    ocr_cache=ocr_cache,
                                )
                                table_rows = normalize_ocr_table_values(table_rows)
                                table_rows, corrected_cells = recover_suspicious_numeric_cells(
                                    page,
                                    boundaries,
                                    table_rows,
                                    row_centres,
                                    ocr_cache=ocr_cache,
                                )
                            finally:
                                close_ocr_page_cache(ocr_cache)
                            if recovered_cells:
                                st.session_state.setdefault("xlsx_recovered_cells", {})[pnum] = recovered_cells
                            if corrected_cells:
                                st.session_state.setdefault("xlsx_corrected_cells", {})[pnum] = corrected_cells

                        if table_rows:
                            table_rows = normalize_ocr_table_values(table_rows)
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
            recovered_by_page = st.session_state.get("xlsx_recovered_cells", {})
            if ocr_pages:
                page_list = ", ".join(str(number) for number in ocr_pages)
                st.info(f"Column-aware OCR was automatically used on page(s): {page_list}.")
                recovered_total = sum(recovered_by_page.values())
                if recovered_total:
                    st.success(
                        f"Targeted second-pass OCR recovered {recovered_total} previously blank cell(s)."
                    )
                corrected_total = sum(st.session_state.get("xlsx_corrected_cells", {}).values())
                if corrected_total:
                    st.success(
                        f"Numeric OCR validation corrected {corrected_total} suspicious cell(s)."
                    )
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
    """Create the isolated SVG annotation editor and compact toolbar."""
    component_dir = os.path.join(tempfile.gettempdir(), "pdf_annotation_component_v8")
    os.makedirs(component_dir, exist_ok=True)
    index_path = os.path.join(component_dir, "index.html")

    component_html = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
:root { --blue:#1677ff; --border:#b8b8b8; --panel:#f5f5f5; }
html,body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; font-family:Arial,sans-serif; background:#fff; }
#toolbar { min-height:84px; display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:6px 8px; box-sizing:border-box; background:var(--panel); border:1px solid var(--border); border-bottom:0; }
.group { display:flex; align-items:center; gap:4px; padding-right:7px; margin-right:2px; border-right:1px solid #d0d0d0; min-height:32px; }
.group:last-child { border-right:0; }
button,select,input { font:13px Arial,sans-serif; }
.tool,.small-btn,.style-btn { min-height:30px; border:1px solid #aaa; border-radius:4px; background:#fff; cursor:pointer; padding:4px 8px; }
.tool.active,.style-btn.active { background:#dcecff; border-color:var(--blue); box-shadow:inset 0 0 0 1px var(--blue); }
.style-btn { width:31px; padding:3px; font-weight:700; }
#italic-btn { font-style:italic; } #underline-btn { text-decoration:underline; }
label.compact { display:flex; align-items:center; gap:4px; white-space:nowrap; font-size:12px; }
select { height:30px; border:1px solid #aaa; border-radius:4px; background:#fff; padding:2px 5px; }
input[type=range] { width:92px; }
.value { min-width:20px; font-size:12px; text-align:center; }
.swatch { width:23px; height:23px; border-radius:4px; border:2px solid #fff; box-shadow:0 0 0 1px #777; cursor:pointer; padding:0; }
.swatch.active { box-shadow:0 0 0 3px var(--blue); }
#custom-btn { display:flex; align-items:center; gap:5px; }
#current-colour { width:18px; height:18px; border:1px solid #555; border-radius:3px; display:inline-block; }
#scroll { width:100%; height:642px; overflow:auto; border:1px solid var(--border); box-sizing:border-box; background:#ddd; }
#stage { position:relative; background:#fff; }
#page { display:block; max-width:none; user-select:none; -webkit-user-drag:none; }
#overlay { position:absolute; left:0; top:0; touch-action:none; z-index:2; }
.inline-text { position:absolute; z-index:5; min-width:2px; min-height:1.2em; border:0; outline:none; padding:0; margin:0; overflow:hidden; resize:none; background:transparent; line-height:1.15; white-space:pre; caret-color:var(--blue); box-shadow:none; }
.object { cursor:pointer; }
.selected-outline { fill:none; stroke:var(--blue); stroke-width:1.5; stroke-dasharray:6 4; pointer-events:none; }
.handle { fill:#fff; stroke:var(--blue); stroke-width:2; cursor:nwse-resize; }
.rotate-line { stroke:var(--blue); stroke-width:1.5; pointer-events:none; }
.rotate-handle { fill:#fff; stroke:var(--blue); stroke-width:2; cursor:grab; }
.endpoint { fill:#fff; stroke:var(--blue); stroke-width:2; cursor:crosshair; }
#picker-backdrop { display:none; position:fixed; inset:0; z-index:50; background:rgba(0,0,0,.18); align-items:flex-start; justify-content:center; padding-top:90px; box-sizing:border-box; }
#picker { width:390px; background:#fff; border:1px solid #777; border-radius:7px; box-shadow:0 8px 30px rgba(0,0,0,.35); padding:12px; }
#picker h3 { margin:0 0 10px; font-size:16px; }
#picker-main { display:flex; gap:10px; }
#sv { position:relative; width:310px; height:220px; cursor:crosshair; touch-action:none; background:red; }
#sv::before { content:""; position:absolute; inset:0; background:linear-gradient(to right,#fff,rgba(255,255,255,0)); }
#sv::after { content:""; position:absolute; inset:0; background:linear-gradient(to top,#000,rgba(0,0,0,0)); }
#sv-marker { position:absolute; width:12px; height:12px; border:2px solid #fff; border-radius:50%; box-shadow:0 0 0 1px #000; transform:translate(-7px,-7px); pointer-events:none; z-index:2; }
#hue { position:relative; width:28px; height:220px; cursor:ns-resize; touch-action:none; background:linear-gradient(to bottom,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00); }
#hue-marker { position:absolute; left:-3px; width:34px; height:4px; background:#fff; border:1px solid #000; transform:translateY(-2px); pointer-events:none; }
#picker-info { display:flex; align-items:center; gap:9px; margin-top:10px; }
#preview { width:64px; height:38px; border:1px solid #555; border-radius:4px; }
#hex { width:92px; height:28px; border:1px solid #aaa; border-radius:4px; padding:2px 6px; text-transform:uppercase; }
#picker-actions { margin-left:auto; display:flex; gap:7px; }
</style>
</head>
<body>
<div id="toolbar"></div>
<div id="scroll"><div id="stage"><img id="page"><svg id="overlay"></svg></div></div>
<div id="picker-backdrop"><div id="picker">
  <h3>Choose custom colour</h3>
  <div id="picker-main"><div id="sv"><div id="sv-marker"></div></div><div id="hue"><div id="hue-marker"></div></div></div>
  <div id="picker-info"><div id="preview"></div><input id="hex" maxlength="7"><div id="picker-actions"><button id="picker-cancel" class="small-btn">Cancel</button><button id="picker-apply" class="small-btn">Apply custom colour</button></div></div>
</div></div>
<script>
const NS='http://www.w3.org/2000/svg';
const COLORS=['#000000','#ffffff','#d00000','#e56b00','#f2c500','#008a2e','#00a7b5','#0057d9','#7a32a8','#7a4a20','#777777'];
const TOOLS=['select','text','image','rectangle','circle','line','arrow','pen'];
let args={},objects=[],selectedId=null,action=null,draft=null,undoStack=[],activeEditor=null,suppressNextTextClick=false,imageAssets={};
let settings={tool:'select',color:'#000000',font:'Arial',thickness:2,font_size:18,bold:false,italic:false,underline:false};
let picker={h:0,s:1,v:1,original:'#000000'};
const toolbar=document.getElementById('toolbar'),stage=document.getElementById('stage'),img=document.getElementById('page'),svg=document.getElementById('overlay');
const backdrop=document.getElementById('picker-backdrop'),sv=document.getElementById('sv'),hue=document.getElementById('hue'),svMarker=document.getElementById('sv-marker'),hueMarker=document.getElementById('hue-marker'),preview=document.getElementById('preview'),hexInput=document.getElementById('hex');
function msg(type,data){window.parent.postMessage(Object.assign({isStreamlitMessage:true,type},data),'*');}
function ready(){msg('streamlit:componentReady',{apiVersion:1});msg('streamlit:setFrameHeight',{height:735});}
function emit(extra={}){msg('streamlit:setComponentValue',{value:Object.assign({annotations:objects,selected_id:selectedId,undo_stack:undoStack,settings:settings,active_color:settings.color,nonce:Date.now()+'_'+Math.random().toString(36).slice(2)},extra),dataType:'json'});}
function snapshot(){return JSON.parse(JSON.stringify(objects));} function pushHistory(){undoStack.push(snapshot());if(undoStack.length>30)undoStack.shift();}
function el(name,attrs={}){const n=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));return n;}
function byId(id){return objects.find(o=>o.id===id);} function rid(){return'a_'+Date.now()+'_'+Math.random().toString(36).slice(2);}
function point(e){const r=svg.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};} function thickness(){return Number(settings.thickness||2);}
function bbox(o){if(o.type==='line'||o.type==='arrow')return{x:Math.min(o.x1,o.x2),y:Math.min(o.y1,o.y2),w:Math.max(1,Math.abs(o.x2-o.x1)),h:Math.max(1,Math.abs(o.y2-o.y1))};if(o.type==='pen'){const xs=o.points.map(p=>p.x),ys=o.points.map(p=>p.y);return{x:Math.min(...xs),y:Math.min(...ys),w:Math.max(1,Math.max(...xs)-Math.min(...xs)),h:Math.max(1,Math.max(...ys)-Math.min(...ys))};}return{x:o.x,y:o.y,w:Math.max(1,o.w),h:Math.max(1,o.h)};}
function center(o){const b=bbox(o);return{x:b.x+b.w/2,y:b.y+b.h/2};} function transform(o){const c=center(o);return`rotate(${Number(o.rotation||0)} ${c.x} ${c.y})`;}
function arrowPolygon(o){const dx=o.x2-o.x1,dy=o.y2-o.y1,len=Math.max(.001,Math.hypot(dx,dy)),ux=dx/len,uy=dy/len,t=Math.max(1,Number(o.thickness||2)),hl=Math.min(len*.65,Math.max(10,t*5.2)),hh=Math.max(5,t*2.8),sh=t*.75,px=-uy,py=ux,bx=o.x2-ux*hl,by=o.y2-uy*hl;return[`${o.x1+px*sh},${o.y1+py*sh}`,`${bx+px*sh},${by+py*sh}`,`${bx+px*hh},${by+py*hh}`,`${o.x2},${o.y2}`,`${bx-px*hh},${by-py*hh}`,`${bx-px*sh},${by-py*sh}`,`${o.x1-px*sh},${o.y1-py*sh}`].join(' ');}
function objectNode(o){const g=el('g',{class:'object','data-id':o.id,transform:transform(o)}),common={stroke:o.color||'#000','stroke-width':o.thickness||2,fill:'none','vector-effect':'non-scaling-stroke'};let n;if(o.type==='rectangle')n=el('rect',Object.assign({x:o.x,y:o.y,width:o.w,height:o.h},common));else if(o.type==='circle')n=el('ellipse',Object.assign({cx:o.x+o.w/2,cy:o.y+o.h/2,rx:Math.abs(o.w/2),ry:Math.abs(o.h/2)},common));else if(o.type==='line')n=el('line',Object.assign({x1:o.x1,y1:o.y1,x2:o.x2,y2:o.y2,'stroke-linecap':'round'},common));else if(o.type==='arrow')n=el('polygon',{points:arrowPolygon(o),fill:o.color||'#000',stroke:o.color||'#000','stroke-width':Math.max(.5,Number(o.thickness||2)*.12),'stroke-linejoin':'round'});else if(o.type==='pen')n=el('polyline',Object.assign({points:o.points.map(p=>`${p.x},${p.y}`).join(' '),'stroke-linecap':'round','stroke-linejoin':'round'},common));else if(o.type==='image'){const asset=imageAssets[o.asset_id];if(asset)n=el('image',{x:o.x,y:o.y,width:o.w,height:o.h,href:asset.data_url,preserveAspectRatio:'none'});}else if(o.type==='text'){n=el('text',{x:o.x,y:o.y+o.font_size,fill:o.color||'#000','font-family':o.font==='Arial'?'Arial':'sans-serif','font-size':o.font_size,'font-weight':o.bold?'bold':'normal','font-style':o.italic?'italic':'normal','text-decoration':o.underline?'underline':'none'});String(o.text||'').split('\n').forEach((line,i)=>{const t=el('tspan',{x:o.x,dy:i===0?0:o.font_size*1.2});t.textContent=line;n.appendChild(t);});}if(n)g.appendChild(n);g.addEventListener('pointerdown',e=>{if(settings.tool!=='select')return;e.stopPropagation();selectedId=o.id;action={kind:'move',id:o.id,start:point(e),original:JSON.parse(JSON.stringify(o)),historySaved:false};svg.setPointerCapture(e.pointerId);render();});return g;}
function selectionNodes(o){const out=[],b=bbox(o),c=center(o),pad=5;out.push(el('rect',{x:b.x-pad,y:b.y-pad,width:b.w+2*pad,height:b.h+2*pad,class:'selected-outline',transform:transform(o)}));if(o.type==='line'){[['p1',o.x1,o.y1],['p2',o.x2,o.y2]].forEach(([kind,x,y])=>{const h=el('circle',{cx:x,cy:y,r:6,class:'endpoint'});h.addEventListener('pointerdown',e=>{e.stopPropagation();action={kind,id:o.id,original:JSON.parse(JSON.stringify(o)),historySaved:false};svg.setPointerCapture(e.pointerId)});out.push(h);});}else{[['nw',b.x,b.y],['ne',b.x+b.w,b.y],['sw',b.x,b.y+b.h],['se',b.x+b.w,b.y+b.h]].forEach(([corner,x,y])=>{const h=el('rect',{x:x-5,y:y-5,width:10,height:10,class:'handle'});h.addEventListener('pointerdown',e=>{e.stopPropagation();action={kind:'resize',corner,id:o.id,original:JSON.parse(JSON.stringify(o)),start:point(e),historySaved:false};svg.setPointerCapture(e.pointerId)});out.push(h);});}const ry=b.y-28;out.push(el('line',{x1:c.x,y1:b.y-pad,x2:c.x,y2:ry+6,class:'rotate-line'}));const rh=el('circle',{cx:c.x,cy:ry,r:7,class:'rotate-handle'});rh.addEventListener('pointerdown',e=>{e.stopPropagation();action={kind:'rotate',id:o.id,center:c,original:JSON.parse(JSON.stringify(o)),historySaved:false};svg.setPointerCapture(e.pointerId)});out.push(rh);return out;}
function applySettingsToSelected(){const o=byId(selectedId);if(!o)return;pushHistory();o.color=settings.color;if(o.type==='text'){o.font=settings.font;o.bold=settings.bold;o.italic=settings.italic;o.underline=settings.underline;}else o.thickness=settings.thickness;render();emit();}
function control(tag,props={}){const n=document.createElement(tag);Object.assign(n,props);return n;}
function group(){const g=control('div');g.className='group';toolbar.appendChild(g);return g;}
function renderToolbar(){toolbar.innerHTML='';let g=group();const prev=control('button',{textContent:'◀',className:'small-btn',title:'Previous page'});prev.disabled=Number(args.page_num)<=0;prev.onclick=()=>emit({requested_page:Number(args.page_num)-1});g.appendChild(prev);const page=control('select');for(let i=0;i<Number(args.page_count||1);i++){const op=control('option',{textContent:`Page ${i+1} of ${args.page_count}`,value:String(i)});if(i===Number(args.page_num))op.selected=true;page.appendChild(op);}page.onchange=()=>emit({requested_page:Number(page.value)});g.appendChild(page);const next=control('button',{textContent:'▶',className:'small-btn',title:'Next page'});next.disabled=Number(args.page_num)>=Number(args.page_count)-1;next.onclick=()=>emit({requested_page:Number(args.page_num)+1});g.appendChild(next);
 g=group();TOOLS.forEach(t=>{const b=control('button',{textContent:t[0].toUpperCase()+t.slice(1),className:'tool'+(settings.tool===t?' active':'')});if(t==='image'&&!args.active_image_id){b.disabled=true;b.title='Upload an image above the editor first';}b.onclick=()=>{if(activeEditor)activeEditor.finish(true);settings.tool=t;renderToolbar();};g.appendChild(b);});
 g=group();COLORS.forEach(c=>{const b=control('button',{className:'swatch'+(c.toLowerCase()===settings.color.toLowerCase()?' active':'')});b.style.background=c;b.title=c;b.onclick=()=>{settings.color=c;applySettingsToSelected();renderToolbar();};g.appendChild(b);});const cb=control('button',{id:'custom-btn',className:'small-btn'});const chip=control('span',{id:'current-colour'});chip.style.background=settings.color;cb.append(chip,document.createTextNode('Custom…'));cb.onclick=openPicker;g.appendChild(cb);
 g=group();const tl=control('label',{className:'compact'});tl.append(document.createTextNode('Thickness'));const tr=control('input',{type:'range',min:'1',max:'18',value:String(settings.thickness)});const tv=control('span',{className:'value',textContent:String(settings.thickness)});tr.oninput=()=>{settings.thickness=Number(tr.value);tv.textContent=tr.value;const o=byId(selectedId);if(o&&o.type!=='text'){o.thickness=settings.thickness;render();}};tr.onchange=()=>{if(byId(selectedId))emit();};tl.append(tr,tv);g.appendChild(tl);
 g=group();const font=control('select');['Arial','Sans Serif'].forEach(v=>{const o=control('option',{value:v,textContent:v});if(v===settings.font)o.selected=true;font.appendChild(o);});font.onchange=()=>{settings.font=font.value;applySettingsToSelected();};g.appendChild(font);const sl=control('label',{className:'compact'});sl.append(document.createTextNode('Size'));const sr=control('input',{type:'range',min:'8',max:'96',value:String(settings.font_size)});const svv=control('span',{className:'value',textContent:String(settings.font_size)});sr.oninput=()=>{settings.font_size=Number(sr.value);svv.textContent=sr.value;};sl.append(sr,svv);g.appendChild(sl);[['bold','B','bold-btn'],['italic','I','italic-btn'],['underline','U','underline-btn']].forEach(([k,label,id])=>{const b=control('button',{id,className:'style-btn'+(settings[k]?' active':''),textContent:label});b.onclick=()=>{settings[k]=!settings[k];applySettingsToSelected();renderToolbar();};g.appendChild(b);});
 g=group();const del=control('button',{className:'small-btn',textContent:'Delete'});del.onclick=deleteSelected;const clear=control('button',{className:'small-btn',textContent:'Clear page'});clear.onclick=()=>{if(objects.length){pushHistory();objects=[];selectedId=null;render();emit();}};g.append(del,clear);
}
function setSelectedColor(c){settings.color=c;const o=byId(selectedId);if(o){pushHistory();o.color=c;}render();renderToolbar();emit();}
function rgbToHsv(hex){const r=parseInt(hex.slice(1,3),16)/255,g=parseInt(hex.slice(3,5),16)/255,b=parseInt(hex.slice(5,7),16)/255,max=Math.max(r,g,b),min=Math.min(r,g,b),d=max-min;let h=0;if(d){if(max===r)h=60*(((g-b)/d)%6);else if(max===g)h=60*((b-r)/d+2);else h=60*((r-g)/d+4);}if(h<0)h+=360;return{h,s:max?d/max:0,v:max};}
function hsvToHex(h,s,v){const c=v*s,x=c*(1-Math.abs((h/60)%2-1)),m=v-c;let r=0,g=0,b=0;if(h<60){r=c;g=x}else if(h<120){r=x;g=c}else if(h<180){g=c;b=x}else if(h<240){g=x;b=c}else if(h<300){r=x;b=c}else{r=c;b=x}const hx=n=>Math.round((n+m)*255).toString(16).padStart(2,'0');return'#'+hx(r)+hx(g)+hx(b);}
function updatePicker(){const c=hsvToHex(picker.h,picker.s,picker.v);sv.style.backgroundColor=hsvToHex(picker.h,1,1);svMarker.style.left=(picker.s*100)+'%';svMarker.style.top=((1-picker.v)*100)+'%';hueMarker.style.top=(picker.h/360*100)+'%';preview.style.background=c;hexInput.value=c.toUpperCase();}
function openPicker(){picker.original=settings.color;Object.assign(picker,rgbToHsv(settings.color));updatePicker();backdrop.style.display='flex';}
function closePicker(){backdrop.style.display='none';}
function dragArea(node,fn){let down=false;node.addEventListener('pointerdown',e=>{e.preventDefault();down=true;node.setPointerCapture(e.pointerId);fn(e);});node.addEventListener('pointermove',e=>{if(down)fn(e);});node.addEventListener('pointerup',e=>{if(down){fn(e);down=false;}});node.addEventListener('pointercancel',()=>down=false);}
dragArea(sv,e=>{const r=sv.getBoundingClientRect();picker.s=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));picker.v=1-Math.max(0,Math.min(1,(e.clientY-r.top)/r.height));updatePicker();});
dragArea(hue,e=>{const r=hue.getBoundingClientRect();picker.h=Math.max(0,Math.min(359.999,(e.clientY-r.top)/r.height*360));updatePicker();});
hexInput.addEventListener('change',()=>{let v=hexInput.value.trim();if(!v.startsWith('#'))v='#'+v;if(/^#[0-9a-fA-F]{6}$/.test(v)){Object.assign(picker,rgbToHsv(v));updatePicker();}});
document.getElementById('picker-cancel').onclick=closePicker;document.getElementById('picker-apply').onclick=()=>{setSelectedColor(hsvToHex(picker.h,picker.s,picker.v));closePicker();};backdrop.addEventListener('pointerdown',e=>{if(e.target===backdrop)closePicker();});
function render(){svg.innerHTML='';objects.forEach(o=>svg.appendChild(objectNode(o)));if(draft)svg.appendChild(objectNode(draft));const sel=byId(selectedId);if(sel)selectionNodes(sel).forEach(n=>svg.appendChild(n));}
function beginInlineText(p){if(activeEditor)activeEditor.finish(true);const input=document.createElement('textarea');input.className='inline-text';input.rows=1;input.spellcheck=false;const fs=Number(settings.font_size||18);Object.assign(input.style,{left:p.x+'px',top:p.y+'px',fontSize:fs+'px',fontFamily:settings.font==='Arial'?'Arial':'sans-serif',fontWeight:settings.bold?'bold':'normal',fontStyle:settings.italic?'italic':'normal',textDecoration:settings.underline?'underline':'none',color:settings.color,width:'2px',height:Math.ceil(fs*1.3)+'px'});stage.appendChild(input);let finished=false;function measure(){const lines=(input.value||'').split('\n'),longest=lines.reduce((a,b)=>a.length>=b.length?a:b,'');input.style.width=Math.max(2,Math.ceil(longest.length*fs*.68)+4)+'px';input.style.height=Math.max(Math.ceil(fs*1.3),Math.ceil(lines.length*fs*1.2))+'px';}function finish(save){if(finished)return;finished=true;const text=input.value;input.remove();activeEditor=null;if(save&&text.trim()){pushHistory();const lines=text.split('\n'),longest=lines.reduce((a,b)=>a.length>=b.length?a:b,''),w=Math.max(8,longest.length*fs*.68),h=Math.max(fs*1.35,lines.length*fs*1.2),o={id:rid(),type:'text',x:p.x,y:p.y,w,h,rotation:0,text,font:settings.font,font_size:fs,bold:settings.bold,italic:settings.italic,underline:settings.underline,color:settings.color,thickness:thickness()};objects.push(o);selectedId=null;suppressNextTextClick=true;render();emit();}else render();}activeEditor={input,finish};input.addEventListener('pointerdown',e=>e.stopPropagation());input.addEventListener('input',measure);input.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Escape'){e.preventDefault();finish(false)}else if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();finish(true)}});input.addEventListener('blur',()=>setTimeout(()=>{if(activeEditor&&activeEditor.input===input)finish(true)},0));measure();requestAnimationFrame(()=>input.focus({preventScroll:true}));}
svg.addEventListener('pointerdown',e=>{const p=point(e);if(activeEditor)activeEditor.finish(true);if(settings.tool==='select'){if(e.target===svg){selectedId=null;render();emit();}return;}if(settings.tool==='text')return;if(settings.tool==='image'){const asset=imageAssets[args.active_image_id];if(!asset)return;pushHistory();const maxW=Math.min(280,args.width*.42),maxH=Math.min(220,args.height*.32),ratio=Math.max(.01,Number(asset.width||1)/Math.max(1,Number(asset.height||1)));let w=maxW,h=w/ratio;if(h>maxH){h=maxH;w=h*ratio;}const o={id:rid(),type:'image',asset_id:args.active_image_id,x:p.x-w/2,y:p.y-h/2,w,h,rotation:0,aspect_ratio:ratio};objects.push(o);selectedId=o.id;settings.tool='select';renderToolbar();render();emit();return;}if(['rectangle','circle','line','arrow'].includes(settings.tool)){draft=settings.tool==='line'||settings.tool==='arrow'?{id:rid(),type:settings.tool,x1:p.x,y1:p.y,x2:p.x,y2:p.y,rotation:0,color:settings.color,thickness:thickness()}:{id:rid(),type:settings.tool,x:p.x,y:p.y,w:1,h:1,rotation:0,color:settings.color,thickness:thickness()};pushHistory();action={kind:'draw',start:p,historySaved:true};svg.setPointerCapture(e.pointerId);render();return;}if(settings.tool==='pen'){pushHistory();draft={id:rid(),type:'pen',points:[p],rotation:0,color:settings.color,thickness:thickness()};action={kind:'pen',historySaved:true};svg.setPointerCapture(e.pointerId);render();}});
svg.addEventListener('click',e=>{if(settings.tool!=='text'||e.target!==svg)return;if(suppressNextTextClick){suppressNextTextClick=false;return;}beginInlineText(point(e));});
svg.addEventListener('pointermove',e=>{if(!action)return;const p=point(e);if(action.kind==='draw'&&draft){if(draft.type==='line'||draft.type==='arrow'){draft.x2=p.x;draft.y2=p.y}else{draft.x=Math.min(action.start.x,p.x);draft.y=Math.min(action.start.y,p.y);draft.w=Math.abs(p.x-action.start.x);draft.h=Math.abs(p.y-action.start.y)}render();}else if(action.kind==='pen'&&draft){draft.points.push(p);render();}else{const o=byId(action.id);if(!o)return;if(!action.historySaved){pushHistory();action.historySaved=true;}if(action.kind==='move'){const dx=p.x-action.start.x,dy=p.y-action.start.y,orig=action.original;if(o.type==='line'||o.type==='arrow'){o.x1=orig.x1+dx;o.y1=orig.y1+dy;o.x2=orig.x2+dx;o.y2=orig.y2+dy}else if(o.type==='pen')o.points=orig.points.map(q=>({x:q.x+dx,y:q.y+dy}));else{o.x=orig.x+dx;o.y=orig.y+dy}}else if(action.kind==='p1'||action.kind==='p2'){if(action.kind==='p1'){o.x1=p.x;o.y1=p.y}else{o.x2=p.x;o.y2=p.y}if(o.type==='arrow'){const ol=Math.max(1,Math.hypot(action.original.x2-action.original.x1,action.original.y2-action.original.y1)),nl=Math.max(1,Math.hypot(o.x2-o.x1,o.y2-o.y1));o.thickness=Math.max(.5,Number(action.original.thickness||2)*(nl/ol));}}else if(action.kind==='rotate')o.rotation=Math.atan2(p.y-action.center.y,p.x-action.center.x)*180/Math.PI+90;else if(action.kind==='resize'){const orig=action.original,b=bbox(orig);let x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h;if(action.corner.includes('n'))y1=p.y;if(action.corner.includes('s'))y2=p.y;if(action.corner.includes('w'))x1=p.x;if(action.corner.includes('e'))x2=p.x;const nx=Math.min(x1,x2),ny=Math.min(y1,y2),nw=Math.max(5,Math.abs(x2-x1)),nh=Math.max(5,Math.abs(y2-y1));if(o.type==='pen'){const sx=nw/b.w,sy=nh/b.h;o.points=orig.points.map(q=>({x:nx+(q.x-b.x)*sx,y:ny+(q.y-b.y)*sy}))}else if(o.type==='arrow'){const sx=nw/b.w,sy=nh/b.h,scale=Math.max(.2,Math.sqrt(Math.abs(sx*sy)));o.x1=nx+(orig.x1-b.x)*sx;o.y1=ny+(orig.y1-b.y)*sy;o.x2=nx+(orig.x2-b.x)*sx;o.y2=ny+(orig.y2-b.y)*sy;o.thickness=Math.max(.5,Number(orig.thickness||2)*scale)}else if(o.type==='text'){const scale=Math.max(.2,Math.max(nw/b.w,nh/b.h));o.x=nx;o.y=ny;o.font_size=Math.max(4,orig.font_size*scale);o.w=Math.max(20,orig.w*scale);o.h=Math.max(5,orig.h*scale)}else if(o.type==='image'){const scale=Math.max(.05,Math.max(nw/b.w,nh/b.h));const newW=Math.max(8,orig.w*scale),newH=Math.max(8,orig.h*scale);let anchorX=b.x,anchorY=b.y;if(action.corner.includes('w'))anchorX=b.x+b.w-newW;if(action.corner.includes('n'))anchorY=b.y+b.h-newH;o.x=anchorX;o.y=anchorY;o.w=newW;o.h=newH}else{o.x=nx;o.y=ny;o.w=nw;o.h=nh}}render();}});
svg.addEventListener('pointerup',()=>{if(!action)return;if((action.kind==='draw'||action.kind==='pen')&&draft){objects.push(draft);selectedId=draft.id;draft=null;}action=null;render();emit();});
function deleteSelected(){if(selectedId){pushHistory();objects=objects.filter(o=>o.id!==selectedId);selectedId=null;render();emit();}}
window.addEventListener('keydown',e=>{if(activeEditor)return;const k=e.key.toLowerCase();if((e.key==='Delete'||e.key==='Backspace')&&selectedId){e.preventDefault();deleteSelected()}else if(e.key==='Escape'){selectedId=null;render();emit()}else if((e.ctrlKey||e.metaKey)&&k==='d'&&selectedId){e.preventDefault();const original=byId(selectedId);if(original){pushHistory();const copy=JSON.parse(JSON.stringify(original));copy.id=rid();if(copy.type==='line'||copy.type==='arrow'){copy.x1+=12;copy.y1+=12;copy.x2+=12;copy.y2+=12}else if(copy.type==='pen')copy.points=copy.points.map(p=>({x:p.x+12,y:p.y+12}));else{copy.x+=12;copy.y+=12}objects.push(copy);selectedId=copy.id;render();emit();}}else if((e.ctrlKey||e.metaKey)&&k==='z'){e.preventDefault();if(undoStack.length){objects=undoStack.pop();selectedId=null;render();emit();}}});
window.addEventListener('message',event=>{if(!event.data||event.data.type!=='streamlit:render')return;args=event.data.args||{};objects=JSON.parse(JSON.stringify(args.annotations||[]));selectedId=args.selected_id||null;undoStack=JSON.parse(JSON.stringify(args.undo_stack||[]));settings=Object.assign(settings,args.settings||{});imageAssets=args.image_assets||{};img.src='data:image/png;base64,'+args.preview_b64;img.width=args.width;img.height=args.height;stage.style.width=args.width+'px';stage.style.height=args.height+'px';svg.setAttribute('width',args.width);svg.setAttribute('height',args.height);svg.setAttribute('viewBox',`0 0 ${args.width} ${args.height}`);renderToolbar();render();});
ready();
</script>
</body>
</html>
"""
    with open(index_path, "w", encoding="utf-8") as component_file:
        component_file.write(component_html)
    return components.declare_component("pdf_annotation_component_v8", path=component_dir)


def show_annotation_editor(page_num, page_count, page_image, annotations, selected_id, undo_stack, settings, image_assets, active_image_id):
    component = get_annotation_component()
    return component(
        page_num=page_num,
        page_count=page_count,
        width=page_image.width,
        height=page_image.height,
        preview_b64=image_to_base64_png(page_image),
        annotations=annotations,
        selected_id=selected_id,
        undo_stack=undo_stack,
        settings=settings,
        image_assets=image_assets,
        active_image_id=active_image_id,
        key=f"annotation_editor_v8_page_{page_num}",
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


def prepare_annotation_image_asset(uploaded_file):
    """Convert a supported uploaded image to a browser/PDF-safe PNG asset."""
    raw_bytes = uploaded_file.getvalue()
    digest = hashlib.sha256(raw_bytes).hexdigest()[:20]
    filename = uploaded_file.name or "image"
    extension = os.path.splitext(filename)[1].lower()
    image = None
    svg_document = None
    png_buffer = BytesIO()

    try:
        if extension == ".svg" or uploaded_file.type == "image/svg+xml":
            svg_document = fitz.open(stream=raw_bytes, filetype="svg")
            svg_page = svg_document[0]
            pixmap = svg_page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=True)
            image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGBA")
        else:
            image = Image.open(BytesIO(raw_bytes))
            image.seek(0)
            image = image.convert("RGBA")

        image.save(png_buffer, format="PNG")
        png_bytes = png_buffer.getvalue()
        return digest, {
            "name": filename,
            "width": image.width,
            "height": image.height,
            "png_bytes": png_bytes,
            "original_png_bytes": png_bytes,
            "data_url": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii"),
            "background_removed": False,
            "background_color": None,
            "background_tolerance": 32,
        }
    finally:
        if image is not None:
            image.close()
        if svg_document is not None:
            svg_document.close()
        png_buffer.close()



def remove_image_background(png_bytes, background_rgb, tolerance):
    """Return an RGBA PNG with pixels near the selected colour made transparent."""
    source = None
    output = BytesIO()
    try:
        source = Image.open(BytesIO(png_bytes)).convert("RGBA")
        pixels = source.load()
        red, green, blue = [int(value) for value in background_rgb]
        tolerance = max(0.0, float(tolerance))
        feather = max(4.0, min(24.0, tolerance * 0.35))

        for y in range(source.height):
            for x in range(source.width):
                pr, pg, pb, pa = pixels[x, y]
                distance = ((pr - red) ** 2 + (pg - green) ** 2 + (pb - blue) ** 2) ** 0.5
                if distance <= tolerance:
                    new_alpha = 0
                elif distance < tolerance + feather:
                    ratio = (distance - tolerance) / feather
                    new_alpha = int(round(pa * ratio))
                else:
                    new_alpha = pa
                pixels[x, y] = (pr, pg, pb, new_alpha)

        source.save(output, format="PNG")
        return output.getvalue()
    finally:
        if source is not None:
            source.close()
        output.close()


def transparency_checkerboard_preview(png_bytes, max_width=620):
    """Composite a transparent image over a checkerboard for a clear live preview."""
    source = None
    preview = None
    try:
        source = Image.open(BytesIO(png_bytes)).convert("RGBA")
        if source.width > max_width:
            ratio = max_width / source.width
            source = source.resize(
                (max_width, max(1, int(round(source.height * ratio)))),
                Image.Resampling.LANCZOS,
            )

        tile = max(8, min(24, source.width // 24 if source.width else 12))
        checker = Image.new("RGBA", source.size, (235, 235, 235, 255))
        draw = ImageDraw.Draw(checker)
        for y in range(0, source.height, tile):
            for x in range(0, source.width, tile):
                if ((x // tile) + (y // tile)) % 2:
                    draw.rectangle(
                        (x, y, min(source.width, x + tile), min(source.height, y + tile)),
                        fill=(195, 195, 195, 255),
                    )
        preview = Image.alpha_composite(checker, source)
        return preview.convert("RGB")
    finally:
        if source is not None:
            source.close()


@st.dialog("Prepare transparent image background")
def show_image_background_dialog(asset_id):
    """Eyedropper and live tolerance preview that does not modify the PDF view."""
    assets = st.session_state.get("annotate_image_assets", {})
    asset = assets.get(asset_id)
    if not asset:
        st.error("The selected image is no longer available.")
        return

    original_bytes = asset.get("original_png_bytes") or asset.get("png_bytes")
    state_key = f"annotate_bg_state_{asset_id}"
    state = st.session_state.setdefault(
        state_key,
        {
            "background_rgb": tuple(asset.get("background_color") or (255, 255, 255)),
            "tolerance": int(asset.get("background_tolerance", 32)),
        },
    )

    st.write(
        "Click the background colour in the image on the left. Adjust tolerance and "
        "review the transparency on the checkerboard before applying it."
    )

    original = Image.open(BytesIO(original_bytes)).convert("RGBA")
    display_width = min(560, original.width)
    scale = display_width / max(1, original.width)
    display_height = max(1, int(round(original.height * scale)))
    clickable = original.convert("RGB")

    left, right = st.columns(2)
    with left:
        st.markdown("**Eyedropper — click the background**")
        click = streamlit_image_coordinates(
            clickable,
            width=display_width,
            key=f"annotate_bg_picker_{asset_id}",
        )
        if click is not None:
            source_x = max(0, min(original.width - 1, int(round(click["x"] / scale))))
            source_y = max(0, min(original.height - 1, int(round(click["y"] / scale))))
            sampled = original.getpixel((source_x, source_y))[:3]
            if tuple(state.get("background_rgb", ())) != tuple(sampled):
                state["background_rgb"] = tuple(sampled)
                st.rerun()

        rgb = tuple(state.get("background_rgb", (255, 255, 255)))
        swatch_hex = "#%02X%02X%02X" % rgb
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin-top:6px;">'
            f'<span style="width:30px;height:30px;border:1px solid #555;background:{swatch_hex};display:inline-block;"></span>'
            f'<span>Selected background: {swatch_hex} &nbsp; RGB {rgb}</span></div>',
            unsafe_allow_html=True,
        )

    state["tolerance"] = st.slider(
        "Background tolerance",
        min_value=0,
        max_value=180,
        value=int(state.get("tolerance", 32)),
        help="Increase this to remove shades close to the sampled background colour.",
        key=f"annotate_bg_tolerance_{asset_id}",
    )

    processed_bytes = remove_image_background(
        original_bytes,
        state["background_rgb"],
        state["tolerance"],
    )
    with right:
        st.markdown("**Live transparency preview**")
        checker = transparency_checkerboard_preview(processed_bytes)
        try:
            st.image(checker, use_container_width=True)
        finally:
            checker.close()

    apply_col, reset_col, cancel_col = st.columns(3)
    with apply_col:
        if st.button("Apply transparency", use_container_width=True, key=f"annotate_bg_apply_{asset_id}"):
            asset["png_bytes"] = processed_bytes
            asset["data_url"] = "data:image/png;base64," + base64.b64encode(processed_bytes).decode("ascii")
            asset["background_removed"] = True
            asset["background_color"] = tuple(state["background_rgb"])
            asset["background_tolerance"] = int(state["tolerance"])
            st.session_state["annotate_active_image_id"] = asset_id
            st.rerun()
    with reset_col:
        if st.button("Restore original", use_container_width=True, key=f"annotate_bg_reset_{asset_id}"):
            asset["png_bytes"] = original_bytes
            asset["data_url"] = "data:image/png;base64," + base64.b64encode(original_bytes).decode("ascii")
            asset["background_removed"] = False
            asset["background_color"] = None
            st.session_state["annotate_active_image_id"] = asset_id
            st.rerun()
    with cancel_col:
        if st.button("Close", use_container_width=True, key=f"annotate_bg_close_{asset_id}"):
            st.rerun()

    original.close()
    clickable.close()


def insert_rotated_image(page, item, image_asset):
    """Burn an independently movable, resizable, and rotatable image into a page."""
    if not image_asset or not image_asset.get("png_bytes"):
        return

    display_width = max(1, int(round(float(item.get("w", 1)))))
    display_height = max(1, int(round(float(item.get("h", 1)))))
    angle = float(item.get("rotation", 0))
    source = None
    resized = None
    rotated = None
    output = BytesIO()

    try:
        source = Image.open(BytesIO(image_asset["png_bytes"])).convert("RGBA")
        resized = source.resize(
            (display_width, display_height),
            Image.Resampling.LANCZOS,
        )
        if angle % 360:
            # SVG/CSS positive angles rotate clockwise in screen coordinates.
            rotated = resized.rotate(
                -angle,
                expand=True,
                resample=Image.Resampling.BICUBIC,
            )
        else:
            rotated = resized.copy()
        rotated.save(output, format="PNG")

        display_points = rotated_points_for_annotation(item)
        xs = [point[0] for point in display_points]
        ys = [point[1] for point in display_points]
        display_rect = (min(xs), min(ys), max(xs), max(ys))
        top_left = display_point_to_pdf(page, display_rect[0], display_rect[1])
        bottom_right = display_point_to_pdf(page, display_rect[2], display_rect[3])
        pdf_rect = fitz.Rect(top_left, bottom_right).normalize()
        pdf_rect = pdf_rect & page.cropbox

        if not pdf_rect.is_empty and not pdf_rect.is_infinite:
            page.insert_image(
                pdf_rect,
                stream=output.getvalue(),
                keep_proportion=False,
                overlay=True,
            )
    finally:
        if rotated is not None:
            rotated.close()
        if resized is not None:
            resized.close()
        if source is not None:
            source.close()
        output.close()


def apply_annotations_to_page(page, annotations, image_assets=None):
    """Create independent PDF annotation objects from the editor's object model."""
    image_assets = image_assets or {}
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

        elif kind == "image":
            insert_rotated_image(page, item, image_assets.get(item.get("asset_id")))

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

    image_assets = st.session_state.setdefault("annotate_image_assets", {})
    uploaded_image = st.file_uploader(
        "Upload an image to insert",
        type=["png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp", "svg"],
        key=f"annotate_image_upload_{st.session_state.get('uploader_nonce', 0)}",
        help="PNG, JPG, JPEG, BMP, GIF, TIFF, WEBP, and SVG are supported.",
    )
    if uploaded_image is not None:
        try:
            asset_id, prepared_asset = prepare_annotation_image_asset(uploaded_image)
            if asset_id not in image_assets:
                image_assets[asset_id] = prepared_asset
            else:
                # Preserve any transparency preparation already applied to this upload.
                image_assets[asset_id].setdefault(
                    "original_png_bytes",
                    prepared_asset.get("original_png_bytes", prepared_asset["png_bytes"]),
                )
            asset = image_assets[asset_id]
            st.session_state["annotate_active_image_id"] = asset_id
            st.caption(
                f"Image ready: {asset['name']} ({asset['width']} × {asset['height']}). "
                "Choose Image in the annotation toolbar, then click the PDF to place it."
            )
        except Exception as exc:
            st.error(f"Unable to prepare the uploaded image: {exc}")

    active_image_id = st.session_state.get("annotate_active_image_id")
    if active_image_id and active_image_id in image_assets and uploaded_image is None:
        active_asset = image_assets[active_image_id]
        transparency_note = " Background transparency applied." if active_asset.get("background_removed") else ""
        st.caption(
            f"Image ready: {active_asset['name']}.{transparency_note} "
            "Choose Image in the annotation toolbar, then click the PDF to place it."
        )

    if active_image_id and active_image_id in image_assets:
        prepare_col, restore_col, _ = st.columns([1.35, 1.1, 3])
        with prepare_col:
            if st.button("Eyedropper / remove background", key="annotate_prepare_image_bg", use_container_width=True):
                show_image_background_dialog(active_image_id)
        with restore_col:
            active_asset = image_assets[active_image_id]
            if st.button(
                "Restore original image",
                key="annotate_restore_image",
                use_container_width=True,
                disabled=not active_asset.get("background_removed", False),
            ):
                original_bytes = active_asset.get("original_png_bytes")
                if original_bytes:
                    active_asset["png_bytes"] = original_bytes
                    active_asset["data_url"] = "data:image/png;base64," + base64.b64encode(original_bytes).decode("ascii")
                    active_asset["background_removed"] = False
                    active_asset["background_color"] = None
                    st.rerun()

    page_num = min(int(st.session_state.get("annotate_page_num", 0)), page_count - 1)
    st.session_state["annotate_page_num"] = page_num
    page_image = render_pdf_page(pdf_bytes, page_num)

    pages = st.session_state.setdefault("annotate_annotations", {})
    pages.setdefault(page_num, [])
    selected_by_page = st.session_state.setdefault("annotate_selected", {})
    last_nonce = st.session_state.setdefault("annotate_last_nonce", {})
    undo_by_page = st.session_state.setdefault("annotate_undo", {})
    undo_by_page.setdefault(page_num, [])
    settings = st.session_state.setdefault(
        "annotate_settings",
        {
            "tool": "select",
            "color": "#000000",
            "font": "Arial",
            "thickness": 2,
            "font_size": 18,
            "bold": False,
            "italic": False,
            "underline": False,
        },
    )

    result = show_annotation_editor(
        page_num,
        page_count,
        page_image,
        pages[page_num],
        selected_by_page.get(page_num),
        undo_by_page[page_num],
        settings,
        {
            asset_id: {
                "name": asset["name"],
                "width": asset["width"],
                "height": asset["height"],
                "data_url": asset["data_url"],
            }
            for asset_id, asset in image_assets.items()
        },
        active_image_id,
    )
    st.caption(
        "Use the compact toolbar above the PDF. Settings remain active until changed. "
        "The page selector, tools, colours, thickness, font controls, image placement, and editing actions are all in the toolbar."
    )

    if result is not None:
        nonce = str(result.get("nonce", ""))
        if nonce and last_nonce.get(page_num) != nonce:
            pages[page_num] = result.get("annotations", [])
            selected_by_page[page_num] = result.get("selected_id")
            undo_by_page[page_num] = result.get("undo_stack", [])
            incoming_settings = result.get("settings")
            if isinstance(incoming_settings, dict):
                st.session_state["annotate_settings"] = incoming_settings
            last_nonce[page_num] = nonce

            requested_page = result.get("requested_page")
            if requested_page is not None:
                st.session_state["annotate_page_num"] = max(0, min(page_count - 1, int(requested_page)))
            st.rerun()

    counts = ", ".join(f"Page {index + 1}: {len(pages.get(index, []))}" for index in range(page_count))
    st.info(f"Annotations saved - {counts}")

    if st.button("Generate Annotated PDF", key="annotate_generate"):
        output = BytesIO()
        document = None
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            for index in range(len(document)):
                apply_annotations_to_page(
                    document[index],
                    pages.get(index, []),
                    image_assets=image_assets,
                )
            document.save(output)
            output_bytes = output.getvalue()
        finally:
            if document is not None:
                document.close()
            output.close()
            gc.collect()
        original_name = st.session_state.get("annotate_pdf_name", "document.pdf")
        base_name = os.path.splitext(original_name)[0] or "document"
        st.download_button(
            "Download Annotated PDF",
            data=output_bytes,
            file_name=f"{base_name}_annotated.pdf",
            mime="application/pdf",
            key="annotate_download",
        )

    del page_image
    gc.collect()



# ============================================================
# Split PDF tool (isolated workflow)
# ============================================================


def get_split_preview_component():
    """Create/load the isolated page-selection and output-order component."""
    component_dir = os.path.join(tempfile.gettempdir(), "split_pdf_preview_component_v2")
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
        background: #f5f5f5;
        font-family: Arial, sans-serif;
    }

    #scroll-window {
        width: 100%;
        height: 660px;
        overflow-x: auto;
        overflow-y: hidden;
        border: 1px solid #cccccc;
        box-sizing: border-box;
        background: #eeeeee;
    }

    #toolbar {
        position: sticky;
        left: 0;
        top: 0;
        z-index: 10;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 10px;
        border-bottom: 1px solid #cccccc;
        background: #ffffff;
    }

    #toolbar button,
    .order-button {
        padding: 6px 12px;
        cursor: pointer;
    }

    #status {
        color: #444444;
        font-size: 14px;
    }

    #page-strip {
        display: flex;
        align-items: flex-start;
        gap: 18px;
        width: max-content;
        min-width: 100%;
        padding: 16px;
        box-sizing: border-box;
    }

    .page-card {
        flex: 0 0 auto;
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 10px;
        border: 2px solid transparent;
        border-radius: 6px;
        background: #ffffff;
        box-shadow: 0 1px 5px rgba(0, 0, 0, 0.18);
    }

    .page-card.selected {
        border-color: #1f77b4;
        background: #f1f8ff;
    }

    .page-image {
        display: block;
        max-height: 560px;
        width: auto;
        max-width: none;
        background: #ffffff;
    }

    .page-choice {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 10px;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        user-select: none;
    }

    .page-choice input {
        width: 20px;
        height: 20px;
        cursor: pointer;
    }

    #order-panel {
        border: 1px solid #cccccc;
        border-top: 0;
        background: #ffffff;
        padding: 10px;
    }

    #order-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 8px;
    }

    #order-header strong {
        font-size: 16px;
    }

    #order-strip {
        display: flex;
        gap: 10px;
        overflow-x: auto;
        padding: 4px 2px 8px 2px;
        min-height: 76px;
    }

    .order-card {
        flex: 0 0 auto;
        display: grid;
        grid-template-columns: auto auto auto;
        align-items: center;
        gap: 6px;
        border: 1px solid #aaaaaa;
        border-radius: 6px;
        padding: 8px;
        background: #f8f8f8;
    }

    .order-label {
        min-width: 70px;
        text-align: center;
        font-weight: 700;
    }

    .order-button {
        min-width: 36px;
        padding: 5px 8px;
    }

    .order-button:disabled {
        opacity: 0.4;
        cursor: default;
    }

    #order-empty {
        color: #666666;
        padding: 12px 4px;
    }
</style>
</head>
<body>
<div id="scroll-window">
    <div id="toolbar">
        <button id="select-all" type="button">Select all</button>
        <button id="clear-all" type="button">Clear all</button>
        <span id="status"></span>
    </div>
    <div id="page-strip"></div>
</div>
<div id="order-panel">
    <div id="order-header">
        <strong>Selected pages in output order</strong>
        <button id="restore-order" type="button">Restore original order</button>
    </div>
    <div id="order-strip"></div>
</div>

<script>
    let currentPages = [];
    let outputOrder = [];

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

    function selectedPages() {
        return Array.from(document.querySelectorAll(".page-checkbox:checked"))
            .map(function(box) { return Number(box.dataset.page); })
            .sort(function(a, b) { return a - b; });
    }

    function normalizeOrder(selected) {
        const selectedSet = new Set(selected);
        const seen = new Set();
        outputOrder = outputOrder.filter(function(pageNum) {
            if (!selectedSet.has(pageNum) || seen.has(pageNum)) {
                return false;
            }
            seen.add(pageNum);
            return true;
        });
        selected.forEach(function(pageNum) {
            if (!seen.has(pageNum)) {
                outputOrder.push(pageNum);
                seen.add(pageNum);
            }
        });
    }

    function renderOrderStrip() {
        const strip = document.getElementById("order-strip");
        strip.innerHTML = "";

        if (outputOrder.length === 0) {
            const empty = document.createElement("div");
            empty.id = "order-empty";
            empty.textContent = "Select one or more pages above.";
            strip.appendChild(empty);
            return;
        }

        outputOrder.forEach(function(pageNum, index) {
            const card = document.createElement("div");
            card.className = "order-card";

            const left = document.createElement("button");
            left.type = "button";
            left.className = "order-button";
            left.textContent = "◀";
            left.title = "Move page left";
            left.disabled = index === 0;
            left.addEventListener("click", function() {
                if (index > 0) {
                    const temp = outputOrder[index - 1];
                    outputOrder[index - 1] = outputOrder[index];
                    outputOrder[index] = temp;
                    commitState();
                }
            });

            const label = document.createElement("div");
            label.className = "order-label";
            label.textContent = "Page " + (pageNum + 1);

            const right = document.createElement("button");
            right.type = "button";
            right.className = "order-button";
            right.textContent = "▶";
            right.title = "Move page right";
            right.disabled = index === outputOrder.length - 1;
            right.addEventListener("click", function() {
                if (index < outputOrder.length - 1) {
                    const temp = outputOrder[index + 1];
                    outputOrder[index + 1] = outputOrder[index];
                    outputOrder[index] = temp;
                    commitState();
                }
            });

            card.appendChild(left);
            card.appendChild(label);
            card.appendChild(right);
            strip.appendChild(card);
        });
    }

    function updateVisuals() {
        document.querySelectorAll(".page-card").forEach(function(card) {
            const box = card.querySelector(".page-checkbox");
            card.classList.toggle("selected", Boolean(box && box.checked));
        });
        const count = selectedPages().length;
        document.getElementById("status").textContent =
            count + " of " + currentPages.length + " page" + (currentPages.length === 1 ? "" : "s") + " selected";
        renderOrderStrip();
    }

    function commitState() {
        const selected = selectedPages();
        normalizeOrder(selected);
        updateVisuals();
        setComponentValue({
            selected_pages: selected,
            output_order: outputOrder.slice(),
            nonce: Date.now() + "_" + Math.random().toString(36).slice(2)
        });
    }

    function render(args) {
        currentPages = args.pages || [];
        const selected = new Set((args.selected_pages || []).map(Number));
        outputOrder = (args.output_order || []).map(Number);
        normalizeOrder(Array.from(selected).sort(function(a, b) { return a - b; }));

        const strip = document.getElementById("page-strip");
        strip.innerHTML = "";

        currentPages.forEach(function(page) {
            const card = document.createElement("div");
            card.className = "page-card";

            const img = document.createElement("img");
            img.className = "page-image";
            img.src = "data:image/png;base64," + page.preview_b64;
            img.alt = "Page " + (page.page_num + 1);
            img.width = page.width;
            img.height = page.height;
            card.appendChild(img);

            const label = document.createElement("label");
            label.className = "page-choice";

            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "page-checkbox";
            checkbox.dataset.page = String(page.page_num);
            checkbox.checked = selected.has(Number(page.page_num));
            checkbox.addEventListener("change", commitState);

            const text = document.createElement("span");
            text.textContent = "Include page " + (page.page_num + 1);

            label.appendChild(checkbox);
            label.appendChild(text);
            card.appendChild(label);
            strip.appendChild(card);
        });

        updateVisuals();
        setFrameHeight(790);
    }

    document.getElementById("select-all").addEventListener("click", function() {
        document.querySelectorAll(".page-checkbox").forEach(function(box) {
            box.checked = true;
        });
        commitState();
    });

    document.getElementById("clear-all").addEventListener("click", function() {
        document.querySelectorAll(".page-checkbox").forEach(function(box) {
            box.checked = false;
        });
        outputOrder = [];
        commitState();
    });

    document.getElementById("restore-order").addEventListener("click", function() {
        outputOrder = selectedPages();
        commitState();
    });

    window.addEventListener("message", function(event) {
        if (event.data && event.data.type === "streamlit:render") {
            render(event.data.args || {});
        }
    });

    sendMessageToStreamlitClient("streamlit:componentReady", { apiVersion: 1 });
    setFrameHeight(790);
</script>
</body>
</html>
"""

    with open(index_path, "w", encoding="utf-8") as component_file:
        component_file.write(component_html)

    return components.declare_component("split_pdf_preview_component_v2", path=component_dir)


def get_split_page_previews(pdf_bytes, zoom=0.85):
    """Render all pages once for the isolated Split PDF preview strip."""
    cache = st.session_state.get("split_preview_cache")
    document_signature = (len(pdf_bytes), pdf_bytes[:32], pdf_bytes[-32:])

    if cache and cache.get("signature") == document_signature:
        return cache["pages"]

    document = None
    previews = []
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        matrix = fitz.Matrix(zoom, zoom)
        for page_num in range(len(document)):
            pixmap = document[page_num].get_pixmap(matrix=matrix, alpha=False)
            previews.append(
                {
                    "page_num": page_num,
                    "width": pixmap.width,
                    "height": pixmap.height,
                    "preview_b64": base64.b64encode(pixmap.tobytes("png")).decode("ascii"),
                }
            )
    finally:
        if document is not None:
            document.close()
        gc.collect()

    st.session_state["split_preview_cache"] = {
        "signature": document_signature,
        "pages": previews,
    }
    return previews


def render_split_tool():
    """Render the fully isolated page-extraction and page-order workflow."""
    st.header("Split PDF")
    pdf_bytes = upload_pdf_for_mode(MODE_SPLIT)
    if pdf_bytes is None:
        st.info(f"Upload a PDF to begin: {MODE_LABELS[MODE_SPLIT]}.")
        return

    page_count = get_pdf_page_count(pdf_bytes)
    if page_count is None:
        return

    st.success(f"Loaded PDF with {page_count} page(s).")
    st.write(
        "Scroll left and right through the preview and check the pages to include. "
        "Use the output-order strip below the previews to move selected pages left or right."
    )

    selected_pages = st.session_state.setdefault("split_selected_pages", [])
    output_order = st.session_state.setdefault("split_output_order", list(selected_pages))
    last_nonce = st.session_state.get("split_last_nonce")
    previews = get_split_page_previews(pdf_bytes)
    component = get_split_preview_component()

    result = component(
        pages=previews,
        selected_pages=selected_pages,
        output_order=output_order,
        key="split_page_preview_v2",
        default=None,
    )

    if result is not None:
        nonce = str(result.get("nonce", ""))
        if nonce and nonce != last_nonce:
            valid_pages = sorted(
                {
                    int(page_num)
                    for page_num in result.get("selected_pages", [])
                    if 0 <= int(page_num) < page_count
                }
            )
            valid_set = set(valid_pages)
            received_order = []
            seen = set()
            for raw_page_num in result.get("output_order", []):
                page_num = int(raw_page_num)
                if page_num in valid_set and page_num not in seen:
                    received_order.append(page_num)
                    seen.add(page_num)
            for page_num in valid_pages:
                if page_num not in seen:
                    received_order.append(page_num)

            st.session_state["split_selected_pages"] = valid_pages
            st.session_state["split_output_order"] = received_order
            st.session_state["split_last_nonce"] = nonce
            st.rerun()

    selected_pages = st.session_state.get("split_selected_pages", [])
    selected_set = set(selected_pages)
    output_order = [
        page_num
        for page_num in st.session_state.get("split_output_order", [])
        if page_num in selected_set
    ]
    for page_num in selected_pages:
        if page_num not in output_order:
            output_order.append(page_num)
    st.session_state["split_output_order"] = output_order

    if output_order:
        display_pages = " → ".join(str(page_num + 1) for page_num in output_order)
        st.info(f"Output order ({len(output_order)} page(s)): {display_pages}")
    else:
        st.warning("No pages are currently selected.")

    if st.button("Generate Excerpt PDF", key="split_generate", disabled=not output_order):
        source_document = None
        excerpt_document = None
        output = BytesIO()
        try:
            source_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            excerpt_document = fitz.open()
            for page_num in output_order:
                excerpt_document.insert_pdf(
                    source_document,
                    from_page=page_num,
                    to_page=page_num,
                )
            excerpt_document.save(output)
            output_bytes = output.getvalue()
        finally:
            if excerpt_document is not None:
                excerpt_document.close()
            if source_document is not None:
                source_document.close()
            output.close()
            gc.collect()

        original_name = st.session_state.get("split_pdf_name", "document.pdf")
        base_name = os.path.splitext(original_name)[0] or "document"
        st.download_button(
            "Download Excerpt PDF",
            data=output_bytes,
            file_name=f"{base_name}_excerpt.pdf",
            mime="application/pdf",
            key="split_download",
        )


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

    if st.button("Split PDF", use_container_width=True):
        switch_mode(MODE_SPLIT)

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
elif active_mode == MODE_SPLIT:
    render_split_tool()
else:
    st.info("Choose a function from the sidebar.")

