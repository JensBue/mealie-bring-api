FROM python:3.14-alpine AS builder

WORKDIR /app

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock ./

RUN POETRY_VIRTUALENVS_IN_PROJECT=true poetry install --only main --no-root


FROM python:3.14-alpine


WORKDIR /app

EXPOSE 8742/tcp

COPY --from=builder /app/.venv/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY source/ source/

ARG APP_VERSION
ARG GIT_REVISION
ARG BUILD_CREATED
LABEL org.opencontainers.image.title="Mealie Bring API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_REVISION}" \
      org.opencontainers.image.source="https://github.com/JensBue/mealie-bring-api" \
      org.opencontainers.image.description="Mealie integration that forwards shopping list items to Bring" \
      org.opencontainers.image.created="${BUILD_CREATED}"
ENV APP_VERSION=${APP_VERSION}
RUN test -n "$APP_VERSION" && test -n "$GIT_REVISION" && test -n "$BUILD_CREATED"

CMD ["python", "-m", "source.mealie_bring_api"]
