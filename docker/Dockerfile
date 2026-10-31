FROM ubuntu:24.04

LABEL org.opencontainers.image.title="op-build-jdcloud-re-cs-02" \
    org.opencontainers.image.description="JDCloud RE-CS-02 firmware build environment (x86_64 Linux host only)"

# Builder UID/GID. op passes 1000 when the host user is root, because many
# autotools packages in the OpenWrt build refuse to configure as root.
ARG BUILDER_UID=1000
ARG BUILDER_GID=1000
# Apt mirror host. Defaults to USTC; pass an empty value to use upstream Ubuntu.
ARG APT_MIRROR="mirrors.ustc.edu.cn"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1

# Ubuntu 24.04 ships deb822 sources at /etc/apt/sources.list.d/ubuntu.sources.
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
    sed -i "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g; \
    s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" \
    /etc/apt/sources.list.d/ubuntu.sources; \
    fi

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    ack antlr3 asciidoc autoconf automake autopoint binutils bison build-essential \
    bzip2 ca-certificates ccache clang cmake cpio curl device-tree-compiler dos2unix \
    fastjar file flex g++-multilib gawk gcc-multilib genisoimage gettext git gnupg \
    gperf haveged help2man intltool jq less libc6-dev-i386 libelf-dev libfuse-dev \
    libglib2.0-dev libgmp3-dev libltdl-dev libmpc-dev libmpfr-dev libncurses-dev \
    libpython3-dev libreadline-dev libssl-dev libtool libyaml-dev lld llvm locales \
    lrzsz msmtp nano ninja-build p7zip p7zip-full patch pkgconf procps python3 \
    python3-dev python3-docutils python3-netifaces python3-ply python3-pyelftools \
    python3-pip python3-setuptools qemu-utils re2c rsync scons squashfs-tools \
    subversion sudo swig texinfo time tzdata uglifyjs unzip upx-ucl vim wget xmlto \
    xxd xz-utils zlib1g-dev zstd; \
    apt-get autoremove --purge -y; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Optional build-host CAs. Keep these separate from firmware rootfs files.
COPY docker/certs/ /usr/local/share/ca-certificates/op-build/
RUN update-ca-certificates

# Ubuntu 24.04 ships a uid-1000 "ubuntu" account; replace it with a "builder"
# account using the requested UID/GID so bind mounts stay writable.
RUN set -eux; \
    uid="$BUILDER_UID"; gid="$BUILDER_GID"; \
    if [ "$uid" -eq 0 ]; then uid=1000; fi; \
    if [ "$gid" -eq 0 ]; then gid=1000; fi; \
    if getent passwd "$uid" >/dev/null; then \
    userdel -r "$(getent passwd "$uid" | cut -d: -f1)" 2>/dev/null || true; \
    fi; \
    if ! getent group "$gid" >/dev/null; then groupadd -g "$gid" builder; fi; \
    useradd -m -u "$uid" -g "$gid" -s /bin/bash builder; \
    echo 'builder ALL=(ALL) NOPASSWD: ALL' >/etc/sudoers.d/builder; \
    chmod 0440 /etc/sudoers.d/builder; \
    id builder

USER builder
WORKDIR /builder
