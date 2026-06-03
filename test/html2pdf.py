#!/usr/bin/env python3
"""
HTML/URL to PDF converter (offline)

Usage:
    # HTML file to PDF
    python html2pdf.py input.html output.pdf

    # Webpage URL to PDF (fetch -> HTML -> PDF)
    python html2pdf.py "https://example.com" output.pdf --url

    # With extra CSS
    python html2pdf.py input.html output.pdf --css "body { font-size: 14pt; }"
"""

import argparse
import sys
import tempfile
from pathlib import Path


def fetch_url(url: str) -> str:
    """Fetch webpage content as HTML string."""
    import httpx
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.text


def convert_html_to_pdf(html_path_or_content: str | Path, output_path: str | Path, extra_css: str = "", is_file: bool = True) -> None:
    """Convert HTML (from file path or content string) to PDF using WeasyPrint (offline)."""
    output_path = Path(output_path)

    try:
        from weasyprint import HTML, CSS

        if is_file:
            HTML(filename=str(html_path_or_content)).write_pdf(str(output_path), stylesheets=[CSS(string=extra_css)] if extra_css else [])
        else:
            HTML(string=str(html_path_or_content)).write_pdf(str(output_path), stylesheets=[CSS(string=extra_css)] if extra_css else [])

        print(f"PDF saved to: {output_path}")

    except ImportError:
        print("weasyprint not installed.", file=sys.stderr)
        print("Install with: pip install weasyprint", file=sys.stderr)
        sys.exit(1)


def url_to_pdf(url: str, output_path: str | Path, extra_css: str = "") -> None:
    """Fetch URL, convert to HTML, then to PDF."""
    print(f"Fetching {url} ...")
    html_content = fetch_url(url)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html_content)
        tmp_path = f.name

    try:
        convert_html_to_pdf(tmp_path, output_path, extra_css, is_file=True)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Convert HTML/URL to PDF (offline)")
    parser.add_argument("input", help="Input HTML file path or URL")
    parser.add_argument("output", help="Output PDF file path")
    parser.add_argument("--url", action="store_true", help="Treat input as URL (fetch and convert)")
    parser.add_argument("--css", default="", help="Extra CSS styles (inline)")
    args = parser.parse_args()

    if args.url:
        url_to_pdf(args.input, args.output, args.css)
    else:
        convert_html_to_pdf(args.input, args.output, args.css, is_file=True)


if __name__ == "__main__":
    main()