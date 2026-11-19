# PDF Tools v1.0.1 — flat GitHub Pages edition

This release is arranged so **every project file is in the repository root**. There are no required folders, no build step, no package installation, and no GitHub Actions workflow.

## Files

Upload every file in this ZIP directly into the top level of your GitHub repository. Do not create `src`, `public`, `assets`, `.github`, or any other folders.

## Publish with GitHub Pages

1. Create or open your GitHub repository.
2. Upload all files from this ZIP into the repository's root (the same level as `index.html`).
3. Commit the files to the `main` branch.
4. Open **Settings → Pages**.
5. Under **Build and deployment**, choose **Deploy from a branch**.
6. Select branch **main** and folder **/(root)**, then Save.
7. Open the Pages URL shown by GitHub after deployment.

No npm, Node.js, GitHub Actions, build command, or nested folder is required.

## Included tools

- Redact PDF
- Convert PDF to XLSX, including per-page draggable column boundaries and PaddleOCR fallback
- Convert PDF to Plain Text
- Rotate PDF
- Annotate PDF
- Split PDF

## Local-only document handling

The selected PDF is read through the browser File API and processed in the user's browser tab. The application contains no application-server upload endpoint and does not send PDF contents to the third-party module CDNs. Third-party JavaScript libraries and OCR model/runtime assets may be downloaded by the browser from their pinned CDN locations; those downloads do not contain the user's PDF. Do not add analytics, telemetry, or remote document-processing integrations without revisiting this privacy promise.

## Browser dependencies

To allow deployment as a flat static site without a build step, the app imports these pinned libraries from CDNs:

- PDF.js 6.5.438 and its matching worker from jsDelivr
- pdf-lib 1.17.1 from esm.sh
- SheetJS/XLSX 0.18.5 from esm.sh
- PaddleOCR.js 0.4.2 from esm.sh; its model/runtime may fetch additional assets when OCR is first used

An internet connection is required to load these libraries and OCR resources. Once loaded, document processing runs locally in the browser. A fully offline deployment would require separately copying and configuring all library, worker, WASM, and OCR model assets; that is not part of this flat-root build.

## Validation notes

This is a static-root restructuring of the v1.0.0 first implementation. All JavaScript modules and the stylesheet are at the repository root, and local imports have been rewritten accordingly. The browser loads dependency modules from pinned CDN URLs. A production browser integration run against real sample PDFs is still required, especially for PaddleOCR initialization, large documents, and complex annotation exports. The redaction export rasterizes pages to remove obscured original content, with the trade-off that text/vector content on those pages is no longer selectable.

## Versioning

Visible application version and source version: **1.0.1**.
