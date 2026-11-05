# brain/composer.py
# DEPRECATED (v6): Composer переехал в personality_core/composer.py.
#
# Причина переноса: personality_core/engine.py импортировал
# brain.composer → инверсия зависимостей (PC → brain). Music worker
# импортирует personality_core → тянул brain. Теперь Composer живёт
# в personality_core, а этот файл — обёртка-реэкспорт для
# совместимости (warmup_tts.py импортирует _join_parts отсюда,
# возможно что-то ещё — grep по проекту).
#
# TODO (позже): убрать обёртку, все импорты → personality_core.composer.

from personality_core.composer import (  # noqa: F401
    Composer,
    _join_parts,
    _substitute,
    _ends_with_pause,
    _PART_PAUSE,
    _END_MARKS,
    _COMPOSER_FILE,
)