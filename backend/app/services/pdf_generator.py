import io
import subprocess
import tempfile
from pathlib import Path


def html_to_pdf_weasyprint(html_content: str) -> bytes:
    """Convert HTML to PDF using weasyprint."""
    from weasyprint import HTML
    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes


def html_to_pdf_wkhtmltopdf(html_content: str) -> bytes:
    """Convert HTML to PDF using wkhtmltopdf (if available)."""
    with tempfile.NamedTemporaryFile(
        suffix=".html", mode="w", delete=False
    ) as f:
        f.write(html_content)
        html_path = f.name

    try:
        result = subprocess.run(
            [
                "wkhtmltopdf",
                "--quiet",
                "--page-size", "A4",
                "--margin-top", "25mm",
                "--margin-bottom", "25mm",
                "--margin-left", "25mm",
                "--margin-right", "25mm",
                html_path,
                "-",
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"wkhtmltopdf failed: {result.stderr.decode()}"
            )
        return result.stdout
    finally:
        Path(html_path).unlink(missing_ok=True)


def html_to_pdf(html_content: str) -> bytes:
    """
    Convert HTML to PDF. Tries weasyprint first, then wkhtmltopdf.
    Raises RuntimeError if neither is available.
    """
    try:
        return html_to_pdf_weasyprint(html_content)
    except (ImportError, OSError):
        # ImportError: weasyprint not installed. OSError: installed but the
        # system Pango/cairo libraries are missing (dev machines without
        # them) — degrade to the next renderer instead of crashing.
        pass

    try:
        return html_to_pdf_wkhtmltopdf(html_content)
    except (FileNotFoundError, RuntimeError):
        pass

    raise RuntimeError(
        "No PDF generator available. "
        "Install weasyprint or wkhtmltopdf."
    )
