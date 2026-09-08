FROM node:24-alpine AS miaobi-web-builder

WORKDIR /frontend
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY apps/miaobi-web/package.json apps/miaobi-web/pnpm-lock.yaml apps/miaobi-web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/miaobi-web/index.html apps/miaobi-web/tsconfig.json apps/miaobi-web/vite.config.ts ./
COPY apps/miaobi-web/src ./src
RUN pnpm build

FROM semantica-local:0.6.6-cce5ea1

ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG PIP_INDEX_URL=https://pypi.org/simple

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
       ffmpeg file git git-lfs libmagic1 fonts-wqy-zenhei \
       libreoffice-calc libreoffice-impress libreoffice-writer \
       tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
# Install the CPU torchvision build before resolving Docling.  If this is done
# afterwards, pip selects the newest PyPI torchvision first and pulls several
# gigabytes of CUDA wheels even though the base image already carries CPU-only
# torch 2.13.
RUN pip install --no-cache-dir --no-deps \
    torchvision==0.28.0+cpu --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir ".[m4]"
RUN pip install --no-cache-dir "pytest>=8.3" "pytest-asyncio>=0.25" "pytest-cov>=6.0"
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY apps /app/apps
COPY --from=miaobi-web-builder /frontend/dist /app/apps/miaobi-web/dist
COPY packages /app/packages
COPY scripts /app/scripts
COPY demo /app/demo
RUN pip install --no-cache-dir --no-deps -e .

RUN if ! getent group app >/dev/null; then groupadd --gid 10001 app; fi \
    && if ! id -u app >/dev/null 2>&1; then useradd --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app; fi \
    && mkdir -p /app/data \
    && chown -R app:app /app
ENV HOME=/app
USER app

EXPOSE 8080
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
