# ARG lets the Ansible task inject the exact agent image version at build time.
# Override with: docker build --build-arg AGENT_IMAGE=<region>-docker.pkg.dev/<project>/<repo>/traffic-agent:<version>
ARG AGENT_IMAGE=traffic-agent:latest

# Pull the pre-built traffic-agent binary from the carrier image.
# Cloud Build produces this image via traffic-agent/cloudbuild.j2.
# No Go compiler or iperf3 needed on the networkvm — Docker handles it.
FROM ${AGENT_IMAGE} AS agent

FROM alpine:latest

# Install networking tools (iperf3 and python3 removed — traffic-agent replaces both).
RUN apk add --no-cache \
    iputils \
    bind-tools \
    curl \
    netcat-openbsd \
    iproute2 \
    tcpdump

# Copy the traffic-agent binary from the carrier image.
COPY --from=agent /traffic-agent /usr/local/bin/traffic-agent
RUN chmod +x /usr/local/bin/traffic-agent

# Copy entrypoint script.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
