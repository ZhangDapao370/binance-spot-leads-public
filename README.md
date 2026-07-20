# Binance 现货新币台

公开只读地址部署在 GitHub Pages。页面和 `data/listings.json` 每天北京时间 09:10 自动更新。

## 收录范围

- 只收录 Binance 官方宣布的新现货币种。
- 不收录 Futures、永续合约、Alpha、Launchpool、Earn 和普通新增交易对。
- 时间在 JSON 中使用 UTC，在网页上转换为北京时间。

## AI 数据

`data/listings.json` 是稳定的公开只读接口。AI 应读取 `items` 数组，不应尝试修改该文件，也不应把 Seed Tag 解读成投资建议。

## 自动更新

GitHub Actions 每天运行抓取器、校验数据并提交有变化的 `data/listings.json` 和 `data/listings.csv`。手动运行工作流也会执行相同流程。
