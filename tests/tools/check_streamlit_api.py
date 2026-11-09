"""Check app.py's Streamlit calls against Streamlit's own source code.

Streamlit cannot be installed in every environment (it needs pyarrow), so
this script reads the Streamlit source with ``ast`` instead of importing it:
every ``st.<name>(...)`` must exist, and every keyword argument passed to a
Streamlit element (directly or through ``_kw(st.<name>, ...)``) must be a
parameter of that element.

Usage:  python tests/tools/check_streamlit_api.py <path to streamlit package> [app.py]
"""
from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict

ELEMENT_METHODS_ON_OBJECTS = True   # cols[0].button(...), c1.metric(...) are element calls too


def streamlit_signatures(pkg: str) -> tuple[dict, set]:
    sigs = defaultdict(list)       # name -> [(params, has_var_kw)]
    for root, _, files in os.walk(pkg):
        if "/tests" in root or "/proto" in root:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(root, f), encoding="utf-8").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    params = {x.arg for x in a.args + a.kwonlyargs + a.posonlyargs}
                    sigs[node.name].append((params, a.kwarg is not None))
    exported = set()
    init = ast.parse(open(os.path.join(pkg, "__init__.py"), encoding="utf-8").read())
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    exported.add(t.id)
        elif isinstance(node, (ast.ImportFrom, ast.Import)):
            for al in node.names:
                exported.add(al.asname or al.name.split(".")[0])
        elif isinstance(node, ast.FunctionDef):
            exported.add(node.name)
    return sigs, exported


CALLABLE_OBJECTS = {"cache_data", "cache_resource"}   # instances whose __call__ takes the arguments


def _accepts(sigs, name, kws):
    options = sigs.get("__call__" if name in CALLABLE_OBJECTS else name, [])
    if not options:
        return False, "no such function in Streamlit"
    for params, var_kw in options:
        if var_kw or kws <= params:
            return True, ""
    best = min(options, key=lambda o: len(kws - o[0]))
    return False, f"unknown keyword(s) {sorted(kws - best[0])}"


def check(app_path: str, pkg: str) -> list[str]:
    sigs, exported = streamlit_signatures(pkg)
    tree = ast.parse(open(app_path, encoding="utf-8").read())
    problems, checked = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        target = node.func.value
        is_st = isinstance(target, ast.Name) and target.id == "st"
        if is_st and name not in exported:
            problems.append(f"line {node.lineno}: st.{name} does not exist")
            continue
        if not is_st and not (name in sigs and _is_container(target)):
            continue
        kws = {k.arg for k in node.keywords if k.arg}
        for k in node.keywords:                               # **_kw(st.x, a=..., stretch=True)
            if k.arg is None and isinstance(k.value, ast.Call) and getattr(k.value.func, "id", "") == "_kw":
                inner = k.value
                if inner.args and isinstance(inner.args[0], ast.Attribute):
                    fn = inner.args[0].attr
                    extra = {x.arg for x in inner.keywords if x.arg and x.arg != "stretch"}
                    ok, why = _accepts(sigs, fn, extra)
                    if not ok:
                        problems.append(f"line {node.lineno}: _kw(st.{fn}, ...) {why}")
                    if any(x.arg == "stretch" for x in inner.keywords):
                        if not (_accepts(sigs, fn, {"width"})[0] or _accepts(sigs, fn, {"use_container_width"})[0]):
                            problems.append(f"line {node.lineno}: st.{fn} has neither width nor use_container_width")
        ok, why = _accepts(sigs, name, kws)
        checked += 1
        if not ok:
            problems.append(f"line {node.lineno}: {'st.' if is_st else ''}{name}(...) {why}")
    print(f"checked {checked} Streamlit calls in {app_path}")
    return problems


def _is_container(node) -> bool:
    """cols[i], c1, left, right, col: names / subscripts used for columns and containers."""
    if isinstance(node, ast.Subscript):
        return True
    return isinstance(node, ast.Name) and node.id in {"c1", "c2", "c3", "left", "right", "col", "cols"}


if __name__ == "__main__":
    pkg = sys.argv[1]
    app = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "..", "app.py")
    found = check(app, pkg)
    for p in found:
        print("PROBLEM", p)
    sys.exit(1 if found else 0)
