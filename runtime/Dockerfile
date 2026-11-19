# Digest-pinned runtime for the smoothie-gate isolated build and replay worker.
# It carries host tools only; compilers are mounted read-only at /toolchain at run time.
FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8
LABEL org.opencontainers.image.source="https://github.com/arthurwolf/smoothieware-gate-dummy"
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential python3 ruby rake git cmake ca-certificates \
      libc6-i386 lib32stdc++6 lib32gcc-s1 \
 && rm -rf /var/lib/apt/lists/*
