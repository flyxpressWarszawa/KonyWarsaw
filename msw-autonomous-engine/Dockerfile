FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MSW_DATA_DIR=/data
WORKDIR /app
RUN useradd --uid 10001 --create-home msw && mkdir /data && chown msw:msw /data
COPY msw_engine /app/msw_engine
COPY legacy /app/legacy
USER 10001:10001
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s CMD python -m msw_engine --health
CMD ["python", "-m", "msw_engine"]
