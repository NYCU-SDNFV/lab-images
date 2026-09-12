# SDNFV Lab Images

Linux container images for the SDNFV networking labs.

The shared base image includes Ubuntu 24.04, Mininet, Open vSwitch, FRRouting,
os-ken, iperf3, iproute2, ethtool, tcpdump, and matplotlib.

## Images

| Image | Platforms | Status |
| --- | --- | --- |
| `ghcr.io/nycu-sdnfv/lab-base:115-1` | `linux/amd64` | Current release |
| `ghcr.io/nycu-sdnfv/lab-base:115-1-resource-test` | `linux/amd64`, `linux/arm64` | Preview with container-aware resource setup |

The setup instructions below apply to the preview image.

## Requirements

- Docker Engine running on a Linux machine or VM.
- Docker Compose for lab repositories that use it.
- Administrator access to prepare the Docker host.
- Host kernel support for the networking features required by your lab.

Containers share the Docker host's kernel; installing an image does not add
kernel modules. Kernel-datapath labs require Open vSwitch support, and some
measurement labs also require netem and BBR.

## Quick start

```sh
IMAGE=ghcr.io/nycu-sdnfv/lab-base:115-1-resource-test
docker pull "$IMAGE"
```

### 1. Prepare the Docker host

Run these commands on the Linux machine or VM running the Docker daemon.
If you use a remote Docker context, prepare the remote host, not the client.

**Host preparation changes system-wide resource limits. Run it only on a
machine you administer.** It raises settings that are below the course
requirements, including the 64 MiB socket-buffer ceilings, without reducing
existing larger values.

```sh
docker run --rm --privileged --network host --entrypoint python3 "$IMAGE" \
  -m lab_resources host-prepare --profile course

docker run --rm --network host --entrypoint python3 "$IMAGE" \
  -m lab_resources host-verify --profile course
```

The second command is read-only. If verification fails, it identifies the
missing or insufficient setting.

### 2. Keep the settings after reboot

On the Docker host, generate and install a sysctl configuration:

```sh
config="$(docker run --rm --network host --entrypoint python3 "$IMAGE" \
  -m lab_resources host-config --profile course)" &&
printf '%s\n' "$config" | sudo tee /etc/sysctl.d/90-sdnfv-labs.conf >/dev/null
```

Regenerate this file after changing host tuning or kernels, and run
`host-verify --profile course` again after a reboot.

### 3. Check container initialization

```sh
docker run --rm --privileged --entrypoint python3 "$IMAGE" \
  -c 'from mininet.util import fixLimits; fixLimits(); print("Resource checks passed")'
```

Keep normal lab containers network-isolated. Only the host preparation and
verification helpers use `--network host`.

For an existing assignment, follow its repository's setup instructions rather
than editing a supplied Dockerfile or Compose file.

## Compatibility

- Use native images for network measurements; CPU emulation can distort results.
- Full course-lab validation currently covers Linux amd64. ARM64 builds have
  resource and basic networking checks; lab-specific kernel requirements still apply.
- Docker Desktop on macOS or Windows has not been validated for this setup.
  A Linux VM running Docker Engine is the supported environment.

## Build locally

```sh
docker build -t sdnfv-lab-base:local base
```
