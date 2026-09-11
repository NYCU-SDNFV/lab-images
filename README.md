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

## Multiarch 測試（不更換正式 tag）

`multiarch-test` branch 的 [獨立 workflow](.github/workflows/multiarch-test.yml)
在原生 `ubuntu-24.04`（amd64）與 `ubuntu-24.04-arm`（arm64）runner 分別建置。
兩邊的 [smoke test](tests/smoke.py) 都通過後，才合併並發布：

```text
ghcr.io/nycu-sdnfv/lab-base:115-1-multiarch-test
```

驗證範圍：image 架構、OVS/FRR/量測工具啟動、os-ken import、matplotlib 繪圖、
OVS netdev + Mininet 雙 host 的 ping 與 TCP 傳輸。不是效能 benchmark，也不代表完整 Lab 評分已通過。
`115-1` 與 `latest` 不由此 workflow 更新；既有學生 repo 不需修改。

```sh
docker buildx imagetools inspect ghcr.io/nycu-sdnfv/lab-base:115-1-multiarch-test
docker pull ghcr.io/nycu-sdnfv/lab-base:115-1-multiarch-test
```

Docker 會自動選擇 host 對應的架構，不需強制 `--platform=linux/amd64`。
macOS 的 Linux VM 是否具備 OVS kernel datapath、netem、BBR 等功能仍須另外驗證；
multiarch image 不提供 host kernel modules。apt 未鎖版本，測試 image 的套件也可能比正式版新。
