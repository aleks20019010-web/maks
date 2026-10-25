# brain/reaction.py
# Публикация событий appraisal + очередь + воркер.
#
# controls.py (и любой другой) зовёт reaction.publish(Event(...)).
# publish не блокирует — кладёт в очередь и возвращается.
# start_worker() запускает фоновый поток, который зовёт service.handle(event).
#
# Два лока: _queue_lock — для очереди, _worker_lock — для воркера.
# Разные ресурсы — разные локи. Иначе дедлок на нереентрантном Lock.

import queue
import threading
from collections import deque

from brain.appraisal.events import Event
from shared.log import log


QUEUE_MAXSIZE = 100
_recent_published: deque = deque(maxlen=20)  # для отладки / тестов


_queue: queue.Queue | None = None
_worker: threading.Thread | None = None
_queue_lock = threading.Lock()
_worker_lock = threading.Lock()


def publish(event: Event) -> None:
    """Положить событие в очередь. Не блокирует."""
    q = _get_queue()
    try:
        q.put_nowait(event)
        _recent_published.append(event)
    except queue.Full:
        log(f"reaction: очередь полна, событие отброшено: {event}", tag="BRAIN", level="WARN")


def start_worker() -> None:
    """Запустить воркер (один раз). Повторный вызов — no-op."""
    global _worker
    q = _get_queue()
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(
            target=_worker_loop,
            args=(q,),
            name="appraisal-worker",
            daemon=True,
        )
        _worker.start()
        log("reaction: воркер запущен", tag="BRAIN")


def _worker_loop(q: queue.Queue) -> None:
    from brain.appraisal.service import AppraisalService
    service = AppraisalService()
    log("reaction: воркер готов, слушаю очередь", tag="BRAIN")

    while True:
        try:
            event = q.get()
        except Exception as e:
            log(f"reaction: очередь сломалась: {e}", tag="BRAIN", level="ERROR")
            return

        try:
            service.handle(event)
        except Exception as e:
            log(f"reaction: ошибка обработки: {e}", tag="BRAIN", level="WARN")


def _get_queue() -> queue.Queue:
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        return _queue


def _reset_for_tests() -> None:
    """Только для тестов: очистить очередь и историю публикаций."""
    global _queue, _worker
    with _queue_lock:
        _queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        _recent_published.clear()
    with _worker_lock:
        _worker = None