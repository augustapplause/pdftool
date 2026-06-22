import fitz
import pandas as pd
import streamlit as st
from io import BytesIO
from PIL import Image
from streamlit_drawable_canvas import st_canvas

st.set_page_config(
    page_title="PDF Redactor / PDF to XLSX",
    layout="wide"
)

st.title("PDF Redactor / PDF to XLSX")

task = st.radio(
    "Choose Function",
    ["Redact PDF", "Convert PDF to XLSX"],
    horizontal=True
)

uploaded = st.file_uploader(
    "Upload PDF",
    type=["pdf"]
)

if uploaded is None:
    st.stop()

pdf_bytes = uploaded.getvalue()

try:
    doc = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )
except Exception as e:
    st.error(f"Unable to open PDF: {e}")
    st.stop()

st.success(f"Loaded PDF ({len(doc)} pages)")

zoom = 1.5

if "redactions" not in st.session_state:
    st.session_state.redactions = {}

if "columns" not in st.session_state:
    st.session_state.columns = {}

page_num = st.selectbox(
    "Select Page",
    list(range(len(doc))),
    format_func=lambda x: f"Page {x + 1}"
)

page = doc[page_num]

pix = page.get_pixmap(
    matrix=fitz.Matrix(zoom, zoom),
    alpha=False
)

img = Image.open(
    BytesIO(
        pix.tobytes("png")
    )
)

st.session_state.redactions.setdefault(page_num, [])
st.session_state.columns.setdefault(page_num, [])

col1, col2 = st.columns([4, 1])

with col2:
    st.subheader("Tools")

    if st.button("Clear Current Page"):
        st.session_state.redactions[page_num] = []
        st.session_state.columns[page_num] = []
        st.rerun()

    if task == "Redact PDF":
        st.info(
            "Draw black rectangles over content to redact."
        )
    else:
        st.info(
            "Draw vertical blue lines to mark column boundaries."
        )

with col1:

    if task == "Redact PDF":

        canvas_result = st_canvas(
            fill_color="rgba(0,0,0,0.8)",
            stroke_width=2,
            stroke_color="#000000",
            background_image=img,
            update_streamlit=True,
            height=img.height,
            width=img.width,
            drawing_mode="rect",
            key=f"redact_{page_num}"
        )

        if st.button("Save Redactions For This Page"):

            rects = []

            if (
                canvas_result.json_data
                and "objects" in canvas_result.json_data
            ):
                for obj in canvas_result.json_data["objects"]:

                    if obj.get("type") == "rect":

                        rects.append(
                            {
                                "x": obj["left"],
                                "y": obj["top"],
                                "w": obj["width"] * obj.get("scaleX", 1),
                                "h": obj["height"] * obj.get("scaleY", 1)
                            }
                        )

            st.session_state.redactions[page_num] = rects

            st.success(
                f"Saved {len(rects)} redaction rectangle(s)"
            )

        if st.button("Generate Redacted PDF"):

            redacted_doc = fitz.open(
                stream=pdf_bytes,
                filetype="pdf"
            )

            for pnum, rects in st.session_state.redactions.items():

                p = redacted_doc[pnum]

                for r in rects:

                    x0 = r["x"] / zoom
                    y0 = r["y"] / zoom
                    x1 = (r["x"] + r["w"]) / zoom
                    y1 = (r["y"] + r["h"]) / zoom

                    rect = fitz.Rect(
                        x0,
                        y0,
                        x1,
                        y1
                    )

                    p.add_redact_annot(
                        rect,
                        fill=(0, 0, 0)
                    )

                p.apply_redactions()

            output_pdf = BytesIO()

            redacted_doc.save(output_pdf)
            redacted_doc.close()

            output_pdf.seek(0)

            st.download_button(
                label="Download Redacted PDF",
                data=output_pdf.getvalue(),
                file_name="redacted.pdf",
                mime="application/pdf"
            )

    else:

        canvas_result = st_canvas(
            fill_color="rgba(0,0,255,0.15)",
            stroke_width=3,
            stroke_color="#0000FF",
            background_image=img,
            update_streamlit=True,
            height=img.height,
            width=img.width,
            drawing_mode="line",
            key=f"xlsx_{page_num}"
        )

        if st.button("Save Columns For This Page"):

            columns = []

            if (
                canvas_result.json_data
                and "objects" in canvas_result.json_data
            ):
                for obj in canvas_result.json_data["objects"]:

                    if obj.get("type") == "line":

                        x1 = obj["left"] + obj["x1"]
                        x2 = obj["left"] + obj["x2"]

                        midpoint = (x1 + x2) / 2

                        columns.append(midpoint)

            columns = sorted(columns)

            st.session_state.columns[page_num] = columns

            st.success(
                f"Saved {len(columns)} column boundary line(s)"
            )

        def group_words_into_rows(words, y_tolerance=4):

            rows = []

            for word in sorted(
                words,
                key=lambda w: (w[1], w[0])
            ):

                x0, y0, x1, y1, text, *_ = word

                y_mid = (y0 + y1) / 2

                placed = False

                for row in rows:

                    if abs(row["y"] - y_mid) <= y_tolerance:

                        row["words"].append(word)

                        row["y"] = (
                            row["y"] + y_mid
                        ) / 2

                        placed = True
                        break

                if not placed:

                    rows.append(
                        {
                            "y": y_mid,
                            "words": [word]
                        }
                    )

            return rows

        def assign_word_to_column(
            word,
            boundaries
        ):

            x0, y0, x1, y1, text, *_ = word

            x_mid = (x0 + x1) / 2

            for i in range(
                len(boundaries) - 1
            ):
                if (
                    boundaries[i]
                    <= x_mid
                    < boundaries[i + 1]
                ):
                    return i

            return None

        if st.button("Generate XLSX"):

            source_doc = fitz.open(
                stream=pdf_bytes,
                filetype="pdf"
            )

            output_xlsx = BytesIO()

            with pd.ExcelWriter(
                output_xlsx,
                engine="openpyxl"
            ) as writer:

                wrote_sheet = False

                for pnum in range(
                    len(source_doc)
                ):

                    p = source_doc[pnum]

                    raw_columns = (
                        st.session_state.columns.get(
                            pnum,
                            []
                        )
                    )

                    if not raw_columns:
                        continue

                    pdf_columns = sorted(
                        [
                            x / zoom
                            for x in raw_columns
                        ]
                    )

                    boundaries = (
                        [0]
                        + pdf_columns
                        + [p.rect.width]
                    )

                    words = p.get_text(
                        "words"
                    )

                    rows = group_words_into_rows(
                        words
                    )

                    table_rows = []

                    for row in rows:

                        cells = [
                            ""
                        ] * (
                            len(boundaries) - 1
                        )

                        for word in sorted(
                            row["words"],
                            key=lambda w: w[0]
                        ):

                            col_index = (
                                assign_word_to_column(
                                    word,
                                    boundaries
                                )
                            )

                            if (
                                col_index
                                is not None
                            ):
                                cells[col_index] = (
                                    cells[col_index]
                                    + " "
                                    + word[4]
                                ).strip()

                        if any(
                            c.strip()
                            for c in cells
                        ):
                            table_rows.append(
                                cells
                            )

                    if table_rows:

                        df = pd.DataFrame(
                            table_rows
                        )

                        df.to_excel(
                            writer,
                            sheet_name=f"Page_{pnum+1}",
                            index=False,
                            header=False
                        )

                        wrote_sheet = True

                if not wrote_sheet:

                    pd.DataFrame(
                        [[
                            "No columns marked or no extractable text found."
                        ]]
                    ).to_excel(
                        writer,
                        sheet_name="Result",
                        index=False,
                        header=False
                    )

            source_doc.close()

            output_xlsx.seek(0)

            st.download_button(
                label="Download XLSX",
                data=output_xlsx.getvalue(),
                file_name="extracted.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
