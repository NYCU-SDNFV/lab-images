# lab-images

SDNFV lab 的容器基底，由 Actions 建好推到 GHCR（public package，pull 免費）。

| image | 內容 | 用途 |
|---|---|---|
| `ghcr.io/nycu-sdnfv/lab-base:115-1` | ubuntu 24.04 + Mininet + OVS + FRR + iperf3/tc/ethtool/tcpdump | Lab 0~3 |
| `ghcr.io/nycu-sdnfv/lab4:115-1` | lab-base + BMv2 + p4c + clang/libbpf | Lab 4（待建）|

學生 repo 的 `Dockerfile` 只需：
```dockerfile
FROM ghcr.io/nycu-sdnfv/lab-base:115-1
```
每學期換一次 tag；改了 `base/` push 到 main 會自動重建。
