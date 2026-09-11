"""Build-time installation; refuse an unreviewed upstream fixLimits structure."""

import ast
import importlib.util
from pathlib import Path
import shutil
import sys
import sysconfig


# Match the complete AST, ignoring only formatting/comments, of Mininet 2.3.0
# (Ubuntu 24.04 mininet 2.3.0-1.1). Keep the resource mapping under explicit review.
EXPECTED_FIX_LIMITS = '''
def fixLimits():
    "Fix ridiculously small resource limits."
    debug( "*** Setting resource limits\\n" )
    try:
        rlimitTestAndSet( RLIMIT_NPROC, 8192 )
        rlimitTestAndSet( RLIMIT_NOFILE, 16384 )
        sysctlTestAndSet( 'fs.file-max', 10000 )
        sysctlTestAndSet( 'net.core.wmem_max', 16777216 )
        sysctlTestAndSet( 'net.core.rmem_max', 16777216 )
        sysctlTestAndSet( 'net.ipv4.tcp_rmem', '10240 87380 16777216' )
        sysctlTestAndSet( 'net.ipv4.tcp_wmem', '10240 87380 16777216' )
        sysctlTestAndSet( 'net.core.netdev_max_backlog', 5000 )
        sysctlTestAndSet( 'net.ipv4.neigh.default.gc_thresh1', 4096 )
        sysctlTestAndSet( 'net.ipv4.neigh.default.gc_thresh2', 8192 )
        sysctlTestAndSet( 'net.ipv4.neigh.default.gc_thresh3', 16384 )
        sysctlTestAndSet( 'net.ipv4.route.max_size', 32768 )
        sysctlTestAndSet( 'kernel.pty.max', 20000 )
    except Exception:
        warn( "*** Error setting resource limits. "
              "Mininet's performance may be affected.\\n" )
'''

REPLACEMENT_FIX_LIMITS = '''def fixLimits():
    "Fix ridiculously small resource limits."
    from lab_resources import initialize_container
    debug( "*** Setting container resource limits\\n" )
    initialize_container()
'''


def patch_source(source):
    tree = ast.parse(source)
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "fixLimits"
    ]
    expected = ast.dump(ast.parse(EXPECTED_FIX_LIMITS).body[0])
    if len(functions) != 1 or ast.dump(functions[0]) != expected:
        raise ValueError(
            "Unexpected mininet.util.fixLimits structure; expected Mininet 2.3.0 "
            "(Ubuntu 2.3.0-1.1). Refusing to patch: review the host/local resource "
            "mapping before supporting this upstream version."
        )
    function = functions[0]
    lines = source.splitlines(keepends=True)
    patched = (
        "".join(lines[:function.lineno - 1])
        + REPLACEMENT_FIX_LIMITS
        + "".join(lines[function.end_lineno:])
    )
    compile(patched, "mininet.util (patched)", "exec")
    return patched


def main():
    spec = importlib.util.find_spec("mininet.util")
    if spec is None or spec.origin is None:
        raise RuntimeError("Cannot locate installed mininet.util; install Mininet first.")
    path = Path(spec.origin)
    patched = patch_source(path.read_text(encoding="utf-8"))
    destination = Path(sysconfig.get_path("purelib")) / "lab_resources.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("lab_resources.py"), destination)
    path.write_text(patched, encoding="utf-8")
    print(f"Installed {destination}; patched only {path}:fixLimits")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, SyntaxError, ImportError, RuntimeError) as error:
        sys.exit(f"Mininet resource patch failed: {error}")
