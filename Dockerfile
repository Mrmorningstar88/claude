FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY signalbot signalbot
COPY assets assets
# State lives on the /data volume; rendered images are scratch files.
ENV STATE_FILE=/data/signals.json OUT_DIR=/tmp/out PYTHONUNBUFFERED=1
# MODE=relay (watch your channel, default) or MODE=webhook (TradingView -> HTTP)
ENV MODE=relay
CMD ["sh", "-c", "mkdir -p /data && exec python -m signalbot $MODE"]
