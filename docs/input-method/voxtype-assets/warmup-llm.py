import json, sys, time, urllib.request
from cleanup import build_messages
port, model = sys.argv[1:]
endpoint = "http://127.0.0.1:" + port
for attempt in range(120):
    try:
        with urllib.request.urlopen(endpoint + "/health", timeout=1) as r:
            if json.load(r).get("status") == "ok":
                break
    except Exception:
        time.sleep(0.25)
else:
    raise RuntimeError("LLM did not become healthy")
for text in ['先检查 Kubernetes 的 deployment 配置，不要直接 apply。如果 API 返回 429，就等 30 秒再重试，最多重试 3 次。这个改动只放到 staging，不要发布到 production。', '这是一段测试文本。']:
    messages = build_messages(text)
    body = {"model":model, "messages":messages, "max_tokens":128, "temperature":0}
    request = urllib.request.Request(endpoint + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request, timeout=60) as r:
        data=json.load(r)
        if not data.get("choices"):
            raise RuntimeError("LLM warm-up returned no choices")
print("LLM ready and warmed up:", model)
