# Dockerfile optimized for FastAPI + Async Jobs
FROM python:3.13-slim-bookworm

WORKDIR /app

# Environment variables for Python optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    JAVA_OPTS="-Xms512m -Xmx1536m -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -XX:ParallelGCThreads=2 -XX:ConcGCThreads=1 -XX:InitiatingHeapOccupancyPercent=35" \
    PYSPARK_SUBMIT_ARGS="--driver-memory 1g --executor-memory 1g --jars /app/jars/iceberg-spark-runtime-3.5_2.12-1.6.1.jar,/app/jars/iceberg-aws-bundle-1.6.1.jar pyspark-shell" \
    SPARK_DRIVER_MEMORY=1g \
    SPARK_EXECUTOR_MEMORY=1g \
    PYTHONPATH=/app

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      openjdk-17-jdk-headless \
      tini \
      bash \
      build-essential \
      curl \
      ca-certificates \
      gcc \
      g++ \
      libssl-dev \
      libbz2-dev \
      liblzma-dev \
      zlib1g-dev \
      libsnappy-dev \
      libgomp1 \
      pkg-config \
      libpq-dev \
      git \
      procps \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if command -v java >/dev/null 2>&1; then \
      JAVA_BIN="$(readlink -f "$(command -v java)")"; \
      JAVA_HOME_DIR="$(dirname "$(dirname "$JAVA_BIN")")"; \
      mkdir -p /usr/lib/jvm; \
      ln -sfn "$JAVA_HOME_DIR" /usr/lib/jvm/java-17-openjdk-amd64; \
    fi

ENV PATH=$JAVA_HOME/bin:$PATH

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir pyspark==3.5.0 \
 && pip install --no-cache-dir -r /app/requirements.txt

RUN mkdir -p /app/jars \
 && curl -fSL -o /app/jars/iceberg-spark-runtime-3.5_2.12-1.6.1.jar \
      https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/1.6.1/iceberg-spark-runtime-3.5_2.12-1.6.1.jar \
 && curl -fSL -o /app/jars/iceberg-aws-bundle-1.6.1.jar \
      https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/1.6.1/iceberg-aws-bundle-1.6.1.jar

COPY . /app

RUN mkdir -p /app/logs \
 && useradd --create-home --home-dir /home/appuser appuser \
 && chown -R appuser:appuser /app \
 && chmod +x /app/entrypoint.sh || true

USER appuser

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]