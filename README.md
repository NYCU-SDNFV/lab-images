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

## Mininet resources：host 準備與 container 初始化分離

Ubuntu 24.04 的 Mininet 2.3.0 將所有 resource 設定包在同一個 catch；
isolated Docker 不能寫 host buffer ceilings，也看不到 backlog／neighbour GC 等 host-only
sysctl。這個 image 在 **build 時只替換 `mininet.util.fixLimits()` 的函式本體**，
保留無參數、回傳 `None` 的 API，改呼叫 `lab_resources.initialize_container()`。
完整 upstream 函式 AST 必須符合已檢查的 2.3.0 結構，否則 build 失敗，不能靜默套錯 patch。

### 一次性、明確 opt-in 的 Docker ENGINE host 準備

以下指令要針對 **實際執行 Docker daemon 的 Linux host／VM**，不是 Docker client、
PVE hypervisor 或筆電的 macOS／Windows kernel。只有短命的管理 helper 使用 `--network host`；
**一般 Lab container 保持預設的獨立 network namespace**。entrypoint 不切換 namespace、
不呼叫 host-prep，也不修改 host sysctl。

```sh
IMAGE=ghcr.io/nycu-sdnfv/lab-base:115-1-resource-test
# 唯一會改 host tunables 的動作；需 host 管理者同意。
docker run --rm --privileged --network host --entrypoint python3 "$IMAGE" \
  -m lab_resources host-prepare --profile course
# Read-only：請在建立 Lab container 前完成，不能拿 container check 取代。
docker run --rm --network host --entrypoint python3 "$IMAGE" \
  -m lab_resources host-verify --profile course
```

`course` profile 的 host 最低值如下；prepare **先讀取／驗證所有必要 knob 才開始寫入**，
只提高低於門檻的整數、保留更大的既有值，並檢查寫入後的值。缺少必要 knob、讀取／寫入
被拒絕或數值不合法均會列出參數與補救方式並失敗；寫入階段若失敗，先前提高的值不會 rollback，
修正權限／環境後可重跑。不可用的 kernel 不能以略過必要設定假裝完成。

| Host sysctl | Course 最低值 | Container 行為 |
|---|---:|---|
| `fs.file-max` | 10000 | 只驗證，不寫 |
| `net.core.wmem_max`、`net.core.rmem_max` | **67108864** | 只驗證，不寫；Lab 1 需要 64 MiB，16 MiB 不算就緒 |
| `net.core.netdev_max_backlog` | 5000 | 不存取；由 host helper 驗證 |
| `net.ipv4.neigh.default.gc_thresh1/2/3` | 4096／8192／16384 | 不存取；由 host helper 驗證 |
| `kernel.pty.max` | 20000 | 只驗證，不寫 |
| `net.ipv4.route.max_size` | 32768（若存在） | 不存取；Linux 3.6 起的 obsolete IPv4 route-cache knob，只有 **ENOENT** 可明確列為 unavailable legacy |

Lab 1 Makefile 原有的 root-netns buffer 設定仍相容，但不足以取代完整 host preparation。
`route.max_size` 存在卻讀寫失敗仍是錯誤；backlog／GC 缺失不是「legacy 可略過」。

### 重開機後持續生效

在 **Docker ENGINE host 的 shell** 產生並安裝設定（不是在遠端 Docker client 的 `/etc`）：

```sh
# 先完整取得成功輸出，避免 helper 失敗時截斷既有設定檔。
config="$(docker run --rm --network host --entrypoint python3 "$IMAGE" \
  -m lab_resources host-config --profile course)" &&
printf '%s\n' "$config" | sudo tee /etc/sysctl.d/90-sdnfv-labs.conf >/dev/null
```

`host-config` 不寫 sysctl；輸出的是 `max(目前值, course 最低值)`，保留產生當下的較大設定，
明列並省略不存在的 legacy knob。Linux host 的 sysctl.d loader 會於開機套用。
這是 **snapshot，不是永遠單調的 loader**：之後若手動調高、換 kernel，應重新產生；
不要用舊 snapshot 覆寫較新的 tuning。檢查其他 sysctl.d 檔案的覆寫順序，重開機後再次執行
`host-verify --profile course`。立即套用請用上面的 monotonic `host-prepare`，而非載入舊檔。
Docker Desktop 的 VM 持久化方式由該平台管理，不能將 client 的設定檔視為 VM 已設定。

### Container 端驗證

```sh
# 刻意不加 --network host；不需啟動 OVS 即可驗證 Mininet 實際入口。
docker run --rm --privileged --ulimit nofile=65536:65536 \
  --entrypoint python3 "$IMAGE" \
  -c 'from mininet.util import fixLimits; fixLimits(); print("Container resource initialization verified")'
```

`fixLimits()` 先驗證 container 可讀的 host prerequisites；任一 buffer 仍是 212992 或
16777216 都會明確失敗，不會嘗試在 container 修 host。接著保留／提高 `RLIMIT_NPROC`
至至少 8192、`RLIMIT_NOFILE` 至至少 16384，**保留 unlimited nproc／hard limits**。
執行慣例仍為 `--ulimit nofile=65536:65536`，不會提高到約十億；`mnexec -c`
會逐個掃描 file descriptors，不能靠巨大 nofile「修正」警告。
僅寫入目前 network namespace 的 `net.ipv4.tcp_rmem`、`tcp_wmem`，各分量至少為
`10240 87380 16777216`，既有較大分量不下降。失敗會拋出具名 `ResourceError`，
不吞例外、不遮蔽 logger，也沒有 `sitecustomize`／no-op patch。

此 container check **不能證明不可見的 backlog／GC 已準備好**，仍需前面的 host-verify。
Focused tests（stdlib `unittest`；除已安裝 Mininet 的整合測試外可跨平台跑）：

```sh
python3 -m unittest discover -s tests -p 'test_resources.py' -v
```

## Multiarch 測試（不更換正式 tag）

`resource-limits-fix` branch 的 [獨立 workflow](.github/workflows/multiarch-test.yml)
在原生 `ubuntu-24.04`（amd64）與 `ubuntu-24.04-arm`（arm64）runner 分別建置。
兩邊的 resource 單元／整合測試、未準備 host 的失敗案例、host preparation，
以及 isolated [smoke test](tests/smoke.py) 都通過後，才合併並發布：

```text
ghcr.io/nycu-sdnfv/lab-base:115-1-resource-test
```

驗證範圍：image 架構、OVS/FRR/量測工具啟動、os-ken import、matplotlib 繪圖、
OVS netdev + Mininet 雙 host 的 ping 與 TCP 傳輸。不是效能 benchmark，也不代表完整 Lab 評分已通過。
`115-1`、`latest` 與先前的 `115-1-multiarch-test` baseline 不由此 workflow 更新。
既有學生 repo 不在此 branch 修改；候選 image 必須配合前面的 host preparation。

```sh
docker buildx imagetools inspect ghcr.io/nycu-sdnfv/lab-base:115-1-resource-test
docker pull ghcr.io/nycu-sdnfv/lab-base:115-1-resource-test
```

Docker 會自動選擇 host 對應的架構，不需強制 `--platform=linux/amd64`。
macOS 的 Linux VM 是否具備 OVS kernel datapath、netem、BBR 等功能仍須另外驗證；
multiarch image 不提供 host kernel modules。apt 未鎖版本，測試 image 的套件也可能比正式版新。
