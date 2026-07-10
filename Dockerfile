FROM python:3.13-slim

# claude-graph shells out to `git ls-files` to enumerate tracked files
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# The mounted repo at /repo is almost always owned by a different UID than
# this container's root user (bind mount from the host), which trips git's
# dubious-ownership safety check (CVE-2022-24765) and makes every git
# command fail with exit 128. The whole point of this image is running git
# commands against whatever repo the caller mounts, so trust it globally.
RUN git config --system --add safe.directory '*'

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY claude_graph ./claude_graph
RUN pip install --no-cache-dir .

# Mount the target repo here: docker run --rm -v "$PWD:/repo" ghcr.io/mohansagark/claude-graph build
WORKDIR /repo
ENTRYPOINT ["claude-graph"]
CMD ["--help"]
