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
import zipfile
from concurrent.futures import ThreadPoolExecutor
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import fitz  # PyMuPDF
from PIL import Image


def format_file_size(num_bytes: int) -> str:
    """Formats bytes into human readable string."""
    if num_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


import xml.etree.ElementTree as ET


def normalize_docx_bytes(file_bytes: bytes) -> bytes:
    """
    Normalizes a DOCX file in-memory.
    If the document was saved as ISO 'Strict Open XML', translates namespace
    URIs (purl.oclc.org/ooxml) to Transitional (schemas.openxmlformats.org)
    so python-docx and OpenXML parsers can read it without crashing.
    """
    if not file_bytes.startswith(b"PK\x03\x04"):
        return file_bytes

    try:
        in_buf = io.BytesIO(file_bytes)
        out_buf = io.BytesIO()
        modified = False

        with zipfile.ZipFile(in_buf, "r") as in_zip:
            with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
                for item in in_zip.infolist():
                    content = in_zip.read(item.filename)
                    if item.filename.endswith((".xml", ".rels")):
                        text = content.decode("utf-8", errors="ignore")
                        if "purl.oclc.org/ooxml" in text:
                            text = text.replace(
                                "http://purl.oclc.org/ooxml/officeDocument/relationships/",
                                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                            )
                            text = text.replace(
                                "http://purl.oclc.org/ooxml/wordprocessingml/",
                                "http://schemas.openxmlformats.org/wordprocessingml/2006/"
                            )
                            text = text.replace(
                                "http://purl.oclc.org/ooxml/drawingml/",
                                "http://schemas.openxmlformats.org/drawingml/2006/"
                            )
                            content = text.encode("utf-8")
                            modified = True
                    out_zip.writestr(item, content)

        if modified:
            return out_buf.getvalue()
    except Exception:
        pass

    return file_bytes


def _fallback_extract_xml_to_html(zip_bytes: bytes) -> list[str]:
    """
    Fallback parser: directly extracts paragraphs and tables from word/document.xml
    when python-docx cannot parse relationships or non-standard package structures.
    """
    body_html = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
            doc_xml_name = None
            for name in z.namelist():
                if name.endswith("document.xml"):
                    doc_xml_name = name
                    break
            if not doc_xml_name:
                return body_html
            xml_content = z.read(doc_xml_name)

        root = ET.fromstring(xml_content)
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "body":
                for child in elem:
                    ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if ctag == "p":
                        p_text = "".join(t.text for t in child.iter() if (t.tag.split("}")[-1] if "}" in t.tag else t.tag) == "t" and t.text)
                        if p_text.strip():
                            body_html.append(f"<p>{html.escape(p_text.strip())}</p>")
                    elif ctag == "tbl":
                        rows_html = []
                        for row in child:
                            rtag = row.tag.split("}")[-1] if "}" in row.tag else row.tag
                            if rtag == "tr":
                                cells_html = []
                                for cell in row:
                                    c_tag = cell.tag.split("}")[-1] if "}" in cell.tag else cell.tag
                                    if c_tag == "tc":
                                        c_text = "".join(t.text for t in cell.iter() if (t.tag.split("}")[-1] if "}" in t.tag else t.tag) == "t" and t.text)
                                        cells_html.append(f"<td>{html.escape(c_text.strip())}</td>")
                                if cells_html:
                                    rows_html.append(f"<tr>{''.join(cells_html)}</tr>")
                        if rows_html:
                            body_html.append(f"<table>{''.join(rows_html)}</table>")
    except Exception:
        pass
    return body_html


def docx_to_html(docx_source) -> str:
    """
    Parses a DOCX document (file path or bytes IO) into clean, modern, styled HTML.
    Extracts text runs, styles, headings, tables, and embedded images.
    Supports both Transitional and ISO Strict Open XML documents, with direct XML fallback.
    """
    if isinstance(docx_source, bytes):
        raw_bytes = docx_source
    elif hasattr(docx_source, "read"):
        raw_bytes = docx_source.read()
    else:
        with open(docx_source, "rb") as f:
            raw_bytes = f.read()

    # Detect legacy Word 97-2003 binary (.doc) files
    if raw_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError(
            "This file is a legacy Word 97-2003 binary file (.doc). "
            "Please open and save it as a modern Word Document (.docx) in Word or Google Docs before converting."
        )

    # Normalize ISO Strict Open XML namespaces if present
    normalized_bytes = normalize_docx_bytes(raw_bytes)

    body_html = []
    doc = None
    try:
        doc = docx.Document(io.BytesIO(normalized_bytes))
    except Exception:
        # Fallback to direct XML parsing of word/document.xml
        body_html = _fallback_extract_xml_to_html(normalized_bytes)
        if not body_html:
            raise ValueError(
                "Unable to parse Word document. Please ensure it is a valid, uncorrupted .docx file."
            )

    if doc is not None:
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


def parse_page_ranges(range_str: str, max_pages: int) -> list:
    """Parses a string like '1-3, 5, 8' into a 0-indexed list of page indices."""
    if not range_str or range_str.strip().lower() == "all":
        return list(range(max_pages))

    pages = set()
    parts = [p.strip() for p in range_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            sub = part.split("-")
            try:
                start = int(sub[0].strip())
                end = int(sub[1].strip())
                if start > end:
                    start, end = end, start
                for p in range(start, end + 1):
                    if 1 <= p <= max_pages:
                        pages.add(p - 1)
            except ValueError:
                continue
        else:
            try:
                p = int(part.strip())
                if 1 <= p <= max_pages:
                    pages.add(p - 1)
            except ValueError:
                continue
    return sorted(list(pages))


def compress_pdf(pdf_source, quality_level: str = "balanced", output_pdf_path: str = None) -> dict:
    """
    Compresses a PDF document by recompressing embedded images, deflating streams,
    and removing garbage/orphaned objects.
    quality_level: 'extreme' (72-96 dpi, max savings), 'balanced' (150 dpi), 'lossless' (clean + deflate only).
    """
    start_time = time.time()

    if isinstance(pdf_source, bytes):
        orig_bytes = pdf_source
        doc = fitz.open("pdf", orig_bytes)
    elif hasattr(pdf_source, "read"):
        orig_bytes = pdf_source.read()
        doc = fitz.open("pdf", orig_bytes)
    else:
        with open(pdf_source, "rb") as f:
            orig_bytes = f.read()
        doc = fitz.open(pdf_source)

    orig_size = len(orig_bytes)
    page_count = len(doc)

    # Process images if not pure lossless
    if quality_level in ["extreme", "balanced"]:
        max_dim = 1100 if quality_level == "extreme" else 1600
        jpeg_quality = 55 if quality_level == "extreme" else 75

        seen_xrefs = set()
        for page in doc:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    base_img = doc.extract_image(xref)
                    if not base_img or not base_img.get("image"):
                        continue

                    img_data = base_img["image"]
                    if len(img_data) < 4096:
                        continue

                    pil_img = Image.open(io.BytesIO(img_data))
                    orig_w, orig_h = pil_img.size

                    needs_resize = orig_w > max_dim or orig_h > max_dim
                    if needs_resize:
                        scale = min(max_dim / orig_w, max_dim / orig_h)
                        new_w = max(1, int(orig_w * scale))
                        new_h = max(1, int(orig_h * scale))
                        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

                    if pil_img.mode in ("RGBA", "LA", "P"):
                        bg = Image.new("RGB", pil_img.size, (255, 255, 255))
                        if pil_img.mode == "RGBA":
                            bg.paste(pil_img, mask=pil_img.split()[-1])
                        else:
                            bg.paste(pil_img.convert("RGB"))
                        pil_img = bg
                    elif pil_img.mode != "RGB":
                        pil_img = pil_img.convert("RGB")

                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                    new_img_bytes = buf.getvalue()

                    if len(new_img_bytes) < len(img_data):
                        page.replace_image(xref, stream=new_img_bytes)
                except Exception:
                    pass

    out_buf = io.BytesIO()
    doc.save(out_buf, garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
    compressed_bytes = out_buf.getvalue()
    doc.close()

    if len(compressed_bytes) >= orig_size:
        compressed_bytes = orig_bytes

    if output_pdf_path:
        with open(output_pdf_path, "wb") as f:
            f.write(compressed_bytes)

    saved_bytes = max(0, orig_size - len(compressed_bytes))
    saved_percent = round((saved_bytes / orig_size) * 100, 1) if orig_size > 0 else 0.0
    duration = round(time.time() - start_time, 2)

    return {
        "pdf_bytes": compressed_bytes,
        "page_count": page_count,
        "original_size": orig_size,
        "original_size_formatted": format_file_size(orig_size),
        "compressed_size": len(compressed_bytes),
        "compressed_size_formatted": format_file_size(len(compressed_bytes)),
        "saved_bytes": saved_bytes,
        "saved_bytes_formatted": format_file_size(saved_bytes),
        "saved_percent": saved_percent,
        "time_taken_sec": duration,
    }


def merge_pdfs(pdf_sources: list, output_pdf_path: str = None) -> dict:
    """
    Merges multiple PDF documents into a single unified PDF.
    pdf_sources: list of bytes or file paths.
    """
    start_time = time.time()
    merged_doc = fitz.open()
    total_input_bytes = 0
    file_count = 0

    for src in pdf_sources:
        try:
            if isinstance(src, bytes):
                total_input_bytes += len(src)
                sub_doc = fitz.open("pdf", src)
            elif hasattr(src, "read"):
                b = src.read()
                total_input_bytes += len(b)
                sub_doc = fitz.open("pdf", b)
            else:
                total_input_bytes += os.path.getsize(src)
                sub_doc = fitz.open(src)

            merged_doc.insert_pdf(sub_doc)
            sub_doc.close()
            file_count += 1
        except Exception:
            continue

    page_count = len(merged_doc)
    out_buf = io.BytesIO()
    merged_doc.save(out_buf, garbage=3, deflate=True)
    merged_bytes = out_buf.getvalue()
    merged_doc.close()

    if output_pdf_path:
        with open(output_pdf_path, "wb") as f:
            f.write(merged_bytes)

    duration = round(time.time() - start_time, 2)
    return {
        "pdf_bytes": merged_bytes,
        "file_count": file_count,
        "page_count": page_count,
        "original_size": total_input_bytes,
        "original_size_formatted": format_file_size(total_input_bytes),
        "merged_size": len(merged_bytes),
        "merged_size_formatted": format_file_size(len(merged_bytes)),
        "time_taken_sec": duration,
    }


def split_pdf(pdf_source, page_range_str: str = "1", output_pdf_path: str = None) -> dict:
    """
    Extracts pages specified by page_range_str (e.g. '1-3, 5') into a new PDF.
    """
    start_time = time.time()

    if isinstance(pdf_source, bytes):
        orig_bytes = pdf_source
        src_doc = fitz.open("pdf", orig_bytes)
    elif hasattr(pdf_source, "read"):
        orig_bytes = pdf_source.read()
        src_doc = fitz.open("pdf", orig_bytes)
    else:
        with open(pdf_source, "rb") as f:
            orig_bytes = f.read()
        src_doc = fitz.open(pdf_source)

    total_pages = len(src_doc)
    target_pages = parse_page_ranges(page_range_str, total_pages)

    if not target_pages:
        target_pages = list(range(min(1, total_pages)))

    out_doc = fitz.open()
    for p_idx in target_pages:
        out_doc.insert_pdf(src_doc, from_page=p_idx, to_page=p_idx)

    src_doc.close()

    out_buf = io.BytesIO()
    out_doc.save(out_buf, garbage=3, deflate=True)
    split_bytes = out_buf.getvalue()
    out_doc.close()

    if output_pdf_path:
        with open(output_pdf_path, "wb") as f:
            f.write(split_bytes)

    duration = round(time.time() - start_time, 2)
    return {
        "pdf_bytes": split_bytes,
        "extracted_page_count": len(target_pages),
        "total_pages": total_pages,
        "extracted_pages_list": [p + 1 for p in target_pages],
        "original_size": len(orig_bytes),
        "original_size_formatted": format_file_size(len(orig_bytes)),
        "split_size": len(split_bytes),
        "split_size_formatted": format_file_size(len(split_bytes)),
        "time_taken_sec": duration,
    }


def _render_single_page(args):
    page_idx, pdf_bytes, dpi = args
    d = fitz.open("pdf", pdf_bytes)
    p = d[page_idx]
    # alpha=False saves 25% CPU and memory (JPEG does not support alpha)
    pix = p.get_pixmap(dpi=dpi, alpha=False)
    jpg_data = pix.tobytes("jpeg")
    
    # Fast lightweight preview thumbnail (48 dpi, alpha=False)
    thumb_pix = p.get_pixmap(dpi=48, alpha=False)
    thumb_b64 = base64.b64encode(thumb_pix.tobytes("jpeg")).decode("utf-8")
    d.close()
    
    filename = f"page_{page_idx + 1}.jpg"
    thumb_info = {
        "page": page_idx + 1,
        "filename": filename,
        "size_formatted": format_file_size(len(jpg_data)),
        "thumbnail_url": f"data:image/jpeg;base64,{thumb_b64}",
    }
    return page_idx, filename, jpg_data, thumb_info


def pdf_to_jpg(pdf_source, dpi: int = 150, output_path: str = None) -> dict:
    """
    Converts a PDF document into high-resolution JPG images.
    Features parallel multi-core rendering, alpha-free rendering, and ultra-fast ZIP packaging.
    """
    start_time = time.time()

    if isinstance(pdf_source, bytes):
        orig_bytes = pdf_source
    elif hasattr(pdf_source, "read"):
        orig_bytes = pdf_source.read()
    else:
        with open(pdf_source, "rb") as f:
            orig_bytes = f.read()

    doc = fitz.open("pdf", orig_bytes)
    page_count = len(doc)
    doc.close()

    if page_count == 0:
        raise ValueError("PDF document contains no pages.")

    # Single-page fast path
    if page_count == 1:
        _, filename, jpg_data, thumb_info = _render_single_page((0, orig_bytes, dpi))
        final_bytes = jpg_data
        media_type = "image/jpeg"
        is_zip = False
        thumbnails_b64 = [thumb_info]
    else:
        # Multi-page parallel rendering across CPU cores
        max_workers = min(4, page_count)
        tasks = [(i, orig_bytes, dpi) for i in range(page_count)]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            rendered = list(executor.map(_render_single_page, tasks))

        rendered.sort(key=lambda x: x[0])
        page_images = [(r[1], r[2]) for r in rendered]
        thumbnails_b64 = [r[3] for r in rendered]

        # Use ZIP_STORED because JPEGs are already DCT-compressed (avoids wasted CPU deflating)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_STORED) as zf:
            for fname, data in page_images:
                zf.writestr(fname, data)
        final_bytes = zip_buf.getvalue()
        media_type = "application/zip"
        is_zip = True

    if output_path:
        with open(output_path, "wb") as f:
            f.write(final_bytes)

    duration = round(time.time() - start_time, 2)
    return {
        "output_bytes": final_bytes,
        "is_zip": is_zip,
        "media_type": media_type,
        "page_count": page_count,
        "pages_info": thumbnails_b64,
        "original_size": len(orig_bytes),
        "original_size_formatted": format_file_size(len(orig_bytes)),
        "converted_size": len(final_bytes),
        "converted_size_formatted": format_file_size(len(final_bytes)),
        "time_taken_sec": duration,
    }
