# PDF Tools v1.0.4 — flat GitHub Pages edition

This is a browser-only PDF utility app designed to be hosted as a static site on GitHub Pages. **Every required project file is in this repository root.** Do not create `src`, `public`, `assets`, `.github`, or other folders.

## Deploy to GitHub Pages

1. Extract this ZIP.
2. Upload every file inside it directly to the top level of your GitHub repository (the same level as `index.html`).
3. Commit the files to the `main` branch.
4. Open **Settings → Pages**.
5. Under **Build and deployment**, select **Deploy from a branch**.
6. Select branch **main** and folder **/(root)**, then save.
7. Open the Pages URL shown by GitHub.

No npm, Node.js, build step, backend, or GitHub Actions workflow is required.

## Included tools

- Redact PDF — draws the selected redaction areas into rasterized replacement pages before export. This removes the covered original content, but the exported pages are raster images and their text is no longer selectable.
- Convert PDF to XLSX — click the ruler to add/remove boundaries, or drag blue boundary lines to reposition them. Boundaries are stored independently by page. Selectable PDF text is extracted first; image-only pages use Tesseract.js 7. English and French language data is included. The converter includes targeted blank-cell OCR, numeric validation, and decimal-recovery heuristics.
- Convert PDF to Plain Text — extracts selectable text from all pages. Scanned pages are not OCR'd in this tool.
- Rotate PDF — rotate individual pages in 90-degree increments and export the document.
- Annotate PDF — add and manipulate text, images, shapes, lines, arrows, and pen marks; set colour and thickness; remove a selected image background colour; and export the annotated document.
- Split PDF — select pages, reorder the output sequence, and export the selected pages.

## Local document privacy

The selected PDF is read with the browser File API and processed in the browser tab. The application does not send the PDF or extracted document content to a server or OCR service. Generated files are created in browser memory and downloaded from a local Blob URL.

This is not an air-gapped application: the browser downloads pinned JavaScript libraries and the Tesseract.js worker/WASM runtime from public CDNs. Those requests do not include the user's PDF. English and French Tesseract traineddata files are included in this ZIP and loaded from the same GitHub Pages origin as the application. No analytics or telemetry is included.

## Runtime libraries

The application is a no-build static site and loads the following pinned libraries at runtime:

- PDF.js 6.5.438 and its matching worker from jsDelivr. The PDF.js WASM decoder directory is explicitly configured because scanned PDFs may contain JBIG2 or JPEG 2000 images; omitting `wasmUrl` can cause the page count to load while the scanned page itself renders blank.
- pdf-lib 1.17.1 from jsDelivr.
- SheetJS 0.20.3 from its official CDN distribution.
- Tesseract.js 7.0.0 and tesseract.js-core 7.0.0 from jsDelivr. The Tesseract.js worker is created through its Blob-worker mechanism to avoid a direct cross-origin Worker constructor. The worker and WASM runtime are CDN-hosted; `eng.traineddata.gz` and `fra.traineddata.gz` are local root files.

An internet connection is required to load these runtime libraries and the OCR worker/WASM files. Once loaded, document processing takes place locally. A fully offline deployment would require separately downloading and self-hosting the JS libraries, PDF.js worker, Tesseract worker, and every compatible Tesseract core/WASM build; that is not included here.

## Browser and performance notes

Use a current Chromium-based browser or Firefox. The app uses ES modules, WebAssembly, Canvas, Blob URLs, `createImageBitmap`, and browser file-download APIs. OCR and PDF processing can be memory-intensive, particularly for large scanned documents. Tesseract uses a Web Worker so recognition should not block the main UI thread, but initial runtime/model loading can take time. The language data is cached by Tesseract.js after its first successful load.

The PDF-to-XLSX converter maps native text items to word positions using measured browser font metrics. This is more precise than assigning text by character count, but remains an approximation of the original PyMuPDF word boxes and must be validated against real PDFs, especially where text sits close to a column boundary.

Annotation export uses PDF.js page viewports to map editor coordinates back to PDF coordinates, including pre-rotated pages. Complex source PDFs and rotated image annotations still require visual verification. The redaction export intentionally rasterizes every page to ensure obscured content is not retained underneath a black overlay.

## Validation status

- All JavaScript files pass Node syntax checks.
- The English and French `.traineddata.gz` files are included in the repository root and pass gzip integrity checks.
- The ZIP is intended to contain root-level files only; no nested folders are required.
- A full live-browser integration run against public CDN dependencies and representative PDFs has **not** been completed in the build environment. The first live deployment should therefore be treated as a test release, particularly for Tesseract initialization, model loading, XLSX accuracy, PDF export, and large-document memory use.

## Version

Application version: **1.0.4**.

## v1.0.4 hotfix

The supplied test file (`26 Report.pdf`) is a one-page letter-size scanned PDF. Its page content is a 3400 x 4400 pixel JBIG2-compressed image, with no text layer and no page-level rotation. PDF.js could parse the page tree but did not have the WASM decoder directory configured, so the embedded scan was not painted. Version 1.0.4 sets `wasmUrl` to the matching PDF.js 6.5.438 `wasm/` directory and uses the documented canvas render parameter.

The test PDF's scanned content itself is rotated sideways within the page image; the PDF page rotation metadata is 0 degrees. After rendering is restored, the preview will show that same sideways scan. Use Rotate PDF to correct its orientation if desired.

This hotfix has passed static source checks and ZIP validation. A live Chromium run against the public CDN could not be completed in this environment, so the fix still needs confirmation in the deployed GitHub Pages site.
