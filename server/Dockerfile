# POUNCer run list server - build context is the repository root:
#   docker compose -f server/docker-compose.yml up -d
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pouncer/__init__.py pouncer/runhist.py pouncer/
COPY server/runlist.py server/
ENV DATA=/data CONTEST=CQWW YEARS=3 PORT=8077
VOLUME /data
EXPOSE 8077
# First start: build the list if there is none yet, then serve it.
CMD ["sh", "-c", "[ -f /data/runlist.sqlite ] || python server/runlist.py build; exec python server/runlist.py serve"]
