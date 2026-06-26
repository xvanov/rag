"""propkb.ocr -- turn scanned PDFs/images into indexable text.

The docrag indexer skips scanned PDFs ("no_text_extracted") because they're
images, not text. Rather than bolt on a mediocre algorithmic OCR, the pipeline
is: render each PDF page to a PNG here, let **Claude read the PNGs in-session**
(vision OCR -- far better on plats/legal notices), then persist the transcription
as a `<name>.ocr.md` sidecar via ``store.write_text`` so it indexes and becomes
searchable.

This module only does the deterministic half: render to PNG. The reading +
transcription is done by Claude (the /intake skill drives it).
"""

from __future__ import annotations

import os


def render_pdf(pdf_path: str, out_dir: str, dpi_scale: int = 3,
               max_pages: int = 0) -> list[str]:
    """Render each page of ``pdf_path`` to a PNG in ``out_dir``. Returns the PNG
    paths. Uses PyMuPDF (fitz). ``dpi_scale`` 3 ~= 216 dpi (legible for OCR)."""
    import fitz  # PyMuPDF
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    doc = fitz.open(pdf_path)
    out = []
    mat = fitz.Matrix(dpi_scale, dpi_scale)
    for i, page in enumerate(doc):
        if max_pages and i >= max_pages:
            break
        pix = page.get_pixmap(matrix=mat)
        p = os.path.join(out_dir, "%s_p%d.png" % (base, i + 1))
        pix.save(p)
        out.append(p)
    doc.close()
    return out
