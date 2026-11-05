FROM node@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex@0.166.0 \
    && npm cache clean --force \
    && useradd --uid 501 --create-home --shell /bin/sh agent
COPY agent-controller.mjs shared-host.mjs start.mjs /opt/switch/
ENV HOME=/home/agent SHELL=/bin/sh LANG=C.UTF-8
USER 501:501
WORKDIR /workspace
ENTRYPOINT ["node", "/opt/switch/start.mjs"]
