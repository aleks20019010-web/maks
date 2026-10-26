# tools/test_llm_skill.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.skills.llm.skill import _call_llm, _build_context
from brain.skills.llm import _parser


user_text = "почему небо голубое"
print("=== 1. context ===")
ctx = _build_context()
print(ctx)

print("\n=== 2. raw LLM ===")
raw = _call_llm(user_text)
print("raw:", repr(raw)[:500] if raw else "None")

print("\n=== 3. parsed ===")
if raw:
    parsed = _parser.parse_response(raw)
    print("parsed:", parsed)
else:
    print("raw is None — LLM не ответила")