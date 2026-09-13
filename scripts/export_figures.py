"""Export a PDF's embedded figures so they can actually be read.

A PDF's text layer contains nothing of what a chart shows. Ingest records that
figures exist (see ``pdf_figure_inventory``) but deliberately does not try to
interpret them: reading a bar chart is a visual judgement, and a wrong number
invented here would be indistinguishable from a real one downstream.

So this does the deterministic half only — pull the images out, downscale them
so they are cheap to look at, and print an inventory. A human or a
vision-capable agent then reads them and records what they show in a note.
Nothing here calls a model, and nothing is written into the wiki.

    python scripts/export_figures.py raw/papers/paper.pdf
    python scripts/export_figures.py raw/papers/paper.pdf --out /tmp/figs --max-px 1400
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.config import PROJECT_ROOT

# Below this, an image is almost always a rule, bullet or logo rather than a
# figure worth a visual pass.
MIN_PIXELS = 10_000


def export_figures(pdf_path: str, out_dir: str, max_px: int = 1400) -> list:
    """Write each embedded image to ``out_dir``. Returns a list of records."""
    import pymupdf

    os.makedirs(out_dir, exist_ok=True)
    exported = []

    with pymupdf.open(pdf_path) as doc:
        for page_number, page in enumerate(doc, start=1):
            for index, image in enumerate(page.get_images(full=True)):
                xref = image[0]
                try:
                    pix = pymupdf.Pixmap(doc, xref)
                except Exception:
                    continue
                if pix.width * pix.height < MIN_PIXELS:
                    continue
                # CMYK/alpha colourspaces cannot be written straight to PNG
                if pix.n - pix.alpha > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if max_px and max(pix.width, pix.height) > max_px:
                    scale = max_px / max(pix.width, pix.height)
                    pix = pymupdf.Pixmap(pix, 0)  # drop alpha for a clean rescale
                    pix.shrink(max(1, int(1 / scale).bit_length() - 1))
                name = f"p{page_number:03d}_{index}.png"
                path = os.path.join(out_dir, name)
                pix.save(path)
                exported.append({
                    "page": page_number,
                    "index": index,
                    "file": path,
                    "width": pix.width,
                    "height": pix.height,
                    "kb": os.path.getsize(path) // 1024,
                })
    return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", help="Path to the PDF")
    parser.add_argument("--out", default=None, help="Output directory")
    parser.add_argument("--max-px", type=int, default=1400,
                        help="Downscale so the longest side is at most this (0 = full size)")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"Error: {args.pdf} does not exist.")
        sys.exit(1)

    try:
        import pymupdf  # noqa: F401
    except ImportError:
        print("Error: PyMuPDF is required to export figures.\n"
              "Run through the wrapper (scripts/zurvan) or an interpreter that has it.")
        sys.exit(1)

    stem = os.path.splitext(os.path.basename(args.pdf))[0]
    out_dir = args.out or os.path.join(str(PROJECT_ROOT), "dist", "figures", stem)

    exported = export_figures(args.pdf, out_dir, args.max_px)
    if not exported:
        print(f"No figures found in {args.pdf}.")
        return

    print(f"Exported {len(exported)} figure(s) to {out_dir}\n")
    for record in exported:
        print(f"  page {record['page']:>3}  {record['width']}x{record['height']}"
              f"  {record['kb']:>4} KB  {os.path.basename(record['file'])}")
    print("\nNext: read these, then record what they show in a note —")
    print("  zurvan remember --type finding --title \"Chart data from <paper>\" --body \"...\"")
    print("Do not anchor a claim to a figure: its numbers are not in the source page.")


if __name__ == "__main__":
    main()
