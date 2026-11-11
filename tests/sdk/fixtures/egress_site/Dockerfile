ARG BASE_IMAGE=fluxion-egress-sandbox-test:local
FROM ${BASE_IMAGE}
USER root
COPY requirements.txt /tmp/egress-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check -r /tmp/egress-requirements.txt
COPY packages /opt/egress-build/packages
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps \
      /opt/egress-build/packages/common /opt/egress-build/packages/contracts \
      /opt/egress-build/packages/logging-kit /opt/egress-build/packages/api-kit \
      /opt/egress-build/packages/platform-sdk \
 && python -m muad_egress_site --install
COPY probe.py /opt/egress-site-probe.py
USER 10001:10001
CMD ["python", "/opt/egress-site-probe.py"]
