FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/smarttest \
    HOME=/tmp

WORKDIR /opt/smarttest
COPY requirements.txt /opt/smarttest/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 65532 runner \
    && useradd --uid 65532 --gid 65532 --no-create-home runner \
    && mkdir -p /workspace /artifacts \
    && chown -R 65532:65532 /workspace /artifacts

COPY packages /opt/smarttest/packages
COPY runner /opt/smarttest/runner

USER 65532:65532
CMD ["python", "-m", "runner.container_entrypoint"]