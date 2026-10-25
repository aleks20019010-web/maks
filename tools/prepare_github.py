# tools/prepare_github.py
# Подготовка репозитория Макса к GitHub.
#
# Читает шаблоны из tools/templates/ (без Python-кавычек).
# Создаёт .gitignore, README.md, requirements.txt, config/secrets.json,
# tools/cleanup_c.ps1, data/.gitkeep.
#
# Запуск:
#   python tools/prepare_github.py --dry-run
#   python tools/prepare_github.py

import os
import sys
import shutil
import re
import json


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "tools", "templates")
DRY_RUN = "--dry-run" in sys.argv


# ==================== ОБЯЗАТЕЛЬНЫЕ ====================

REQUIRED_FILES = [
    "supervisor.py",
    "start.bat",
    "stop.bat",
    "brain/main.py",
    "personality_core/engine.py",
    "shared/config.py",
    "responses/wake.json",
    "responses/music.json",
    "responses/openings.json",
    "responses/composer.json",
    "docs/architecture.md",
    "config/workers.json",
    "config/identity.json",
]


def _log(msg):
    prefix = "[DRY-RUN] " if DRY_RUN else ""
    print(f"{prefix}{msg}")


def _write_from_template(template_name: str, target_rel: str) -> bool:
    """Прочитать шаблон из tools/templates/ и записать в target_rel."""
    src = os.path.join(TEMPLATES_DIR, template_name)
    dst = os.path.join(BASE_DIR, target_rel)

    if not os.path.exists(src):
        _log(f"  ⚠ {template_name} — нет шаблона", )
        return False
    if os.path.exists(dst):
        _log(f"  ⏭ {target_rel} — уже есть")
        return False

    if DRY_RUN:
        _log(f"  📝 {target_rel} — будет создан из {template_name}")
        return False

    try:
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(content)
        _log(f"  ✅ {target_rel} — создан")
        return True
    except Exception as e:
        _log(f"  ✗ {target_rel}: {e}")
        return False


def _extract_secrets() -> dict:
    """Извлечь токены из shared/yandex.py и config/*.txt."""
    secrets = {}

    yandex_path = os.path.join(BASE_DIR, "shared", "yandex.py")
    if os.path.exists(yandex_path):
        try:
            with open(yandex_path, "r", encoding="utf-8") as f:
                content = f.read()
            m = re.search(r'TOKEN\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                secrets["yandex_token"] = m.group(1)
        except Exception:
            pass

    for key, fname in [("yandex_key", "yandex_key.txt"),
                       ("yandex_folder", "yandex_folder.txt")]:
        p = os.path.join(BASE_DIR, "config", fname)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    secrets[key] = v
            except Exception:
                pass

    return secrets


def _write_secrets(secrets: dict):
    if not secrets:
        _log("  ⏭ config/secrets.json — секретов не найдено")
        return
    path = os.path.join(BASE_DIR, "config", "secrets.json")
    if os.path.exists(path):
        _log("  ⏭ config/secrets.json — уже есть")
        return
    if DRY_RUN:
        _log(f"  📝 config/secrets.json — будет создан ({list(secrets.keys())})")
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(secrets, f, ensure_ascii=False, indent=2)
        _log(f"  ✅ config/secrets.json — создан ({list(secrets.keys())})")
    except Exception as e:
        _log(f"  ✗ config/secrets.json: {e}")


def _copy_cleanup_script():
    src = r"D:\Scripts\cleanup_c.ps1"
    dst = os.path.join(BASE_DIR, "tools", "cleanup_c.ps1")
    if os.path.exists(dst):
        _log("  ⏭ tools/cleanup_c.ps1 — уже есть")
        return
    if not os.path.exists(src):
        _log("  ⚠ tools/cleanup_c.ps1 — нет источника D:\\Scripts\\cleanup_c.ps1")
        return
    if DRY_RUN:
        _log("  📝 tools/cleanup_c.ps1 — будет скопирован")
        return
    try:
        shutil.copy2(src, dst)
        _log("  ✅ tools/cleanup_c.ps1 — скопирован")
    except Exception as e:
        _log(f"  ✗ tools/cleanup_c.ps1: {e}")


def _create_gitkeep():
    for d in ["data"]:
        path = os.path.join(BASE_DIR, d, ".gitkeep")
        if os.path.exists(path):
            _log(f"  ⏭ {d}/.gitkeep — уже есть")
            continue
        if DRY_RUN:
            _log(f"  📝 {d}/.gitkeep — будет создан")
            continue
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
            _log(f"  ✅ {d}/.gitkeep — создан")
        except Exception as e:
            _log(f"  ✗ {d}/.gitkeep: {e}")


def main():
    _log("=" * 60)
    _log("Подготовка Макса к GitHub")
    _log("=" * 60)
    _log(f"BASE_DIR: {BASE_DIR}")
    _log(f"TEMPLATES_DIR: {TEMPLATES_DIR}")
    _log("")

    _log("1. .gitignore")
    _write_from_template(".gitignore.txt", ".gitignore")
    _log("")

    _log("2. README.md")
    _write_from_template("README.md.txt", "README.md")
    _log("")

    _log("3. requirements.txt")
    _write_from_template("requirements.txt", "requirements.txt")
    _log("")

    _log("4. Секреты → config/secrets.json")
    secrets = _extract_secrets()
    _write_secrets(secrets)
    _log("")

    _log("5. cleanup_c.ps1 → tools/")
    _copy_cleanup_script()
    _log("")

    _log("6. .gitkeep")
    _create_gitkeep()
    _log("")

    _log("7. Обязательные файлы")
    missing = []
    for f in REQUIRED_FILES:
        path = os.path.join(BASE_DIR, f)
        exists = os.path.exists(path)
        _log(f"  {'✅' if exists else '❌'} {f}")
        if not exists:
            missing.append(f)
    _log("")

    _log("=" * 60)
    if missing:
        _log(f"⚠ Отсутствуют {len(missing)} файлов: {missing}")
    else:
        _log("✅ Все обязательные файлы на месте")
    _log("=" * 60)
    _log("")
    _log("Дальше:")
    _log("  1. git init")
    _log("  2. git add .")
    _log("  3. git commit -m 'Maks: голосовой дворецкий'")
    _log("  4. git remote add origin https://github.com/<логин>/maks.git")
    _log("  5. git push -u origin main")


if __name__ == "__main__":
    main()