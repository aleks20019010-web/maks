# tests/test_cleanup_recycle.py
# Тесты A6: «очисти корзину» — отдельный скил, свой pending, include_recycle=True.
#
# Не запускаем PowerShell. Мокаем cleanup_dry / cleanup_real / pending.
# Проверяем:
#   - can_handle: «очисти корзину» / «почисти корзину» → True, «почисти диск» → False
#   - handle: dry-run с include_recycle=True, pending set, фраза про корзину
#   - confirm: «да» → cleanup_real(include_recycle=True)

import pytest

from brain.skills.system import cleanup
from brain.skills.system.cleanup import (
    CleanupRecycleSkill,
    CleanupRecycleConfirmSkill,
    CLEANUP_RECYCLE_PENDING_KEY,
)


# ── can_handle ────────────────────────────────────────────────

def test_can_handle_recycle():
    """«очисти корзину» / «почисти корзину» → True."""
    skill = CleanupRecycleSkill()
    assert skill.can_handle("очисти корзину") is True
    assert skill.can_handle("почисти корзину") is True
    assert skill.can_handle("убери корзину") is True


def test_can_handle_not_recycle():
    """«почисти диск C» / «включи музыку» → False."""
    skill = CleanupRecycleSkill()
    assert skill.can_handle("почисти диск c") is False
    assert skill.can_handle("включи музыку") is False
    assert skill.can_handle("") is False


# ── handle: dry-run + pending ─────────────────────────────────

@pytest.fixture
def fake_cleanup(monkeypatch):
    """Мокаем cleanup_dry / cleanup_real / pending.set / pending.get / _say_*."""
    calls = {"dry": [], "real": [], "pending": [], "said": []}

    def _dry(include_recycle=False, include_winold=False):
        calls["dry"].append({"include_recycle": include_recycle})
        data = {
            "FreeBefore": 100,
            "TotalGB": 500,
            "Details": [
                {"Desc": "Корзина", "Freed": 3.5},
                {"Desc": "%TEMP%", "Freed": 1.0},
            ],
        }
        return data, "msg", 1.0

    def _real(include_recycle=False):
        calls["real"].append({"include_recycle": include_recycle})
        return {"Freed": 3.5, "FreeAfter": 103.5}, "Корзина очищена."

    monkeypatch.setattr(cleanup, "cleanup_dry", _dry)
    monkeypatch.setattr(cleanup, "cleanup_real", _real)

    from brain import pending
    monkeypatch.setattr(pending, "set",
                        lambda key, **kw: calls["pending"].append({"key": key, **kw}))
    monkeypatch.setattr(pending, "pop",
                        lambda key: (calls["pending"].append({"pop": key}) or
                                     type("P", (), {"data": {}})()))
    monkeypatch.setattr(pending, "get",
                        lambda key: type("P", (), {"data": {}})())

    monkeypatch.setattr(cleanup, "_say_text",
                        lambda text, rate=None: calls["said"].append(text))
    monkeypatch.setattr(cleanup, "_say_phrase",
                        lambda phrase_set, rate=None, **kw: calls["said"].append(phrase_set))

    return calls


def test_handle_recycle_dry_run(fake_cleanup):
    """«очисти корзину» → dry-run с include_recycle=True, pending cleanup_recycle."""
    skill = CleanupRecycleSkill()
    skill.handle("очисти корзину")

    assert len(fake_cleanup["dry"]) == 1
    assert fake_cleanup["dry"][0]["include_recycle"] is True
    assert any(p.get("key") == CLEANUP_RECYCLE_PENDING_KEY
               for p in fake_cleanup["pending"])
    assert any("Корзина" in s or "корзин" in s.lower() for s in fake_cleanup["said"])


def test_confirm_yes_recycle(fake_cleanup):
    """«да» → cleanup_real(include_recycle=True)."""
    skill = CleanupRecycleConfirmSkill()
    skill.handle("да")

    assert len(fake_cleanup["real"]) == 1
    assert fake_cleanup["real"][0]["include_recycle"] is True


def test_confirm_no_recycle(fake_cleanup):
    """«нет» → cleanup_real НЕ вызывается."""
    skill = CleanupRecycleConfirmSkill()
    skill.handle("нет")

    assert fake_cleanup["real"] == []