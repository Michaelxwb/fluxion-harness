ARG BASE_IMAGE=fluxion-egress-sandbox-test:local
FROM ${BASE_IMAGE}
USER root
COPY requirements.txt /tmp/egress-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check -r /tmp/egress-requirements.txt
COPY packages /opt/egress-build/packages
COPY apps /opt/egress-build/apps
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps \
      /opt/egress-build/packages/common /opt/egress-build/packages/contracts \
      /opt/egress-build/packages/logging-kit /opt/egress-build/packages/api-kit \
      /opt/egress-build/packages/platform-sdk \
      /opt/egress-build/packages/artifact-store /opt/egress-build/packages/skill-sdk \
      /opt/egress-build/packages/agent-core \
      /opt/egress-build/apps/agent-worker /opt/egress-build/apps/agent-runtime \
 && python -m muad_egress_site --install
COPY probe.py /opt/egress-site-probe.py
COPY core_probe.py /opt/egress-core-probe.py
COPY worker_probe.py /opt/egress-worker-probe.py
COPY runtime_probe.py /opt/egress-runtime-probe.py
COPY worker_recovery_probe.py /opt/egress-worker-recovery-probe.py
COPY runtime_recovery_probe.py /opt/egress-runtime-recovery-probe.py
USER 10001:10001
CMD ["python", "/opt/egress-site-probe.py"]
