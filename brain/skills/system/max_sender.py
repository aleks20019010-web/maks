"""MAX: отправка отчёта. Токен из env, curl через временный файл.

Логика:
- Проверка интернета (socket к 8.8.8.8 и 1.1.1.1).
- Проверка curl.exe в PATH.
- Отправка через curl (UTF-8 без BOM, platform-api2.max.ru).
- При no_internet — фоновая попытка раз в минуту, пересборка отчёта.
- Lock на _retry_active — два вызова не запускают две попытки.
- 3 неудачи в фоне — сдаться.
"""

import os
import json
import socket
import shutil
import subprocess
import threading
import tempfile
from shared.log import log


# ===== Настройки =====
MAX_API_URL = "https://platform-api2.max.ru/messages"
CURL_TIMEOUT_SEC = 30
SOCKET_TIMEOUT_SEC = 1
RETRY_INTERVAL_SEC = 60
MAX_RETRY_ATTEMPTS = 3

# ===== Состояние фоновой попытки =====
_retry_lock = threading.Lock()
_retry_active = False


def _get_max_credentials() -> tuple[str | None, str | None]:
    """Токен и chat_id из env. Как в скрипте монитора."""
    token = os.environ.get("MAX_TOKEN")
    chat_id = os.environ.get("MAX_CHAT_ID")
    return token, chat_id


def _check_internet() -> bool:
    """Проверка интернета: TCP-коннект к двум DNS. Если один сработал — есть."""
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            socket.create_connection((host, 53), timeout=SOCKET_TIMEOUT_SEC).close()
            return True
        except OSError:
            continue
    return False


def _check_curl() -> bool:
    """curl.exe в PATH?"""
    return shutil.which("curl.exe") is not None


def _send_via_curl(text: str) -> str:
    """
    Отправка через curl. Возвращает:
        "sent" | "max_error"
    """
    token, chat_id = _get_max_credentials()
    if not token or not chat_id:
        log("max_sender: MAX_TOKEN или MAX_CHAT_ID не заданы",
            tag="SYSTEM", level="ERROR")
        return "max_error"

    url = f"{MAX_API_URL}?chat_id={chat_id}"
    body = {"text": text}

    tmp_path = None
    try:
        # Временный файл — UTF-8 без BOM. Как в скрипте.
        fd, tmp_path = tempfile.mkstemp(
            prefix="max_", suffix=".json", dir=tempfile.gettempdir()
        )
        os.close(fd)
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            json.dump(body, f, ensure_ascii=False, separators=(",", ":"))

        result = subprocess.run(
            [
                "curl.exe",
                "-sS",
                "--fail",
                "--max-time", str(CURL_TIMEOUT_SEC),
                "-X", "POST",
                url,
                "-H", f"Authorization: {token}",
                "-H", "Content-Type: application/json; charset=utf-8",
                "--data-binary", f"@{tmp_path}",
            ],
            capture_output=True,
            timeout=CURL_TIMEOUT_SEC + 5,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            log(f"max_sender: curl exit={result.returncode} {stderr}",
                tag="SYSTEM", level="ERROR")
            return "max_error"

        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        if '"error"' in stdout:
            log(f"max_sender: MAX вернул error: {stdout}",
                tag="SYSTEM", level="ERROR")
            return "max_error"

        log("max_sender: отчёт отправлен в MAX", tag="SYSTEM", level="INFO")
        return "sent"

    except subprocess.TimeoutExpired:
        log("max_sender: curl timeout", tag="SYSTEM", level="ERROR")
        return "max_error"
    except FileNotFoundError:
        log("max_sender: curl.exe не найден", tag="SYSTEM", level="ERROR")
        return "max_error"
    except Exception as e:
        log(f"max_sender: ошибка отправки: {e}", tag="SYSTEM", level="ERROR")
        return "max_error"
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _retry_loop() -> None:
    """
    Фоновая попытка. Раз в минуту проверяет интернет.
    При появлении — пересобирает отчёт (через skill.build_text) и отправляет.
    После 3 неудач — сдаётся.
    """
    global _retry_active

    # Импорт внутри — чтобы не было циклической зависимости на уровне модулей.
    from brain.skills.system import skill as system_skill

    attempts = 0
    try:
        while attempts < MAX_RETRY_ATTEMPTS:
            threading.Event().wait(RETRY_INTERVAL_SEC)

            if not _check_internet():
                attempts += 1
                log(
                    f"max_sender retry: нет интернета "
                    f"(попытка {attempts}/{MAX_RETRY_ATTEMPTS})",
                    tag="SYSTEM", level="WARN",
                )
                continue

            # Интернет есть — пересобираем свежий отчёт и отправляем.
            try:
                built = system_skill.build_text()
                text = built.get("max_text", "")
                if not text:
                    log("max_sender retry: build_text вернул пусто",
                        tag="SYSTEM", level="ERROR")
                    attempts += 1
                    continue

                status = _send_via_curl(text)
                if status == "sent":
                    log("max_sender retry: отчёт отправлен в MAX",
                        tag="SYSTEM", level="INFO")
                    return
                else:
                    attempts += 1
                    log(
                        f"max_sender retry: MAX ошибка "
                        f"(попытка {attempts}/{MAX_RETRY_ATTEMPTS})",
                        tag="SYSTEM", level="WARN",
                    )
            except Exception as e:
                attempts += 1
                log(f"max_sender retry: build_text упал: {e}",
                    tag="SYSTEM", level="ERROR")

        log("max_sender retry: сдался после 3 неудач",
            tag="SYSTEM", level="ERROR")
    finally:
        with _retry_lock:
            _retry_active = False


def send(text: str) -> str:
    """
    Основная отправка. Возвращает:
        "sent" | "no_internet" | "max_error" | "retry_pending" | "curl_missing"
    """
    global _retry_active

    # 1. Проверка интернета.
    if not _check_internet():
        with _retry_lock:
            if _retry_active:
                return "retry_pending"
            _retry_active = True

        thread = threading.Thread(target=_retry_loop, daemon=True, name="max_retry")
        thread.start()
        log("max_sender: нет интернета, запущена фоновая попытка",
            tag="SYSTEM", level="WARN")
        return "no_internet"

    # 2. Проверка curl.
    if not _check_curl():
        log("max_sender: curl.exe не найден в PATH",
            tag="SYSTEM", level="ERROR")
        return "curl_missing"

    # 3. Отправка.
    return _send_via_curl(text)