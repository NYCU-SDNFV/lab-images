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

## macOS (Docker Desktop)

`lab-base` is published for both `linux/amd64` and `linux/arm64`. Docker
Desktop therefore pulls the native ARM64 image automatically on Apple Silicon
Macs; Intel Macs use AMD64. The image remains a Linux container, run by Docker
Desktop's Linux VM (it is not a native macOS process).

Mininet and Open vSwitch create network namespaces and virtual interfaces, so
start a lab container with the required Linux capabilities:

```sh
docker run --rm -it --privileged \
  -v "$PWD:/workspace" -w /workspace \
  ghcr.io/nycu-sdnfv/lab-base:115-1
```

On Apple Silicon, do not force `--platform linux/amd64`: that would run under
emulation and can make networking experiments substantially slower. Docker
Desktop must be running and allowed sufficient CPU and memory in its Settings.
