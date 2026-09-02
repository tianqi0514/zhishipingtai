FROM semantica-local:0.6.6-cce5ea1

ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

USER root
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -exec \
    sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" {} +

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
       ffmpeg file git libmagic1 fonts-wqy-zenhei \
       libreoffice-calc libreoffice-impress libreoffice-writer \
       tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir ".[m4]"
RUN pip install --no-cache-dir "pytest>=8.3" "pytest-asyncio>=0.25" "pytest-cov>=6.0"
# PyPI's torchvision wheel can resolve to a CUDA build while the Semantica
# base image carries CPU-only torch. Force the matching CPU wheel for Docling.
RUN pip install --no-cache-dir --force-reinstall --no-deps \
    torchvision==0.28.0+cpu --index-url https://download.pytorch.org/whl/cpu
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY apps /app/apps
COPY packages /app/packages
RUN pip install --no-cache-dir --no-deps -e .

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8080
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
