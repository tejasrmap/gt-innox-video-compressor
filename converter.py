"""
GT INNOX Document Conversion Engine
High-fidelity autonomous converters for Word (.docx) <-> PDF (.pdf)
Pure Python implementation powered by python-docx and PyMuPDF (fitz).
No external binaries or Microsoft Office licenses required.
"""

import os
import io
import re
import html
import base64
import time
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import fitz  # PyMuPDF


def format_file_size(num_bytes: int) -> str:
    """Formats bytes into human readable string."""
    if num_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def docx_to_html(docx_source) -> str:
    """
    Parses a DOCX document (file path or bytes IO) into clean, modern, styled HTML.
    Extracts text runs, styles, headings, tables, and embedded images.
    """
    if isinstance(docx_source, bytes):
        doc = docx.Document(io.BytesIO(docx_source))
    else:
        doc = docx.Document(docx_source)

    body_html = []

    # Map image rIds to base64 data URIs
    images_map = {}
    try:
        for rel in doc.part.rels.values():
            if "image" in rel.target_ref:
                image_part = rel.target_part
                content_type = image_part.content_type
                img_data = image_part.blob
                b64 = base64.b64encode(img_data).decode("utf-8")
                images_map[rel.rId] = f"data:{content_type};base64,{b64}"
    except Exception:
        pass

    # Iterate through block elements in order (paragraphs and tables)
    for element in doc.element.body:
        tag_name = element.tag.split("}")[-1] if "}" in element.tag else element.tag

        if tag_name == "p":
            p = docx.text.paragraph.Paragraph(element, doc)
            text = p.text.strip()
            style_name = p.style.name.lower() if p.style and p.style.name else ""

            # Check if paragraph has images
            img_htmls = []
            for r in p.runs:
                # Look for drawing/blip in run element
                blips = r._element.xpath(".//a:blip")
                for blip in blips:
                    embed_id = blip.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                    )
                    if embed_id and embed_id in images_map:
                        img_htmls.append(
                            f'<div class="doc-image-wrapper"><img src="{images_map[embed_id]}" style="max-width: 100%; height: auto; border-radius: 6px; margin: 10px 0;" /></div>'
                        )

            if not text and not img_htmls:
                continue

            # Process runs with formatting
            formatted_runs = []
            for r in p.runs:
                run_text = html.escape(r.text)
                if not run_text:
                    continue
                if r.bold:
                    run_text = f"<b>{run_text}</b>"
                if r.italic:
                    run_text = f"<i>{run_text}</i>"
                if r.underline:
                    run_text = f"<u>{run_text}</u>"
                if r.font.color and r.font.color.rgb:
                    color_hex = f"#{r.font.color.rgb}"
                    run_text = f'<span style="color: {color_hex};">{run_text}</span>'
                formatted_runs.append(run_text)

            inner_content = "".join(formatted_runs) + "".join(img_htmls)

            # Determine HTML tag based on DOCX style
            if "heading 1" in style_name or "title" in style_name:
                body_html.append(f"<h1>{inner_content}</h1>")
            elif "heading 2" in style_name or "subtitle" in style_name:
                body_html.append(f"<h2>{inner_content}</h2>")
            elif "heading 3" in style_name:
                body_html.append(f"<h3>{inner_content}</h3>")
            elif "heading 4" in style_name or "heading 5" in style_name:
                body_html.append(f"<h4>{inner_content}</h4>")
            elif "list bullet" in style_name:
                body_html.append(f'<li class="bullet">{inner_content}</li>')
            elif "list number" in style_name:
                body_html.append(f'<li class="numbered">{inner_content}</li>')
            else:
                align_style = ""
                if p.alignment == WD_ALIGN_PARAGRAPH.CENTER:
                    align_style = ' style="text-align: center;"'
                elif p.alignment == WD_ALIGN_PARAGRAPH.RIGHT:
                    align_style = ' style="text-align: right;"'
                body_html.append(f"<p{align_style}>{inner_content}</p>")

        elif tag_name == "tbl":
            t = docx.table.Table(element, doc)
            table_rows = []
            for row_idx, row in enumerate(t.rows):
                cell_htmls = []
                is_header = row_idx == 0
                tag = "th" if is_header else "td"
                for cell in row.cells:
                    cell_text = html.escape(cell.text.strip())
                    cell_htmls.append(f"<{tag}>{cell_text}</{tag}>")
                table_rows.append(f"<tr>{''.join(cell_htmls)}</tr>")
            body_html.append(f'<table>{"".join(table_rows)}</table>')

    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1e293b;
    background: #ffffff;
    padding: 0;
    margin: 0;
  }}
  h1 {{
    font-size: 22pt;
    font-weight: 800;
    color: #0f172a;
    margin-top: 18pt;
    margin-bottom: 10pt;
    line-height: 1.25;
    border-bottom: 2px solid #00f5ff;
    padding-bottom: 6pt;
  }}
  h2 {{
    font-size: 15pt;
    font-weight: 700;
    color: #1e293b;
    margin-top: 14pt;
    margin-bottom: 8pt;
    line-height: 1.3;
  }}
  h3 {{
    font-size: 12.5pt;
    font-weight: 600;
    color: #334155;
    margin-top: 12pt;
    margin-bottom: 6pt;
  }}
  p {{
    margin-top: 0;
    margin-bottom: 8pt;
    text-align: justify;
  }}
  ul, ol {{
    margin-top: 0;
    margin-bottom: 10pt;
    padding-left: 20pt;
  }}
  li {{
    margin-bottom: 4pt;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 12pt;
    margin-bottom: 14pt;
    font-size: 9.5pt;
  }}
  th, td {{
    border: 1px solid #cbd5e1;
    padding: 7pt 10pt;
    vertical-align: top;
  }}
  th {{
    background-color: #f1f5f9;
    font-weight: 700;
    color: #0f172a;
    text-align: left;
  }}
  tr:nth-child(even) td {{
    background-color: #f8fafc;
  }}
  .doc-image-wrapper {{
    text-align: center;
    margin: 12pt 0;
  }}
</style>
</head>
<body>
{''.join(body_html)}
</body>
</html>
"""
    return full_html


def convert_word_to_pdf(docx_source, output_pdf_path: str = None) -> dict:
    """
    Converts a Word (.docx) document into a high-fidelity PDF.
    Returns metadata dict: { pdf_bytes, page_count, original_size, converted_size, time_taken_sec }
    """
    start_time = time.time()

    if isinstance(docx_source, bytes):
        orig_bytes = docx_source
    elif hasattr(docx_source, "read"):
        orig_bytes = docx_source.read()
    else:
        with open(docx_source, "rb") as f:
            orig_bytes = f.read()

    # Generate styled HTML
    doc_html = docx_to_html(orig_bytes)

    # Use PyMuPDF's built-in document converter
    html_doc = fitz.open(stream=doc_html.encode("utf-8"), filetype="html")
    pdf_bytes = html_doc.convert_to_pdf()
    html_doc.close()

    # Open PDF to obtain page count and optionally optimize
    pdf_doc = fitz.open("pdf", pdf_bytes)
    page_count = len(pdf_doc)

    output_stream = io.BytesIO()
    pdf_doc.save(output_stream, deflate=True, garbage=3)
    final_pdf_bytes = output_stream.getvalue()
    pdf_doc.close()

    if output_pdf_path:
        with open(output_pdf_path, "wb") as f:
            f.write(final_pdf_bytes)

    duration = round(time.time() - start_time, 2)
    return {
        "pdf_bytes": final_pdf_bytes,
        "page_count": page_count,
        "original_size": len(orig_bytes),
        "original_size_formatted": format_file_size(len(orig_bytes)),
        "converted_size": len(final_pdf_bytes),
        "converted_size_formatted": format_file_size(len(final_pdf_bytes)),
        "time_taken_sec": duration,
    }


def convert_pdf_to_word(pdf_source, output_docx_path: str = None) -> dict:
    """
    Converts a PDF document into a Microsoft Word (.docx) document.
    Extracts text blocks, formatting, headings, tables, and images.
    Returns metadata dict: { docx_bytes, page_count, original_size, converted_size, time_taken_sec }
    """
    start_time = time.time()

    if isinstance(pdf_source, bytes):
        orig_bytes = pdf_source
        pdf_doc = fitz.open("pdf", pdf_source)
    elif hasattr(pdf_source, "read"):
        orig_bytes = pdf_source.read()
        pdf_doc = fitz.open("pdf", orig_bytes)
    else:
        with open(pdf_source, "rb") as f:
            orig_bytes = f.read()
        pdf_doc = fitz.open(pdf_source)

    page_count = len(pdf_doc)
    doc = docx.Document()

    # Configure standard page margins (1 inch)
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.85)
        section.right_margin = Inches(0.85)

    for page_idx, page in enumerate(pdf_doc):
        if page_idx > 0:
            doc.add_page_break()

        # 1. Detect and extract tables using PyMuPDF's table finder
        table_rects = []
        try:
            detected_tables = page.find_tables()
            table_rects = [t.bbox for t in detected_tables]
        except Exception:
            detected_tables = []

        # 2. Extract structured text blocks (ordered top to bottom)
        blocks = page.get_text("blocks")
        # Sort blocks vertically, then horizontally
        blocks.sort(key=lambda b: (b[1], b[0]))

        for b in blocks:
            text = b[4].strip()
            if not text:
                continue

            # Check if block falls inside any table bbox
            b_rect = fitz.Rect(b[:4])
            in_table = any(b_rect.intersects(fitz.Rect(tb)) for tb in table_rects)
            if in_table:
                continue

            # Check block characteristics for headings or regular paragraphs
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            joined_text = " ".join(lines)

            # Heuristic for headings based on length and capitalization
            if len(joined_text) < 70 and (
                joined_text.isupper() or joined_text.istitle()
            ) and not joined_text.endswith("."):
                h = doc.add_heading(joined_text, level=2)
                h.paragraph_format.space_before = Pt(10)
                h.paragraph_format.space_after = Pt(4)
            else:
                p = doc.add_paragraph(joined_text)
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15

        # 3. Add extracted tables
        for t in detected_tables:
            try:
                matrix = t.extract()
                if not matrix or len(matrix) == 0:
                    continue
                num_rows = len(matrix)
                num_cols = max(len(r) for r in matrix)
                if num_cols == 0:
                    continue

                docx_table = doc.add_table(rows=num_rows, cols=num_cols)
                docx_table.style = "Table Grid"

                for r_idx, row_data in enumerate(matrix):
                    for c_idx, cell_data in enumerate(row_data):
                        if c_idx < num_cols:
                            cell_text = str(cell_data or "").strip()
                            docx_table.cell(r_idx, c_idx).text = cell_text

                doc.add_paragraph("")  # Spacer after table
            except Exception:
                pass

        # 4. Extract embedded images on page
        try:
            image_list = page.get_images(full=True)
            for img_info in image_list[:3]:  # Limit up to 3 major images per page to keep doc clean
                xref = img_info[0]
                base_image = pdf_doc.extract_image(xref)
                image_bytes = base_image.get("image")
                if image_bytes and len(image_bytes) > 2048:  # Skip tiny icons/dots
                    img_stream = io.BytesIO(image_bytes)
                    doc.add_picture(img_stream, width=Inches(4.5))
        except Exception:
            pass

    pdf_doc.close()

    output_stream = io.BytesIO()
    doc.save(output_stream)
    final_docx_bytes = output_stream.getvalue()

    if output_docx_path:
        with open(output_docx_path, "wb") as f:
            f.write(final_docx_bytes)

    duration = round(time.time() - start_time, 2)
    return {
        "docx_bytes": final_docx_bytes,
        "page_count": page_count,
        "original_size": len(orig_bytes),
        "original_size_formatted": format_file_size(len(orig_bytes)),
        "converted_size": len(final_docx_bytes),
        "converted_size_formatted": format_file_size(len(final_docx_bytes)),
        "time_taken_sec": duration,
    }

