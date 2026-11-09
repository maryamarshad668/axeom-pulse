"""A small stand-in for Streamlit used to run app.py in tests.

It does not draw anything. It records every element the script shows, keeps
session state between runs, and lets a test "tap" a button the way Streamlit
does: the button's on_click runs first, then the script runs again.
Real rendering is checked separately (see README, "Checking the app").
"""
from __future__ import annotations

import functools
import runpy
import types


class StopScript(Exception):
    pass


class RerunScript(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


class _Block:
    """Columns, containers and expanders: same API, used as context managers."""

    def __init__(self, app, kind, label=""):
        self._app, self._kind, self._label = app, kind, label

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return getattr(self._app, name)


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = SessionState()
        self.uploads = []            # what the file uploader returns
        self.pressed = set()         # keys/labels of buttons that return True on the next run
        self._caches = {}
        self.reset_run()

    # ---------------------------------------------------------- bookkeeping
    def reset_run(self):
        self.shown = []              # (kind, text)
        self.buttons = {}            # key -> dict(label, on_click, args, kwargs, disabled)

    def text(self) -> str:
        return "\n".join(str(t) for _, t in self.shown)

    def _show(self, kind, value=""):
        self.shown.append((kind, value))

    # ---------------------------------------------------------- running
    def run(self, path: str, max_reruns: int = 3):
        for _ in range(max_reruns):
            self.reset_run()
            try:
                runpy.run_path(path, run_name="__main__")
                return self
            except StopScript:
                return self
            except RerunScript:
                self.pressed.clear()         # as in Streamlit: after a rerun a button is no longer pressed
                continue
        return self

    def tap(self, key: str, path: str):
        """Press a button: run its callback (as Streamlit does), then rerun the script."""
        b = self.buttons[key]
        assert not b["disabled"], f"button {key} is disabled"
        if b["on_click"]:
            b["on_click"](*b["args"], **b["kwargs"])
        else:
            self.pressed.add(key)
        try:
            return self.run(path)
        finally:
            self.pressed.discard(key)

    # ---------------------------------------------------------- page config / text
    def set_page_config(self, **kw):
        self._show("page_config", kw.get("page_title", ""))

    def markdown(self, body="", unsafe_allow_html=False, **kw):
        self._show("markdown", body)

    def caption(self, body="", **kw):
        self._show("caption", body)

    def write(self, *args, **kw):
        self._show("write", " ".join(map(str, args)))

    def code(self, body="", language=None, **kw):
        self._show("code", body)

    def success(self, body="", **kw):
        self._show("success", body)

    def warning(self, body="", **kw):
        self._show("warning", body)

    def error(self, body="", **kw):
        self._show("error", body)

    def info(self, body="", **kw):
        self._show("info", body)

    def metric(self, label, value, **kw):
        self._show("metric", f"{label}: {value}")

    def dataframe(self, data=None, **kw):
        self._show("dataframe", data)

    def plotly_chart(self, fig, **kw):
        fig.to_json()                         # the figure must serialise
        self._show("chart", fig)

    def link_button(self, label, url, **kw):
        self._show("link_button", f"{label} -> {url}")

    def download_button(self, label, data, file_name=None, **kw):
        self._show("download", (file_name, data))

    # ---------------------------------------------------------- layout
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Block(self, "column") for _ in range(n)]

    def container(self, **kw):
        return _Block(self, "container")

    def expander(self, label, expanded=False, **kw):
        self._show("expander", label)
        return _Block(self, "expander", label)

    # ---------------------------------------------------------- widgets
    def _value(self, key, default):
        if key is not None and key in self.session_state:
            return self.session_state[key]
        if key is not None:
            self.session_state[key] = default
        return default

    def radio(self, label, options, index=0, key=None, **kw):
        return self._value(key, list(options)[index] if options else None)

    def selectbox(self, label, options, index=0, key=None, format_func=None, placeholder=None, **kw):
        options = list(options)
        default = options[index] if (index is not None and options) else None
        return self._value(key, default)

    def checkbox(self, label, value=False, key=None, **kw):
        return self._value(key, value) if key else (label in self.pressed or value)

    def file_uploader(self, label, type=None, accept_multiple_files=False, key=None, **kw):
        return list(self.uploads) if accept_multiple_files else (self.uploads[0] if self.uploads else None)

    def button(self, label, key=None, on_click=None, args=(), kwargs=None, disabled=False, **kw):
        k = key or label
        assert k not in self.buttons, f"duplicate button key {k!r} (Streamlit would fail)"
        self.buttons[k] = {"label": label, "on_click": on_click, "args": tuple(args or ()),
                           "kwargs": dict(kwargs or {}), "disabled": disabled}
        self._show("button", label)
        return k in self.pressed

    # ---------------------------------------------------------- control flow
    def stop(self):
        raise StopScript()

    def rerun(self):
        raise RerunScript()

    # ---------------------------------------------------------- caching
    def _cache(self, func=None, **opts):
        def deco(f):
            store = self._caches.setdefault(f.__qualname__, {})

            @functools.wraps(f)
            def wrapper(*args):
                if args not in store:
                    store[args] = f(*args)
                return store[args]
            return wrapper
        return deco(func) if callable(func) else deco

    cache_resource = _cache
    cache_data = _cache


class Upload:
    """What st.file_uploader returns: a name and the bytes."""

    def __init__(self, name: str, data: bytes):
        self.name, self._data = name, data

    def getvalue(self) -> bytes:
        return self._data
