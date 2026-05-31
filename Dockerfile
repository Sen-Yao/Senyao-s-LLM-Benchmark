FROM node:22-bookworm-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir fastapi uvicorn[standard] sqlalchemy pydantic pydantic-settings httpx pyyaml cryptography python-multipart
COPY backend ./backend
COPY tasks ./tasks
COPY --from=frontend-build /src/frontend/dist ./frontend/dist
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
