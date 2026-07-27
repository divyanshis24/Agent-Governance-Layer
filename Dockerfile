# Agent Governance Layer — build the console, then serve it from the control plane.
FROM node:20-alpine AS console
WORKDIR /console
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY sdk/ ./sdk/
COPY demo/ ./demo/
COPY --from=console /console/dist ./frontend/dist

WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "aegis.main:app", "--host", "0.0.0.0", "--port", "8000"]
