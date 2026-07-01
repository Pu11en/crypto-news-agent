FROM python:3.12-slim
RUN echo "hello from crypto-intel" && python --version
COPY config/config.yaml /data/.hermes/config.yaml
RUN mkdir -p /data/.hermes/plugins/model-providers/bai /data/.hermes/plugins/crypto-intel /data/crypto-intel
COPY plugins/model-providers/bai/ /data/.hermes/plugins/model-providers/bai/
COPY plugins/crypto-intel/ /data/.hermes/plugins/crypto-intel/
RUN pip install --no-cache-dir "sqlalchemy>=2.0" "requests>=2.31" "httpx>=0.27"
EXPOSE 8080
CMD ["python", "-c", "print('crypto-intel-pipeline-v2 running'); import time; time.sleep(3600)"]
