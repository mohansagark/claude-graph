FROM python:3.13-slim

# claude-graph shells out to `git ls-files` to enumerate tracked files
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY claude_graph ./claude_graph
RUN pip install --no-cache-dir .

# Mount the target repo here: docker run --rm -v "$PWD:/repo" ghcr.io/mohansagark/claude-graph build
WORKDIR /repo
ENTRYPOINT ["claude-graph"]
CMD ["--help"]
