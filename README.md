# PDF Tools v1.0.7 - flat GitHub Pages edition

All application files are directly in the repository root. No application subfolders, build step, Node.js server, Python runtime, or backend are required.

## Deploy

1. Extract `pdf-tools-v1.0.7.zip`.
2. Upload every extracted file directly into the GitHub repository root, replacing the previous release files.
3. In GitHub, open **Settings -> Pages**.
4. Select **Deploy from a branch**, choose `main` and `/(root)`, then save.

The PDF itself is read through the browser File API and processed in browser memory. It is not uploaded to a PDF-processing server. The app downloads its pinned PDF, PDF modification, OCR and spreadsheet libraries from their specified CDNs. English and French Tesseract data files are hosted in the repository root.

## Version 1.0.7 - image-resampling parity and OCR diagnostics

- Replaced browser Canvas scaling with a separable Lanczos-3 resampler using pixel-centre mapping and normalized filter weights, matching Pillow's Lanczos approach much more closely.
- Changed enlarged dimensions to Python's `int(width * enlargement)` truncation behaviour rather than JavaScript rounding.
- Kept the grayscale and full-range autocontrast transformations that were previously measured as pixel-identical to Pillow on the test crop.
- Changed Tesseract.js worker initialization from `LSTM_ONLY` to `OEM.DEFAULT`, corresponding to the original Python `--oem 3` request. The supplied modern `eng`/`fra` data still exposes the LSTM engine.
- Removed `user_defined_dpi=300` from worker initialization and per-recognition parameters; the original Python command did not set a forced DPI.
- Added optional OCR diagnostics. When enabled, the XLSX converter downloads a ZIP containing each column's source crop, exact processed Tesseract input, raw Tesseract TSV, per-column timing and pixel/PDF boundary metadata. The PDF itself is not included.
- Preserved the original Streamlit column-by-column extraction, confidence filtering, row grouping, blank-cell recovery, numeric recovery and numeric conversion sequence. Arithmetic checks remain diagnostics only and never modify extracted values.

## OCR diagnostics

In **Convert PDF to XLSX**, select **Capture OCR diagnostic images** before generating. The app downloads the XLSX and a separate `ocr-diagnostics.zip`. The ZIP contains:

- `*_source_crop.png`: the padded column crop before grayscale/autocontrast/enlargement.
- `*_tesseract_input.png`: the exact image passed to Tesseract.js.
- `*_tesseract.tsv`: raw word-level Tesseract output, including confidence and coordinates.
- `column_summary.tsv`: page/column timing and file index.
- `README.txt`: boundary and processing metadata.

Diagnostics increase memory use and add image-encoding time. Leave the option off for normal conversions.

## Compatibility and validation notes

- Browser rendering still uses PDF.js; this release changes OCR preprocessing, not the PDF renderer.
- Lanczos-3 is implemented as separable JavaScript convolution. Its output should be much closer to Pillow than browser Canvas image smoothing, but pixel identity with Pillow is not guaranteed because Pillow uses its own coefficient quantization and implementation details.
- Tesseract.js 7 is configured with OEM.DEFAULT/PSM 6 and English/French data. Browser engine build differences may still affect recognition compared with the native Python Tesseract binary.
- Tesseract.js worker and core/WASM assets are loaded from pinned jsDelivr URLs. PDF.js, PDF-Lib, SheetJS and JSZip are also loaded from pinned CDNs. The PDF content remains local.
- JavaScript syntax, module references, root-only archive contents and Lanczos output against a Pillow reference crop were checked for this release. Full GitHub Pages/browser integration and OCR workbook accuracy still require verification in the deployed site.

Application version: **1.0.7**.
