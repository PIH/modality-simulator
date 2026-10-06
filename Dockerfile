FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# /data holds the acquisition log; a named volume mounted there takes this ownership.
RUN useradd --system --uid 1000 simulator && mkdir -p /data /images && chown simulator /data
USER simulator

VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)"
CMD ["modality-simulator"]
