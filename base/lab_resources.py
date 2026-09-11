"""Explicit Docker-engine host preparation and Mininet container initialization."""

import argparse
from pathlib import Path
import sys


# The course profile includes Lab 1's 64 MiB root-netns socket buffer ceilings.
HOST_MINIMUMS = {
    "fs.file-max": 10000,
    "net.core.wmem_max": 67108864,
    "net.core.rmem_max": 67108864,
    "net.core.netdev_max_backlog": 5000,
    "net.ipv4.neigh.default.gc_thresh1": 4096,
    "net.ipv4.neigh.default.gc_thresh2": 8192,
    "net.ipv4.neigh.default.gc_thresh3": 16384,
    "net.ipv4.route.max_size": 32768,
    "kernel.pty.max": 20000,
}
OPTIONAL_LEGACY = {
    "net.ipv4.route.max_size":
        "obsolete IPv4 route-cache limit since Linux 3.6; may be absent",
}
# Backlog, neighbour GC, and the legacy route knob are not visible in lab netns.
CONTAINER_HOST_MINIMUMS = {
    name: HOST_MINIMUMS[name]
    for name in (
        "fs.file-max", "net.core.wmem_max", "net.core.rmem_max", "kernel.pty.max"
    )
}
TCP_MINIMUMS = {
    "net.ipv4.tcp_rmem": (10240, 87380, 16777216),
    "net.ipv4.tcp_wmem": (10240, 87380, 16777216),
}
PROCESS_MINIMUMS = {"RLIMIT_NPROC": 8192, "RLIMIT_NOFILE": 16384}

HOST_ADVICE = (
    "Run python3 -m lab_resources host-prepare --profile course explicitly "
    "on the Docker ENGINE host, in its root network namespace "
    "(the one-shot Docker helper needs --privileged --network host). "
    "A required knob missing there requires a compatible host kernel. "
    "Keep normal lab containers network-isolated; see README.md."
)
LOCAL_ADVICE = (
    "Use a privileged, network-isolated lab container with writable "
    "/proc/sys/net/ipv4. Do not use --network host for normal labs."
)


class ResourceError(RuntimeError):
    """A named resource prerequisite or adjustment failed."""


class Sysctl:
    def __init__(self, root="/proc/sys"):
        self.root = Path(root)

    def read(self, name):
        return self.root.joinpath(*name.split(".")).read_text(encoding="ascii")

    def write(self, name, value):
        self.root.joinpath(*name.split(".")).write_text(
            value + "\n", encoding="ascii"
        )


def _read_values(sysctl, name, count):
    text = sysctl.read(name)
    values = tuple(int(part) for part in text.split())
    if len(values) != count or any(value < 0 for value in values):
        raise ValueError(f"expected {count} nonnegative integer(s), got {text!r}")
    return values


def _snapshot(sysctl, minimums, *, allow_legacy=False):
    values = {}
    errors = []
    for name in minimums:
        try:
            values[name] = _read_values(sysctl, name, 1)[0]
        except FileNotFoundError as error:
            if allow_legacy and name in OPTIONAL_LEGACY:
                values[name] = None
            else:
                errors.append(f"{name}: required host prerequisite is missing: {error}")
        except (OSError, ValueError) as error:
            errors.append(f"{name}: cannot read host prerequisite: {error}")
    if errors:
        raise ResourceError("\n".join(errors + [HOST_ADVICE]))
    return values


def _require_minimums(values, minimums):
    errors = [
        f"{name}={value}; course host prerequisite requires >= {minimums[name]}"
        for name, value in values.items()
        if value is not None and value < minimums[name]
    ]
    if errors:
        raise ResourceError("\n".join(errors + [HOST_ADVICE]))


def _raise_sysctl(sysctl, name, minimum, advice):
    operation = "read"
    try:
        current = _read_values(sysctl, name, len(minimum))
        target = tuple(max(old, needed) for old, needed in zip(current, minimum))
        if target == current:
            return current
        operation = f"write {' '.join(map(str, target))}"
        sysctl.write(name, " ".join(map(str, target)))
        operation = "read back"
        actual = _read_values(sysctl, name, len(minimum))
    except (OSError, ValueError) as error:
        raise ResourceError(f"{name}: cannot {operation}: {error}. {advice}") from error
    if any(value < wanted for value, wanted in zip(actual, target)):
        raise ResourceError(
            f"{name}: read back {actual}, below requested {target}. {advice}"
        )
    return actual


def prepare_host(sysctl=None):
    """Preflight every host knob, then raise (never lower) the course settings."""
    if sysctl is None:
        sysctl = Sysctl()
    # No writes until every required knob is readable and well-formed.
    values = _snapshot(sysctl, HOST_MINIMUMS, allow_legacy=True)
    for name, minimum in HOST_MINIMUMS.items():
        if values[name] is not None:
            values[name] = _raise_sysctl(sysctl, name, (minimum,), HOST_ADVICE)[0]
    return values


def verify_host(sysctl=None):
    """Read-only verification in the Docker engine host's root network namespace."""
    if sysctl is None:
        sysctl = Sysctl()
    values = _snapshot(sysctl, HOST_MINIMUMS, allow_legacy=True)
    _require_minimums(values, HOST_MINIMUMS)
    return values


def host_config(sysctl=None):
    """Generate sysctl.d assignments preserving larger values observed now."""
    if sysctl is None:
        sysctl = Sysctl()
    values = _snapshot(sysctl, HOST_MINIMUMS, allow_legacy=True)
    lines = [
        "# SDNFV course profile; install on the Docker ENGINE host.",
        "# Snapshot: max(current value, course minimum). Regenerate after retuning.",
    ]
    for name, minimum in HOST_MINIMUMS.items():
        if values[name] is None:
            lines.append(f"# {name}: unavailable; {OPTIONAL_LEGACY[name]}.")
        else:
            lines.append(f"{name} = {max(values[name], minimum)}")
    return "\n".join(lines) + "\n"


def _raise_process_limits(limits):
    for name, minimum in PROCESS_MINIMUMS.items():
        if name == "RLIMIT_NOFILE":
            advice = (
                "Recreate the lab container with --ulimit nofile=65536:65536; "
                "do not use enormous nofile limits (mnexec -c scans file descriptors)."
            )
        else:
            advice = (
                "Allow at least --ulimit nproc=8192:8192, or keep an inherited "
                "unlimited nproc limit."
            )
        try:
            limit = getattr(limits, name)
            soft, hard = limits.getrlimit(limit)
            # RLIM_INFINITY is commonly -1, not a small finite limit.
            if soft == limits.RLIM_INFINITY or soft >= minimum:
                continue
            new_hard = (
                hard if hard == limits.RLIM_INFINITY or hard >= minimum else minimum
            )
            limits.setrlimit(limit, (minimum, new_hard))
            actual_soft, actual_hard = limits.getrlimit(limit)
        except (OSError, ValueError) as error:
            raise ResourceError(
                f"{name}: cannot raise resource limit: {error}. {advice}"
            ) from error
        if any(
            value != limits.RLIM_INFINITY and value < minimum
            for value in (actual_soft, actual_hard)
        ):
            raise ResourceError(
                f"{name}: read back {(actual_soft, actual_hard)}, "
                f"requires >= {minimum}. {advice}"
            )


def initialize_container(sysctl=None, limits=None):
    """Mininet fixLimits implementation: check host ceilings; adjust local state."""
    if sysctl is None:
        sysctl = Sysctl()
    values = _snapshot(sysctl, CONTAINER_HOST_MINIMUMS)
    _require_minimums(values, CONTAINER_HOST_MINIMUMS)
    if limits is None:
        import resource as limits
    _raise_process_limits(limits)
    for name, minimum in TCP_MINIMUMS.items():
        _raise_sysctl(sysctl, name, minimum, LOCAL_ADVICE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("host-prepare", "host-verify", "host-config"),
        help="explicit host preparation, read-only verification, or sysctl.d output",
    )
    parser.add_argument(
        "--profile", required=True, choices=("course",),
        help="course includes Lab 1's 67108864-byte root-netns buffer ceilings",
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "host-config":
            sys.stdout.write(host_config())
        else:
            values = prepare_host() if args.action == "host-prepare" else verify_host()
            for name, value in values.items():
                if value is None:
                    print(
                        f"{name}: unavailable legacy knob; {OPTIONAL_LEGACY[name]}",
                        file=sys.stderr,
                    )
                else:
                    print(f"{name} = {value} (minimum {HOST_MINIMUMS[name]})")
            print("Course host prerequisites verified.")
    except ResourceError as error:
        parser.exit(1, f"lab_resources: {error}\n")


if __name__ == "__main__":
    main()
