FROM python:3.11-slim AS dependency-builder
WORKDIR /app
COPY . /app
RUN pip install pipenv && \
    pipenv requirements > requirements.txt && \
    pip install --timeout=60 --retries=5 --target=/site-packages -r requirements.txt

ADD https://github.com/krallin/tini/releases/download/v0.19.0/tini-static /tini
RUN chmod +x /tini

FROM python:3.11-slim
WORKDIR /app
COPY --from=dependency-builder /site-packages /site-packages
COPY --from=dependency-builder /app/ /app/
COPY --from=dependency-builder /tini /tini
RUN apt-get update && apt-get install -y --no-install-recommends gettext-base && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/site-packages
ENTRYPOINT ["/tini", "--"]
CMD ["/app/run.sh"]
