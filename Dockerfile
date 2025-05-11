# Use the official Playwright Python image
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Install xvfb for virtual display
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application files
COPY . .

# Install Python dependencies and Playwright browser binaries
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chrome

# Command to run the Telegram bot with xvfb
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24", "python3", "tele.py"]
