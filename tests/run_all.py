"""
tests/run_all.py — единый раннер тестов Макса.

Использование:
    python tests/run_all.py            # все тесты
    python tests/run_all.py phrase     # только test_*.py, где в имени есть 'phrase'
    python tests/run_all.py -v         # подробный вывод (print из тестов)

Находит все test_*.py в папке tests/, импортирует, запускает все функции
с префиксом test_. Считает pass/fail. Exit-code 0 если всё зелёное.
"""

import os
import sys
import importlib.util
import traceback
import io
import contextlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"


def discover(filter_str: str | None = None):
    """Найти все test_*.py, кроме run_all.py."""
    files = []
    for p in sorted(TESTS_DIR.glob("test_*.py")):
        if p.name == "run_all.py":
            continue
        if filter_str and filter_str not in p.name:
            continue
        files.append(p)
    return files


def import_module_from_path(path: Path):
    """Импортировать модуль по пути."""
    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    # добавим корень проекта в sys.path, чтобы импорты внутри тестов работали
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(mod)
    return mod


def run_all_tests(verbose: bool = False, filter_str: str | None = None) -> int:
    files = discover(filter_str)
    if not files:
        print("⚠ Не найдено ни одного test_*.py")
        return 1

    print(f"🧪 Раннер тестов — {len(files)} модулей")
    print(f"   ROOT: {ROOT}")
    print(f"   TESTS: {TESTS_DIR}")
    if filter_str:
        print(f"   фильтр: *{filter_str}*")
    print()

    total_pass = 0
    total_fail = 0
    failed_details = []

    for f in files:
        rel = f.relative_to(ROOT)
        print(f"📦 {rel}")

        try:
            mod = import_module_from_path(f)
        except Exception as e:
            print(f"   💥 не удалось импортировать: {type(e).__name__}: {e}")
            failed_details.append((str(rel), "IMPORT", traceback.format_exc()))
            total_fail += 1
            print()
            continue

        test_funcs = sorted(
            name for name in dir(mod)
            if name.startswith("test_") and callable(getattr(mod, name))
        )
        if not test_funcs:
            print("   ⚠ нет тестов в модуле")
            print()
            continue

        for name in test_funcs:
            fn = getattr(mod, name)
            try:
                if verbose:
                    fn()
                else:
                    # проглотить stdout теста
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        fn()
                print(f"   ✅ {name}")
                total_pass += 1
            except AssertionError as e:
                print(f"   ❌ {name}: {e}")
                total_fail += 1
                failed_details.append((f"{rel}::{name}", "ASSERT", str(e)))
            except Exception as e:
                print(f"   💥 {name}: {type(e).__name__}: {e}")
                total_fail += 1
                failed_details.append((f"{rel}::{name}", "ERROR", traceback.format_exc()))

        print()

    # ── итог ────────────────────────────────────────────────
    print("=" * 60)
    print(f"  Прошло:  {total_pass}")
    print(f"  Упало:   {total_fail}")

    if failed_details:
        print("\n  Детали падений:")
        for loc, kind, msg in failed_details:
            print(f"\n  ⛔ {loc} [{kind}]")
            # первая строка + след
            for line in msg.strip().splitlines()[:6]:
                print(f"     {line}")

    print("=" * 60)
    return 0 if total_fail == 0 else 1


def main():
    args = sys.argv[1:]
    verbose = "-v" in args or "--verbose" in args
    args = [a for a in args if not a.startswith("-")]
    filter_str = args[0] if args else None

    rc = run_all_tests(verbose=verbose, filter_str=filter_str)
    sys.exit(rc)


if __name__ == "__main__":
    main()