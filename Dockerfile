FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY scripts/ scripts/

RUN mkdir -p /data /data/uploaded_images

ENV DATABASE_URL=sqlite:////data/inventory.db
ENV IMAGE_DIR=/data/uploaded_images

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
