FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[web]"

COPY src/ src/
RUN pip install --no-cache-dir .

EXPOSE 8080

ENV ASUSROUTERCONTROL_DATA_DIR=/data

VOLUME ["/data"]

CMD ["uvicorn", "asusroutercontrol.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
