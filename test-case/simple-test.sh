#!/bin/bash
# 最小连通性测试：curl 直接打 chat/completions
# 用法: ./simple-test.sh [model] [max_tokens]

MODEL="${1:-${OPENAI_MODEL:-x-preview-f-free}}"
MAX_TOKENS="${2:-300}"

curl -sS -m 60 https://opencode.ai/zen/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"你好，請用一句話介紹台積電\"}],\"max_tokens\":$MAX_TOKENS}"
