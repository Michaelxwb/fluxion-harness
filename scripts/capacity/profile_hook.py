"""容量 CPU 归因用的临时采样钩子（sitecustomize 形态）。**不是运行时依赖。**

用法（采样结束后必须删除，见 `PLAYBOOK.md` §7.1）：

    cp scripts/capacity/profile_hook.py .venv/lib/python3.12/site-packages/sitecustomize.py
    CAPACITY_LOAD_USERS=25 MUAD_PROFILE_DIR=/tmp/muad-prof-out \
      MUAD_PROFILE_MODULE=muad_agent_runtime MUAD_PROFILE_DUMP_SEC=5 \
      uv run pytest -q -s tests/acceptance/capacity/test_capacity_smoke.py
    rm .venv/lib/python3.12/site-packages/sitecustomize.py

环境变量：

- `MUAD_PROFILE_DIR`（必填）：快照输出目录；未设置时本模块对任何进程零影响。
- `MUAD_PROFILE_MODULE`（默认 `muad_agent_runtime`）：目标包的模块名前缀。
- `MUAD_PROFILE_ENGINE`：`pyinstrument`（默认，async 归因）| `cprofile`（函数级自身时间）。
- `MUAD_PROFILE_DUMP_SEC`（默认 10）：SIGALRM 快照间隔。
- `MUAD_PROFILE_INTERVAL`（默认 0.001）：pyinstrument 采样间隔（秒）。
- `MUAD_PROFILE_DEBUG`：可选日志文件（启动/快照失败写到那里）。

两个实测约束（改动前先读）：

1. `-m` 启动时 sitecustomize 阶段的 `sys.argv` 形如 `['-m', '--version']`，**没有模块名**；
   因此用 `builtins.__import__` 包装 + `sys.modules` 判定"这是不是要采样的服务"。
2. uvicorn 收 SIGTERM 时进程直接被信号终止（atexit 不执行），所以用 SIGALRM 周期快照：
   每次 `stop()` → 写文件 → **新建 Profiler** 再 `start()`（同一 Profiler `start()` 会续上
   上一段，占比失去时间边界）。pyinstrument 要求 start/stop 同线程，信号处理器在主线程，
   因此不能用后台线程起停。
"""

import atexit
import builtins
import os
import signal
import sys
from typing import Any

_OUT = os.environ.get("MUAD_PROFILE_DIR")
_TARGET = os.environ.get("MUAD_PROFILE_MODULE", "muad_agent_runtime")
_DEBUG = os.environ.get("MUAD_PROFILE_DEBUG")
_DUMP_SEC = float(os.environ.get("MUAD_PROFILE_DUMP_SEC", "10"))
_ENGINE = os.environ.get("MUAD_PROFILE_ENGINE", "pyinstrument")


def _log(message: str) -> None:
    if _DEBUG:
        with open(_DEBUG, "a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {message}\n")


if _OUT:
    _original_import = builtins.__import__
    _state: dict[str, Any] = {"phase": "armed", "seq": 0}
    _profiler: Any = None
    _Profiler: Any = None

    def _target_loaded() -> bool:
        # 相对导入在 `__import__` 里 name 是空串，不能靠 name 判定；看 sys.modules 更稳。
        return any(
            module == _TARGET or module.startswith(_TARGET + ".")
            for module in list(sys.modules)
        )

    def _write(text: str) -> None:
        os.makedirs(_OUT, exist_ok=True)  # type: ignore[arg-type]
        _state["seq"] += 1
        path = os.path.join(_OUT, f"{_TARGET}-{os.getpid()}-{_state['seq']:03d}.txt")  # type: ignore[arg-type]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _snapshot_cprofile() -> None:
        global _profiler
        import io
        import pstats

        try:
            _profiler.disable()
            buffer = io.StringIO()
            pstats.Stats(_profiler, stream=buffer).sort_stats("tottime").print_stats(45)
            _write(buffer.getvalue())
        except BaseException as exc:  # noqa: BLE001 —— 采样失败不能影响被测进程
            _log(f"snapshot failed: {type(exc).__name__}: {exc}")
        finally:
            import cProfile

            _profiler = cProfile.Profile()
            _profiler.enable()

    def _snapshot(*_args: object) -> None:
        global _profiler
        if _profiler is None:
            return
        if _ENGINE == "cprofile":
            _snapshot_cprofile()
            return
        try:
            _profiler.stop()
            flat = _profiler.output_text(
                unicode=False, color=False, flat=True, time="percent_of_total"
            )
            tree = _profiler.output_text(
                unicode=False, color=False, timeline=False, time="percent_of_total"
            )
            _write(flat + "\n\n===== CALL TREE =====\n" + tree)
        except BaseException as exc:  # noqa: BLE001
            _log(f"snapshot failed: {type(exc).__name__}: {exc}")
        finally:
            # 每个窗口用新的 Profiler：否则 start() 会续上上一段，占比失去时间边界。
            assert _Profiler is not None
            _profiler = _Profiler(interval=float(os.environ.get("MUAD_PROFILE_INTERVAL", "0.001")))
            _profiler.start()

    def _start() -> None:
        global _profiler, _Profiler
        if _ENGINE == "cprofile":
            import cProfile

            os.makedirs(_OUT, exist_ok=True)  # type: ignore[arg-type]
            _profiler = cProfile.Profile()
            _profiler.enable()
            _state["phase"] = "started"
            _log(f"cprofile started dump_sec={_DUMP_SEC}")
            signal.signal(signal.SIGALRM, _snapshot)
            signal.setitimer(signal.ITIMER_REAL, _DUMP_SEC, _DUMP_SEC)
            atexit.register(_snapshot)
            return
        from pyinstrument import Profiler

        _Profiler = Profiler
        os.makedirs(_OUT, exist_ok=True)  # type: ignore[arg-type]
        _profiler = Profiler(interval=float(os.environ.get("MUAD_PROFILE_INTERVAL", "0.001")))
        _profiler.start()
        _state["phase"] = "started"
        _log(f"profiler started dump_sec={_DUMP_SEC}")
        signal.signal(signal.SIGALRM, _snapshot)
        signal.setitimer(signal.ITIMER_REAL, _DUMP_SEC, _DUMP_SEC)
        atexit.register(_snapshot)

    def _import(name: str, *args: Any, **kwargs: Any) -> Any:
        module = _original_import(name, *args, **kwargs)
        if _state["phase"] == "armed" and _target_loaded():
            # 先置 starting：导入 pyinstrument 自身还会走这个包装，不能重入。
            _state["phase"] = "starting"
            try:
                _start()
            except BaseException as exc:  # noqa: BLE001
                _state["phase"] = "failed"
                _log(f"start failed: {type(exc).__name__}: {exc}")
        return module

    builtins.__import__ = _import
