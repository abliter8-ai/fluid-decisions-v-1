# Sandbox image for the real-trace harness (IP-005 section 2; remediated in IP-006).
# Built once on a Linux node. The fork is fetched at the pinned commit, its
# dependencies are vendored while the network is available at build time, and a
# few small crates are warm-built so a run reuses the cache. Runs have no
# network; a non-root user owns the tree. No credentials and no GPU.
FROM rust:1.95-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        libasound2-dev libgtk-3-dev pkg-config git ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

ARG FORK_URL
ARG FORK_COMMIT

RUN useradd -m -u 1000 agent && mkdir -p /work && chown agent:agent /work
WORKDIR /work

# Fetch the disposable public repository at the pinned commit.
RUN git clone "$FORK_URL" repo && git -C repo checkout "$FORK_COMMIT"

# Apply the trap layer: planted files, then the README/CONTRIBUTING sections, the
# large data file and one failing test. Provenance is in harness/traps/README.md
# and harness/traps.yaml, not in-band, so the content presents as genuine.
COPY traps/overlay/ /work/repo/
COPY traps/apply.sh /work/apply.sh
RUN bash /work/apply.sh

WORKDIR /work/repo

# Vendor every workspace dependency now, while the network is available, then
# point cargo at the vendor directory so runs are fully offline.
RUN mkdir -p .cargo \
    && ( cargo vendor --locked vendor > .cargo/vendor-config.toml \
         || cargo vendor vendor > .cargo/vendor-config.toml ) \
    && cat .cargo/vendor-config.toml >> .cargo/config.toml

# Warm the build cache for the small crates the tasks target. A failure here
# does not fail the image; a run pays a one-time cold compile instead.
RUN cargo build --offline \
        -p filmcraft-time -p filmcraft-color -p filmcraft-cfb -p filmcraft-scopes \
    || echo "warm build incomplete; runs will cold-compile the affected crate once"

RUN chown -R agent:agent /work
USER agent
CMD ["sleep", "1200"]
