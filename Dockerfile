FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the alfred package
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Copy CLI entry point
COPY alfred_cli.py .

# Default request path (override by mounting a file or setting REQUEST_PATH)
ENV REQUEST_PATH=/app/request/request.json

CMD ["python", "alfred_cli.py"]
