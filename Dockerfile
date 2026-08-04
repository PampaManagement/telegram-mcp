FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY . .
RUN uv sync --frozen --no-dev
CMD ["uv", "run", "main.py"]
