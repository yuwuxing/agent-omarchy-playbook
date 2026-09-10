#!/usr/bin/env python3
"""Conservative OpenAI-compatible API correction; stdout is always paste-ready text."""
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

SETTINGS_FILE = Path(__file__).with_name("llm-settings.json")
# Count non-whitespace characters, including punctuation; preserve short input verbatim.
SHORT_INPUT_MAX_CHARS = 10


def allowed_numbers(original, corrected):
    pattern = r"\d+(?:\.\d+)*"
    before = Counter(re.findall(pattern, original))
    after = Counter(re.findall(pattern, corrected))
    if before == after:
        return True
    # Only permit dropping an immediately and explicitly corrected number.
    # Ordinary instructions such as "把 8080 改成 3000" must retain both.
    correction = re.compile(
        r"(?P<old>\d+(?:\.\d+)*)\s*[，,]?\s*"
        r"(?:哦\s*[，,]?\s*)?(?:不对|不|说错了)\s*[，,]?\s*"
        r"(?:应该是|改成|改为|是|用)\s*(?P<new>\d+(?:\.\d+)*)(?![\d.])"
    )
    for match in correction.finditer(original):
        old, new = match.group('old', 'new')
        if old != new and after[new] >= before[new] and after[old] < before[old]:
            before[old] -= 1
    return +before == after


def validate(original, corrected, finish_reason):
    if finish_reason != "stop" or not corrected.strip():
        raise ValueError("empty or truncated response")
    if len(corrected) > len(original) * 2 + 32:
        raise ValueError("unexpected expansion")
    if any(tag in corrected and tag not in original for tag in ("<think>", "</think>", "<transcript>", "```")):
        raise ValueError("unexpected model formatting")
    if not allowed_numbers(original, corrected):
        raise ValueError("numbers changed")
    for token in re.findall(r"(?<![\w/])(?:/[A-Za-z0-9_.~/-]+|--[A-Za-z0-9][A-Za-z0-9_-]*)", original):
        if token not in corrected:
            raise ValueError("path or option changed")
    return corrected.strip()


def build_messages(text):
    prompt = Path(__file__).with_name("llm-correction-prompt.txt").read_text().strip()
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "校对以下转写，只返回原句的校正版：\n<transcript>帮我写一个 Python 脚本。</transcript>"},
        {"role": "assistant", "content": "帮我写一个 Python 脚本。"},
        {"role": "user", "content": "整理以下口述，只返回最终正文：\n<transcript>分三步，先安装依赖，然后启动服务，最后测试接口。</transcript>"},
        {"role": "assistant", "content": "- 安装依赖\n- 启动服务\n- 测试接口"},
        {"role": "user", "content": "整理以下口述，只返回最终正文：\n<transcript>" + text + "</transcript>"},
    ]


def correct(text):
    settings = json.loads(SETTINGS_FILE.read_text())
    messages = build_messages(text)
    body = dict(settings.get("request_options", {}))
    body.update({"model": settings["model"], "messages": messages,
                 "temperature": 0, "max_tokens": 1024, "stream": False})
    headers = {"Content-Type": "application/json"}
    if settings.get("api_key_env"):
        headers["Authorization"] = "Bearer " + os.environ[settings["api_key_env"]]
    request = urllib.request.Request(settings["endpoint"], data=json.dumps(body).encode(),
                                     headers=headers)
    with urllib.request.urlopen(request, timeout=6) as response:
        data = json.load(response)
    choice = data["choices"][0]
    return validate(text, choice["message"]["content"], choice["finish_reason"])


def main():
    original = sys.stdin.read()
    if len(re.sub(r"\s", "", original)) <= SHORT_INPUT_MAX_CHARS:
        sys.stdout.write(original)
        return
    try:
        result = correct(original)
    except Exception as error:
        # Do not put private dictation text or model responses in the journal.
        print("LLM correction skipped; original retained (" + type(error).__name__ + ")", file=sys.stderr)
        result = original
    sys.stdout.write(result)


if __name__ == "__main__":
    main()
