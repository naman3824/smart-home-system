# Lean image for the climate-control micro-service (api_server.py).
# NOTE: build context is the REPOSITORY ROOT (see docker-compose.yml), so the
# COPY paths below are repo-relative and the root .dockerignore applies.
FROM python:3.11-slim
WORKDIR /app

COPY climate-control/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY climate-control/api_server.py .

# OPENWEATHER_API_KEY (and optionally CITY / LATITUDE / LONGITUDE) must be
# supplied at runtime via the environment — see docker-compose.yml env_file.
# The .env file is intentionally NOT copied into the image.
EXPOSE 3000
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "3000"]
