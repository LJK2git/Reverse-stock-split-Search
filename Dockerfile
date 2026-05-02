# Use lightweight Python image
FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory inside container
WORKDIR /app

# Install system dependencies + Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libgtk-3-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*



RUN git clone https://github.com/LJK2git/Reverse-stock-split-Search.git .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
# Copy the rest of the project files
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh


# Run the script
CMD ["/entrypoint.sh"]
