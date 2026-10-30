FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 stockalert \
    && useradd --uid 10001 --gid stockalert --no-create-home stockalert \
    && mkdir /app/data \
    && chown stockalert:stockalert /app/data

COPY stock_alert ./stock_alert
USER stockalert
EXPOSE 8765
CMD ["python", "-m", "stock_alert", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
