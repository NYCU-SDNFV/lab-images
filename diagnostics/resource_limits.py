import argparse
import ast
import inspect
import json
import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

from mininet import util


def error_details(error):
    return {
        "type": type(error).__name__,
        "message": str(error),
        "errno": getattr(error, "errno", None),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--expect", choices=("warning", "clean", "observe"), required=True)
    args = parser.parse_args()
    architecture = subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True,
    ).strip()
    if architecture != args.architecture:
        raise RuntimeError(f"Expected {args.architecture}, got {architecture}")
    source = textwrap.dedent(inspect.getsource(util.fixLimits))
    if args.phase == "isolated-before":
        print("INSTALLED_SOURCE", util.__file__)
        print(subprocess.check_output(
            ["dpkg-query", "-W", "mininet"], text=True,
        ).strip())
        print(source)
        print(inspect.getsource(util.sysctlTestAndSet))
        print(inspect.getsource(util.rlimitTestAndSet))

    state = {
        "phase": args.phase,
        "architecture": architecture,
        "network_namespace": os.readlink("/proc/self/ns/net"),
        "nofile": resource.getrlimit(resource.RLIMIT_NOFILE),
        "nproc": resource.getrlimit(resource.RLIMIT_NPROC),
    }
    exceptions = []

    def trace(frame, event, argument):
        if (event == "exception" and frame.f_code.co_filename == util.__file__
                and frame.f_code.co_name in ("sysctlTestAndSet", "rlimitTestAndSet")):
            error = error_details(argument[1])
            error.update({
                "function": frame.f_code.co_name,
                "line": frame.f_lineno,
                "name": frame.f_locals.get("name"),
                "target": frame.f_locals.get("limit"),
            })
            exceptions.append(error)
        return trace

    # Trace the original function without replacing its logger or error handling.
    sys.settrace(trace)
    try:
        util.fixLimits()
    finally:
        sys.settrace(None)
    state["original_exceptions"] = exceptions

    function = ast.parse(source).body[0]
    attempts = next(node for node in function.body if isinstance(node, ast.Try))
    results = []
    for statement in attempts.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            raise RuntimeError("Unexpected installed fixLimits structure")
        call = statement.value
        if not isinstance(call.func, ast.Name) or call.func.id not in (
            "rlimitTestAndSet", "sysctlTestAndSet",
        ):
            raise RuntimeError("Unexpected installed fixLimits operation")
        first, target = call.args
        name = getattr(util, first.id) if isinstance(first, ast.Name) else ast.literal_eval(first)
        target = ast.literal_eval(target)
        result = {
            "function": call.func.id,
            "name": first.id if isinstance(first, ast.Name) else name,
            "target": target,
        }
        if call.func.id == "sysctlTestAndSet":
            path = Path("/proc/sys") / name.replace(".", "/")
            try:
                result["before"] = path.read_text().strip()
            except OSError as error:
                result["read_error"] = error_details(error)
        try:
            getattr(util, call.func.id)(name, target)
        except (OSError, ValueError) as error:
            result["error"] = error_details(error)
        results.append(result)
        print("LIMIT_OPERATION", json.dumps(result), flush=True)
    state["operations"] = results
    failures = [result for result in results if "error" in result]
    state["error_count"] = len(exceptions) + len(failures)
    print("LIMIT_RESULT", json.dumps(state), flush=True)
    if args.expect == "warning" and not exceptions:
        raise RuntimeError("The original warning was not reproduced")
    if args.expect == "clean" and state["error_count"]:
        raise RuntimeError("Resource-limit initialization still fails")


if __name__ == "__main__":
    main()
