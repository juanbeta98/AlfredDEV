# Dockerfile.builder — Builds the alfred_solver binary for Linux amd64.
#
# This is NOT the application runtime image (that's Dockerfile).
# This image runs PyInstaller inside a linux/amd64 container to produce
# a Linux-native alfred_solver binary, even when building from macOS ARM64.
#
# Usage (invoked automatically by scripts/build_binary.sh --platform linux):
#
#   docker build --platform linux/amd64 \
#     -f Dockerfile.builder \
#     -t alfred-builder .
#
#   docker run --rm \
#     --platform linux/amd64 \
#     -v "$(pwd)/dist:/output" \
#     alfred-builder
#
# Output: dist/alfred_solver_linux on the host.

FROM --platform=linux/amd64 python:3.11-slim

WORKDIR /build

# System deps required by PyInstaller bootloader and C-extension packages (e.g. pyarrow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    binutils \
    && rm -rf /var/lib/apt/lists/*

# Layer cache: copy dependency declarations before source so pip install
# layers survive code-only changes.
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Install the alfred package so PyInstaller can resolve all internal imports
COPY src/ src/
RUN pip install --no-cache-dir -e .

RUN pip install --no-cache-dir pyinstaller

COPY alfred_solver.spec .
RUN pyinstaller alfred_solver.spec --distpath /build/dist/

# On container run, copy the binary to /output (mounted as dist/ on the host)
ENTRYPOINT ["/bin/sh", "-c", "cp /build/dist/alfred_solver /output/alfred_solver_linux && echo 'Binary written to /output/alfred_solver_linux'"]
