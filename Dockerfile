FROM python:3.11-slim

# Install system dependencies including ffmpeg, nodejs (required for yt-dlp JS challenges), and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application files
COPY . .

# Expose port
EXPOSE 5000

# Command to run production WSGI server (supports dynamic PORT on Render/Railway/Fly)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 8 --timeout 300 app:app"]


