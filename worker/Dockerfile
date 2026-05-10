FROM python:3.11-slim

WORKDIR /app

# Torch CPU-only — varianta CUDA ar fi ~2.5 GB inutil în Docker fără GPU
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
