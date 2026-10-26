# TEST-ONLY offline candidate; local image inputs are asserted by the build script.
ARG PI_DONOR
ARG PPT_BASE
FROM ${PI_DONOR} AS donor
FROM ${PPT_BASE}
USER 0
LABEL io.ppt-service.candidate="offline-test-only" io.ppt-service.production="false"
COPY --from=donor /usr/local/bin/node /usr/local/bin/node
COPY --from=donor /opt/pi/ /opt/pi/
COPY source/ /opt/pi-candidate/source/
COPY harness/ /opt/pi-candidate/harness/
COPY SHA256SUMS /opt/pi-candidate/SHA256SUMS
ENV PYTHONPATH=/opt/pi-candidate/source PYTHONDONTWRITEBYTECODE=1 PI_OFFLINE=1 HOME=/tmp/candidate-home
WORKDIR /work
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "-I", "/opt/pi-candidate/harness/smoke.py"]
CMD []
