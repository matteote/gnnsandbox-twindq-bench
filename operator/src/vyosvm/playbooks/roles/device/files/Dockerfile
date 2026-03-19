FROM alpine:latest

# Install all required networking and testing tools
RUN apk add --no-cache \
    iputils \
    bind-tools \
    curl \
    netcat-openbsd \
    iperf3 \
    python3 \
    iproute2 \
    tcpdump

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Use entrypoint for runtime configuration
ENTRYPOINT ["/entrypoint.sh"]