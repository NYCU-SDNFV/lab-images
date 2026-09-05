# ghcr.io/nycu-sdnfv/lab-base — SDNFV lab 共用基底（Lab 0~3）
# 2026-09-06: + python3-matplotlib（Lab 1 的 CDF / Pareto 圖在容器內畫）
# 2026-09-06: + python3-os-ken（Lab 2 的 OpenFlow 1.3 controller；Ubuntu 24.04 apt 版 2.8.1，Debian 包不含 sample apps）
# Mininet + Open vSwitch + FRR + os-ken + 量測工具。OVS 由 entrypoint 啟動（容器內沒有 systemd）。
# 一學期一個 tag（115-1）；學生 Dockerfile 只寫 FROM 這個 image。
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      mininet openvswitch-switch openvswitch-common \
      frr frr-pythontools \
      iproute2 iputils-ping net-tools tcpdump iperf3 ethtool \
      python3 python3-matplotlib python3-os-ken procps psmisc ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
LABEL org.opencontainers.image.source="https://github.com/NYCU-SDNFV/lab-images" \
      org.opencontainers.image.description="SDNFV lab base: Mininet + OVS + FRR + iperf3/tc/ethtool"
