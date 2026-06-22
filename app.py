import fitz
import pandas as pd
import streamlit as st

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from streamlit_drawable_canvas import st_canvas
from streamlit_image_coordinates import streamlit_image_coordinates


st.set_page_config(
    page_title="PDF Redactor / PDF to XLSX",
    layout="wide",
)

st.title("PDF Redactor / PDF to XLSX")

task = st.radio(
    "Choose Function",
    ["Redact PDF", "Convert PDF to XLSX"],
    horizontal=True,
)

uploaded = st.file_uploader("Upload PDF", type=["pdf"])

if uploaded is None:
    st.stop()

pdf_bytes = uploaded.getvalue()

try:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
except Exception as e:
    st.error(f"Unable to open PDF: {e}")
    st.stop()

st.success(f"Loaded PDF with {len(doc)} page(s).")

zoom = 1.5

if "redactions" not in st.session_state:
    st.session_state.redactions = {}

if "columns" not in st.session_state:
    st.session_state.columns = {}

page_num = st.selectbox(
    "Select Page",
    list(range(len(doc))),
    format_func=lambda x: f"Page {x + 1}",
)

page = doc[page_num]

pix = page.get_pixmap(
    matrix=fitz.Matrix(zoom, zoom),
    alpha=False,
)

page_image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")

st.session_state.redactions.setdefault(page_num, [])
st.session_state.columns.setdefault(page_num, [])


def clear_current_page():
    st.session_state.redactions[page_num] = []
    st.session_state.columns[page_num] = []


def make_download_button(buffer, filename, mime):
    st.download_button(
        label=f"Download {filename}",
        data=buffer.getvalue(),
        file_name=filename,
        mime=mime,
    )


def draw_column_overlay(image, columns):
    preview = image.copy()
    draw = ImageDraw.Draw(preview)

    for x in columns:
        draw.line(
            [(x, 0), (x, preview.height)],
            fill=(0, 0, 255),
            width=3,
        )

    return preview


def make_ruler(width, height=70, columns=None):
    columns = columns or []

    ruler = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(ruler)

    draw.rectangle(
        [(0, 0), (width - 1, height - 1)],
        outline=(80, 80, 80),
        width=1,
    )

    for x in range(0, width, 50):
        draw.line([(x, 0), (x, 25)], fill=(0, 0, 0), width=1)
        draw.text((x + 3, 28), str(x), fill=(0, 0, 0))

    for x in range(0, width, 10):
        draw.line([(x, 0), (x, 10)], fill=(130, 130, 130), width=1)

    for x in columns:
        draw.line([(x, 0), (x, height)], fill=(0, 0, 255), width=4)
        draw.ellipse(
            [(x - 6, height - 18), (x + 6, height - 6)],
            fill=(0, 0, 255),
        )

    return ruler


def toggle_column(columns, clicked_x, tolerance=10):
    columns = list(columns)

    for existing_x in columns:
        if abs(existing_x - clicked_x) <= tolerance:
            columns.remove(existing_x)
            return sorted(columns)

    columns.append(clicked_x)
    return sorted(columns)


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
            rows.append(
                {
                    "y": y_mid,
                    "words": [word],
                }
            )

    return rows


def assign_word_to_column(word, boundaries):
    x0, y0, x1, y1, text, *_ = word
    x_mid = (x0 + x1) / 2

    for i in range(len(boundaries) - 1):
        if boundaries[i] <= x_mid < boundaries[i + 1]:
            return i

    return None


left_col, right_col = st.columns([4, 1])

with right_col:
    st.subheader("Page Tools")

    if st.button("Clear Current Page"):
        clear_current_page()
        st.rerun()

    if task == "Redact PDF":
        st.info("Draw black rectangles over areas to redact.")
    else:
        st.info(
            "Click the ruler to add/remove column boundaries. "
            "Blue vertical lines show the selected columns."
        )

    st.write("Saved column points:")
    st.write(st.session_state.columns.get(page_num, []))


with left_col:
    if task == "Redact PDF":
        st.subheader("Redaction Markup")

        st.caption("If the canvas appears blank, confirm Streamlit is pinned to 1.40.0.")

        canvas_result = st_canvas(
            fill_color="rgba(0,0,0,0.80)",
            stroke_width=2,
            stroke_color="#000000",
            background_image=page_image,
            update_streamlit=True,
            height=page_image.height,
            width=page_image.width,
            drawing_mode="rect",
            key=f"redact_canvas_page_{page_num}",
        )

        if st.button("Save Redactions For This Page"):
            rects = []

            if canvas_result.json_data and "objects" in canvas_result.json_data:
                for obj in canvas_result.json_data["objects"]:
                    if obj.get("type") == "rect":
                        rects.append(
                            {
                                "x": obj["left"],
                                "y": obj["top"],
                                "w": obj["width"] * obj.get("scaleX", 1),
                                "h": obj["height"] * obj.get("scaleY", 1),
                            }
                        )

            st.session_state.redactions[page_num] = rects
            st.success(f"Saved {len(rects)} redaction rectangle(s).")

        if st.button("Generate Redacted PDF"):
            redacted_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

            for pnum, rects in st.session_state.redactions.items():
                p = redacted_doc[pnum]

                for r in rects:
                    x0 = r["x"] / zoom
                    y0 = r["y"] / zoom
                    x1 = (r["x"] + r["w"]) / zoom
                    y1 = (r["y"] + r["h"]) / zoom

                    rect = fitz.Rect(x0, y0, x1, y1)

                    p.add_redact_annot(
                        rect,
                        fill=(0, 0, 0),
                    )

                p.apply_redactions()

            output_pdf = BytesIO()
            redacted_doc.save(output_pdf)
            redacted_doc.close()
            output_pdf.seek(0)

            make_download_button(
                output_pdf,
                "redacted.pdf",
                "application/pdf",
            )

    else:
        st.subheader("Column Markup")

        current_columns = st.session_state.columns.get(page_num, [])

        ruler = make_ruler(
            width=page_image.width,
            columns=current_columns,
        )

        click = streamlit_image_coordinates(
            ruler,
            key=f"ruler_page_{page_num}",
        )

        if click is not None:
            clicked_x = int(click["x"])
            st.session_state.columns[page_num] = toggle_column(
                current_columns,
                clicked_x,
            )
            st.rerun()

        preview = draw_column_overlay(
            page_image,
            st.session_state.columns.get(page_num, []),
        )

        st.image(
            preview,
            caption=f"Page {page_num + 1} with selected column boundaries",
            use_container_width=False,
        )

        if st.button("Generate XLSX"):
            source_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            output_xlsx = BytesIO()

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

            source_doc.close()
            output_xlsx.seek(0)

            make_download_button(
                output_xlsx,
                "extracted.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )