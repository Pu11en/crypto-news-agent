FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code.
COPY accounts.txt .
COPY src/ ./src/

# The Railway volume mounts at /data; create it so a local `docker run`
# without a volume still works (SQLite will just write there).
RUN mkdir -p /data

# python-telegram-bot v21 is async — run unbuffered so logs stream live.
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/bot.py"]
