#!/usr/bin/env bash
# macOS / Linux 啟動：自動準備 Python 3.12（透過 uv）、安裝套件、開啟瀏覽器。
# 真實資料：bash start.sh      示範資料：bash start.sh demo
set -e
cd "$(dirname "$0")/backend"

# 1) 準備 uv（Python 版本與套件管理工具）；macOS 內建 Python 太舊，不使用
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "第一次執行：安裝 uv（約 10 秒）…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || python3 -m pip install --user -q uv
  export PATH="$HOME/.local/bin:$PATH"
  for d in "$HOME"/Library/Python/*/bin; do [ -d "$d" ] && export PATH="$d:$PATH"; done
fi

# 2) 建立 Python 3.12 環境並安裝套件（第一次約 1–2 分鐘）
if [ ! -x .venv/bin/python ]; then
  echo "建立 Python 3.12 環境…"
  uv venv --python 3.12 .venv
fi
echo "檢查套件…"
uv pip install --python .venv/bin/python -q -r requirements.txt

[ -f .env ] || cp .env.example .env

if [ "${1:-}" = "demo" ]; then
  export DEMO_MODE=true
  [ -f data/twstock_demo.db ] || .venv/bin/python -m app.cli demo
else
  export DEMO_MODE=false
  echo "真實資料模式：第一次會在背景抓取證交所／櫃買今日收盤，並回填歷史股價（需數小時，可關閉後再開會續傳）。"
fi

# 3) 伺服器就緒後自動開啟瀏覽器
( for i in $(seq 1 60); do
    if curl -s -o /dev/null http://127.0.0.1:8000/api/meta; then
      (command -v open >/dev/null && open http://127.0.0.1:8000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000) || true
      break
    fi
    sleep 1
  done ) &

echo "網站：http://127.0.0.1:8000   （關閉此視窗即停止）"
exec .venv/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
