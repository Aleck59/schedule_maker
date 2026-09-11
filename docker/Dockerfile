FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir hatchling
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --no-deps -w /wheels .

FROM python:3.12-slim
LABEL org.opencontainers.image.title="Schedule Maker" \
      org.opencontainers.image.description="Модульная веб-система составления расписания"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SM_DATABASE_URL=sqlite:////data/schedule.db
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
RUN mkdir -p /data && useradd -r -u 1000 app && chown -R app /data
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["sh", "-c", "sm db upgrade && sm serve --host 0.0.0.0 --port 8000"]
