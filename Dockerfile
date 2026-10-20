FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py wsgi.py gunicorn.conf.py index.html visuals.py ./
COPY assets/ ./assets/
EXPOSE 8080
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:application"]
