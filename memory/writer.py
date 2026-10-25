# memory/writer.py
# Фоновый writer: вычитывает очередь событий и пишет в SQLite.
# Основной поток никогда не ждёт запись.
#
# v2 (2026-10-10):
#   + Обработка новых типов: repeat, idle, wake, mode_change,
#                            volume_change, silence
#   + context передаётся в _insert_*
#   + stop() добивает очередь перед финальным recalc

import time
import queue
import threading


class Writer:
    """Фоновый поток для записи событий в память.
    Batch-запись раз в FLUSH_INTERVAL сек или при заполнении BATCH_SIZE."""

    BATCH_SIZE = 50          # сколько событий накапливать до записи
    FLUSH_INTERVAL = 1.0     # но не реже раза в секунду

    def __init__(self, events_handler, aggregates_handler):
        self.events = events_handler          # events.py (для вызовов _insert_*)
        self.aggregates = aggregates_handler  # aggregates.py (для recalc)
        self.queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._events_since_recalc = 0
        self._recalc_interval = 20
        self._processed = 0

    # --------------------------------------------------------
    # ПУБЛИЧНОЕ API
    # --------------------------------------------------------
    def start(self):
        """Запускает фоновый поток."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MemoryWriter")
        self._thread.start()
        print("🧠 memory: writer запущен")

    def stop(self, timeout=5.0):
        """Останавливает поток, дожидается flush.

        Гарантия: все события из очереди будут записаны перед выходом.
        """
        if self._thread is None:
            return
        print("🧠 memory: writer останавливается...")
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        print(f"🧠 memory: writer остановлен (processed={self._processed})")

    def put(self, event_type, data):
        """Кладёт событие в очередь. Мгновенно."""
        self.queue.put((event_type, data))

    # --------------------------------------------------------
    # ЦИКЛ ОБРАБОТКИ
    # --------------------------------------------------------
    def _loop(self):
        last_flush = time.time()
        while not self._stop.is_set():
            try:
                # Забираем из очереди с таймаутом
                try:
                    item = self.queue.get(timeout=0.3)
                except queue.Empty:
                    # Очередь пуста — проверяем, не надо ли flush
                    if self._events_since_recalc > 0 and (time.time() - last_flush) > self.FLUSH_INTERVAL:
                        self._maybe_recalc()
                        last_flush = time.time()
                    continue

                event_type, data = item
                try:
                    self._handle(event_type, data)
                    self._events_since_recalc += 1
                    self._processed += 1
                except Exception as e:
                    print(f"⚠ memory writer: {event_type} error: {e}")

                # Если накопилось много — flush
                if self._events_since_recalc >= self.BATCH_SIZE:
                    self._maybe_recalc()
                    last_flush = time.time()

            except Exception as e:
                print(f"⚠ memory writer loop: {e}")
                time.sleep(0.5)

        # === ДОБИВАЕМ ОЧЕРЕДЬ ПЕРЕД ВЫХОДОМ ===
        drained = 0
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            event_type, data = item
            try:
                self._handle(event_type, data)
                self._events_since_recalc += 1
                self._processed += 1
                drained += 1
            except Exception as e:
                print(f"⚠ memory writer drain: {event_type} error: {e}")

        if drained:
            print(f"🧠 memory: writer добрал {drained} событий из очереди")

        # Финальный recalc
        try:
            if self._events_since_recalc > 0:
                self._maybe_recalc()
        except Exception as e:
            print(f"⚠ memory final flush: {e}")

    # --------------------------------------------------------
    # ОБРАБОТКА СОБЫТИЙ
    # --------------------------------------------------------
    def _handle(self, event_type, data):
        """Обрабатывает одно событие — вызывает соответствущий метод events."""
        ctx = data.get('context')   # опциональный контекст (dict → JSON в events)

        if event_type == 'play_request':
            self.events._insert_play_request(data['query'], context=ctx)

        elif event_type == 'play_start':
            self.events._insert_play_start(
                data['track_id'],
                data['artist'],
                data['title'],
                data['source'],
                context=ctx,
            )

        elif event_type == 'skip':
            self.events._insert_skip(
                data['track_id'],
                data.get('seconds_played', 0),
                context=ctx,
            )

        elif event_type == 'finish':
            self.events._insert_finish(
                data['track_id'],
                data.get('duration', 0),
                context=ctx,
            )

        elif event_type == 'search':
            self.events._insert_search(
                data['query'],
                data.get('found_artist'),
                data.get('found_track'),
                data.get('success', False),
                context=ctx,
            )

        # --- Новые типы (v2) ---
        elif event_type == 'repeat_command':
            self.events._insert_repeat(
                data['command'],
                data['count'],
                context=ctx,
            )

        elif event_type == 'idle_period':
            self.events._insert_idle(
                data['duration_sec'],
                data['was_playing'],
                context=ctx,
            )

        elif event_type == 'wake_word':
            self.events._insert_wake(
                data.get('source', 'voice'),
                context=ctx,
            )

        elif event_type == 'mode_change':
            self.events._insert_mode_change(
                data['from_mode'],
                data['to_mode'],
                data.get('trigger', ''),
                context=ctx,
            )

        elif event_type == 'volume_change':
            self.events._insert_volume_change(
                data['old'],
                data['new'],
                data.get('source', ''),
                context=ctx,
            )

        elif event_type == 'silence_command':
            self.events._insert_silence(
                data['duration_sec'],
                context=ctx,
            )

        else:
            print(f"⚠ memory writer: unknown event '{event_type}'")

    def _maybe_recalc(self):
        """Пересчитать веса, если накопилось."""
        if self._events_since_recalc >= self._recalc_interval:
            self._events_since_recalc = 0
            try:
                result = self.aggregates.recalc_all()
                print(f"🔄 memory: веса пересчитаны ({result})")
            except Exception as e:
                print(f"⚠ memory recalc: {e}")