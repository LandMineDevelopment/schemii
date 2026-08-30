FROM postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73

ARG SCHEMII_VERSION=development
ARG SCHEMII_REVISION=development
ARG SCHEMII_SOURCE=https://github.com/LandMineDevelopment/schemii
LABEL org.opencontainers.image.title="Schemii metadata PostgreSQL" \
      org.opencontainers.image.version="$SCHEMII_VERSION" \
      org.opencontainers.image.revision="$SCHEMII_REVISION" \
      org.opencontainers.image.source="$SCHEMII_SOURCE"
RUN printf '%s\n' "$SCHEMII_VERSION" > /opt/schemii-release-version \
    && printf '%s\n' "$SCHEMII_REVISION" > /opt/schemii-release-revision

COPY --chmod=0555 secret-entrypoint.sh /usr/local/bin/schemii-metadata-postgres
ENTRYPOINT ["/usr/local/bin/schemii-metadata-postgres"]
CMD ["postgres"]
