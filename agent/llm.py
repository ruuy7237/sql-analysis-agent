# -*- coding: utf-8 -*-
"""统一 LLM 客户端（与另外两个仓库保持一致）。

环境变量：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
无 Key 时为 mock 模式：不做生成，本项目的评测会退化为「规则基线 vs LLM」中的基线部分。
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "deepseek-chat")


def mode() -> str:
    return "mock" if not API_KEY else "api"


def chat(messages, temperature=0.0, retries=2):
    if mode() == "mock":
        return {"role": "assistant", "content": ""}
    url = BASE_URL.rstrip("/") + "/chat/completions"
    payload = {"model": MODEL, "messages": messages, "temperature": temperature}
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err}")
