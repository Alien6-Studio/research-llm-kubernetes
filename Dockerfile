FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY service.py /app/service.py
COPY v2/__init__.py v2/network_policy.py /app/v2/
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-u", "/app/service.py"]
