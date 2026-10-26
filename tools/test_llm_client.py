# tools/test_llm_client.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.skills.llm.skill import _get_client, _client_error

c = _get_client()
print("client:", "OK" if c else "FAIL")
print("error:", _client_error)