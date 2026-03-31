FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the alfred package
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Copy CLI entry point and master data
COPY alfred_cli.py .
COPY data/master data/master

# Copy solver binary — must be present at bin/alfred_solver in the app bundle.
# Build the binary first: ./scripts/build_binary.sh --platform linux
# Then regenerate the bundle: ./scripts/build_prod.sh --type prod --binary-linux dist/alfred_solver_linux
COPY bin/alfred_solver bin/alfred_solver
RUN chmod +x bin/alfred_solver

# .env and license/ are NOT bundled — they are mounted from the customer's
# Layer 1 installation root at runtime via docker-compose volumes.

# Default request path (used by CLI runs; ignored by the HTTP server)
ENV REQUEST_PATH=/app/request/request.json

EXPOSE 8000

# HTTP server: POST /run to submit a request, GET /health for liveness.
# To run as a one-shot CLI instead: docker run ... alfred --request <path>
CMD ["uvicorn", "alfred.server:app", "--host", "0.0.0.0", "--port", "8000"]
