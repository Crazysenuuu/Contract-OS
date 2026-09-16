FROM python:3.11-slim

WORKDIR /app

# Install system dependencies.
# libpango/libcairo/libgdk-pixbuf are required at runtime by WeasyPrint,
# which powers agreement PDF rendering, analytics PDF export, and PDF
# watermark stamping (spec 1.23 / 2.07). Note: wkhtmltopdf is NOT installed
# — it was removed from Debian 13 (trixie, the current python:*-slim base)
# and is EOL upstream; app.services.pdf_generator keeps the wkhtmltopdf
# fallback for environments that provide it themselves.
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libffi-dev \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
