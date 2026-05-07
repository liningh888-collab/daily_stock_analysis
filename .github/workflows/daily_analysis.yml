name: 高胜率稳健量化策略
on:
  schedule:
    - cron: '15 1 * * 1-5'
  workflow_dispatch:

jobs:
  analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install requests pandas numpy yfinance
      - name: 运行策略推送
        env:
          TZ: Asia/Shanghai
        run: python main.py
