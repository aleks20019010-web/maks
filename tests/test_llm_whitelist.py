# tests/test_llm_whitelist.py
# B-новое (день 30): whitelist проверяет не только prefix, но и объект.
#
# Проверяем:
#   - валидные команды проходят: «включи арию», «поставь киша», «пауза»
#   - «включи что-нибудь» / «поставь эдакое» — не проходят
#   - «включи » без объекта — не проходит
#   - FORBIDDEN по-прежнему блокирует: «формат диск», «shutdown»
#   - «громкость 50» — не проходит (A2/B1: убран prefix)

from brain.skills.llm._whitelist import is_command_allowed


# ── валидные ──────────────────────────────────────────────────

def test_allowed_valid_prefix():
    """«включи арию» / «поставь киша» — проходят."""
    assert is_command_allowed("включи арию") is True
    assert is_command_allowed("поставь киша") is True
    assert is_command_allowed("найди машину времени") is True


def test_allowed_exact():
    """«пауза» / «статус» / «погода» — проходят."""
    assert is_command_allowed("пауза") is True
    assert is_command_allowed("статус") is True
    assert is_command_allowed("погода") is True


# ── мусорные объекты (B-новое) ────────────────────────────────

def test_noise_object_blocked():
    """«включи что-нибудь» / «поставь эдакое» — не проходят."""
    assert is_command_allowed("включи что-нибудь") is False
    assert is_command_allowed("включи что нибудь") is False
    assert is_command_allowed("поставь эдакое") is False
    assert is_command_allowed("найди чего-нибудь") is False
    assert is_command_allowed("играй какое-нибудь") is False


def test_prefix_without_object_blocked():
    """«включи » без объекта — не проходит."""
    assert is_command_allowed("включи ") is False
    assert is_command_allowed("включи") is False
    assert is_command_allowed("поставь ") is False


# ── FORBIDDEN ─────────────────────────────────────────────────

def test_forbidden_blocked():
    """«формат диск» / «shutdown» — не проходят."""
    assert is_command_allowed("формат диск") is False
    assert is_command_allowed("shutdown") is False
    assert is_command_allowed("удали всё") is False


# ── A2/B1: «громкость » убран из prefix ──────────────────────

def test_volume_not_in_whitelist():
    """A2/B1: LLM не может вернуть «громкость X» как command."""
    assert is_command_allowed("громкость 50") is False
    assert is_command_allowed("громкость пятьдесят") is False