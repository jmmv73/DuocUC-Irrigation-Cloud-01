FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    groupadd --system --gid 10001 iot && \
    useradd --system --uid 10001 --gid 10001 --no-create-home iot
COPY --chown=iot:iot core.py app.py ./
USER 10001:10001
EXPOSE 8080
CMD ["python", "app.py"]
