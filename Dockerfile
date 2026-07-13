FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY db.py .
COPY auth.py .
COPY applog.py .
COPY automation.py .
COPY alert_responses.py .
COPY static/ static/
# Standalone browser tools (enroll-faces.html) served at /tools by server.py.
COPY tools/ tools/

# /app/data holds smarthome.db (SQLite) and logs/server.log. Mount this as a
# Docker volume in production (-v smarthome-data:/app/data) so security logs,
# audit logs, system logs, family members, and device state all survive
# container restarts/redeploys. Without the volume mount everything still
# works, it just resets on every redeploy.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
