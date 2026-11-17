FROM python:3.12-slim

WORKDIR /app
COPY app.py server.py ./
COPY templates/ templates/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir flask waitress

ENV TICKETS_DB=/data/tickets.db
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).status==200 else 1)"

CMD ["python", "server.py"]
