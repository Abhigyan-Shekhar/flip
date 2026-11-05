FROM python@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /opt/flip/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /opt/flip/requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/src
WORKDIR /workspace
