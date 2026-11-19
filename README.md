# PDF Tools v1.0.6 - flat GitHub Pages edition

All application files are in this repository root. No folders, build step, Node.js server, Python runtime, or backend are required.

## Deploy

1. Extract `pdf-tools-v1.0.6.zip`.
2. Upload every file in the extracted folder directly into the GitHub repository root, replacing the previous release files.
3. In GitHub, open **Settings -> Pages**.
4. Select **Deploy from a branch**, choose `main` and `/(root)`, then save.
5. Open the published site and test with a local PDF.

The PDF file is read through the browser File API and processed in browser memory. It is not uploaded to this repository or to a PDF-processing server. Library scripts and OCR language/runtime assets are downloaded separately from their pinned public CDN locations.

## Version 1.0.6 - XLSX workflow parity

The PDF-to-XLSX extraction sequence has been reworked to mirror the supplied Streamlit/PyMuPDF implementation as closely as possible in a browser:

1. Read native PDF words first. Use OCR only when the page has no native words, as in the original `get_page_words_with_ocr_fallback` function.
2. For OCR pages, render at 4.0x, crop each user-marked column independently, include `max(10, round(zoom * 3))` pixels of padding, convert to grayscale, apply full-range autocontrast, enlarge by 1.75x for narrow crops or 1.5x for wider crops, add a 16-pixel white border, then perform one PSM 6 recognition pass.
3. Retain only first-pass words with confidence >= 10 and keep words only when their centre falls inside the true column boundary.
4. Group words into rows using the original first-matching-row/pairwise-centre algorithm, with a 6-point tolerance for OCR and 4 points for native PDF text.
5. If OCR produced rows, render one 5.0x recovery image and reuse a crop cache for both recovery passes.
6. Recover only blank cells supported by neighbours: left-and-right or above-and-below populated cells. Skip the first column unless it is inferred to be numeric. Only attempt recovery on rows with at least three populated cells.
7. For each targeted blank-cell crop, run grayscale/autocontrast, 4x enlargement and a 24-pixel border. Try PSM 7, 8 and 13 on the clean image first. Run the thresholded variant only if no sufficiently strong result is found. Stop at the same confidence/decimal score thresholds as the Python `_ocr_cell_candidate` helper.
8. Normalize OCR-inserted numeric whitespace, infer decimal precision using the original minimum sample count and 70% consensus, then re-OCR only unpunctuated numeric strings of five or more digits in qualifying columns.
9. Apply the original cell-by-cell numeric conversion rules, preserving dates, ranges, identifiers and ordinary text.

The application retains an additional `OCR Review` worksheet for low-confidence and diagnostic warnings. Arithmetic checks are diagnostic only: they never change any extracted value or any `Page_N` cell.

## Compatibility notes

- Browser OCR uses Tesseract.js 7 with English and French trained data.
- The original Python code invokes Tesseract with `--oem 3`; Tesseract.js uses its LSTM-only engine because the packaged modern `eng` and `fra` trained-data files are LSTM models. PSM modes, language selection, confidence handling, image preparation, column cropping, row grouping and recovery thresholds follow the supplied Python workflow.
- Browser canvas resizing uses the browser's high-quality image resampler; it cannot guarantee pixel-identical output to Pillow's LANCZOS resampler.
- The same OCR model family can still yield slightly different recognition from the Python system Tesseract installation. Compare generated workbooks against representative documents before relying on them operationally.
- Tesseract.js worker/core runtime files are loaded from pinned jsDelivr URLs. The PDF itself remains local.
- PDF.js and PDF-Lib are loaded from pinned CDNs. The app needs an internet connection to load these libraries and OCR runtime assets.
- Full end-to-end browser tests are not claimed by this source package. Syntax, workflow parity, and root-level packaging checks should be completed before deployment; verify all six functions in the target browser.

## Tools

- Redact PDF
- Convert PDF to XLSX
- Convert PDF to Plain Text
- Rotate PDF
- Annotate PDF
- Split PDF

Application version: **1.0.6**.
