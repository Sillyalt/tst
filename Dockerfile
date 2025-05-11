# Use the official Playwright Python image
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set working directory
WORKDIR /app

# Copy application files
COPY . .

# Install Python dependencies and Playwright browser binaries
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chrome

# Command to run the Telegram bot
CMD ["python3", "tele.py"]
