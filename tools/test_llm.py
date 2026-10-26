from openai import OpenAI

with open("D:/Maks/config/llm_key.txt", "r", encoding="utf-8") as f:
    key = f.read().strip()

client = OpenAI(api_key=key, base_url="https://api.aitunnel.ru/v1/")

try:
    resp = client.chat.completions.create(
        model="deepseek-v4-flash-0731",
        messages=[{"role": "user", "content": "Привет! Ответь одним словом."}],
        max_tokens=100,
    )
    print("=== RESPONSE ===")
    print("finish_reason:", resp.choices[0].finish_reason)
    print("content:", repr(resp.choices[0].message.content))
    print("usage:", resp.usage)
except Exception as e:
    print("FAIL:", e)