FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the alfred package
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Copy CLI entry point and solver binary
COPY alfred_cli.py .
COPY dist/alfred_solver bin/alfred_solver

# .env and license/ are NOT bundled — they are mounted from the customer's
# Layer 1 installation root at runtime via docker-compose volumes.

# Default request path (override by mounting a file or setting REQUEST_PATH)
ENV REQUEST_PATH=/app/request/request.json

CMD ["alfred"]
