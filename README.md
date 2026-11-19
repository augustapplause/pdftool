# PDF Tools v1.0.5 - flat GitHub Pages edition

A static browser application for six local PDF workflows: redact, convert to XLSX, extract plain text, rotate, annotate and split. The project is structured so every application file is placed directly in the GitHub repository root.

## Privacy and processing

The selected PDF is read with the browser File API and processed in browser memory. The PDF contents are not uploaded to GitHub or to a PDF-processing server. Tesseract.js runtime files are fetched from pinned public CDNs when OCR is first used; the English and French trained data files are included locally in this repository. OCR requests operate on in-memory canvas images in the browser.

The application does not use server-side processing, a database, analytics or telemetry. Normal requests to the static-host/CDN providers expose ordinary network metadata such as IP address.

## Deploy to GitHub Pages

1. Download and extract `pdf-tools-v1.0.5.zip`.
2. Upload every file from the extracted folder directly into the root of your GitHub repository. Do not create a subfolder.
3. Commit the files to your `main` branch.
4. In GitHub, open **Settings -> Pages**.
5. Choose **Deploy from a branch**, select `main`, and select `/(root)`.
6. Save and open the Pages URL after deployment completes.

No npm, Python, Node server or build step is required.

## Files

All files below are at repository root:

- `index.html` - app shell
- `styles.css` - application styling
- `app.js`, `config.js`, `state.js`, `ui.js`, `vendor.js` - app structure and state
- `pdf.js`, `redact.js`, `xlsx.js`, `xlsx-tool.js`, `ocr.js`, `text.js`, `rotate.js`, `annotate.js`, `split.js` - tool modules
- `eng.traineddata.gz`, `fra.traineddata.gz` - local Tesseract language data
- `README.md`, `RELEASE-NOTES.txt` - documentation

## XLSX conversion workflow

The XLSX converter retains the original workflow:

1. Upload a PDF locally.
2. Select a page.
3. Click the ruler to add/remove vertical column boundaries, or drag an existing blue boundary to move it.
4. Boundaries are saved independently per page.
5. Generate a workbook with one worksheet per page that has marked columns.

Selectable PDF text is preferred. Image-only pages use Tesseract.js 7 with English and French language data. OCR is performed one user-defined column at a time, then words are grouped into table rows by their page coordinates.

### OCR accuracy refinements in v1.0.5

- Scanned pages are rendered at 5.5x, approximating the embedded scan resolution in the supplied sample, while keeping the coordinates tied to PDF page points.
- Column crops receive grayscale histogram autocontrast and high-quality enlargement before OCR.
- First-pass word confidence below 10 is discarded, matching the provided Streamlit implementation.
- Word-level confidence is retained and summarized at cell level.
- Suspicious blank and low-confidence populated cells may receive targeted OCR retries using several segmentation modes and an adaptive-threshold image variant.
- Decimal formatting can be proposed only when the column has a strong decimal-place pattern, the source string is a magnitude outlier, and a high-confidence second image read confirms the digit sequence.
- Leading `=` or `|` OCR artefacts are stripped only when the remaining text is a valid numeric token.

These safeguards are conservative. OCR may still misread a difficult scan; the review sheet helps identify uncertain results instead of presenting every recognition as reliable.

## OCR Review worksheet and arithmetic diagnostics

When review items exist, the workbook contains an additional `OCR Review` worksheet. It reports low-confidence cells, non-numeric results in predominantly numeric columns, possible missing decimal punctuation and supported arithmetic diagnostics.

Arithmetic validation is **diagnostic only**. It never modifies values in the `Page_N` worksheets. The initial arithmetic profile is deliberately limited to a clearly detected Plan Summary Report layout; the app does not assume financial formulas for arbitrary documents. A discrepancy is only a prompt to inspect the source image, not proof that OCR is wrong.

## Limitations

- Tesseract.js runtime and core/WASM are loaded from pinned jsDelivr URLs. English and French trained data are included in the repository. If the CDN is unavailable or blocked, OCR initialization will fail with an error; PDF operations that do not require OCR remain available.
- First OCR use downloads the Tesseract runtime/core and can take time. The language data can be served locally from the repository.
- OCR quality depends on scan quality, skew, font, contrast, boundaries and the number of columns. The supplied source report is used as a regression sample, but other PDF layouts require testing.
- This release has passed source-level JavaScript syntax checks and ZIP/path validation. It has not been verified end-to-end in the user's live GitHub Pages browser. Browser runtime and export tests remain necessary.
- Redaction output is rasterized to remove covered source content; the redacted pages are not text-selectable.

## Version

Application version: **1.0.5**.
