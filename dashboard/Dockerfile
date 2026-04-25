FROM python:3.12

# Install gettext for envsubst
RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*

ADD dashboard/build/web/ /dashboard
WORKDIR /dashboard

# Set default Google Maps API key if not provided
# ENV GOOGLE_MAPS_API_KEY=""

# Create entrypoint script to substitute environment variables
RUN echo '#!/bin/bash\n\
# Substitute environment variables in index.html\n\
if [ -f /dashboard/index.html.template ]; then\n\
  envsubst < /dashboard/index.html.template > /dashboard/index.html\n\
  echo "Environment variables substituted in index.html"\n\
fi\n\
# Start the web server\n\
exec python3 -m http.server 8080' > /entrypoint.sh && chmod +x /entrypoint.sh


ENTRYPOINT ["/entrypoint.sh"]
