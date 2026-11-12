FROM python:3.15-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY service.py /app/service.py
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-u", "/app/service.py"]
