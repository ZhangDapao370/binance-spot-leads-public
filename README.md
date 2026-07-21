# Binance 上新监控台

公开只读地址：<https://zhangdapao370.github.io/binance-spot-leads-public/>

现货 AI JSON：<https://zhangdapao370.github.io/binance-spot-leads-public/data/listings.json>

永续合约 AI JSON：<https://zhangdapao370.github.io/binance-spot-leads-public/data/contracts.json>

页面和两个 JSON 每天北京时间 09:10 自动更新。

## 收录范围

- 只收录 Binance 官方宣布的新现货币种。
- 永续合约单独收录到合约标签页，不会混入现货记录。
- 永续模块只收新上线的 perpetual contract，不收下架、交割、参数调整和活动公告。
- 一份公告同时上线多个合约时，每个合约拆成一条记录。
- 时间在 JSON 中使用 UTC，在网页上转换为北京时间。

## AI 数据

`data/listings.json` 和 `data/contracts.json` 是稳定的公开只读接口。AI 应读取 `items` 数组，不应尝试修改文件，也不应把 Seed Tag 或永续合约上线解读成投资建议。

## 自动更新

GitHub Actions 每天运行两个抓取器，分别校验并提交现货与永续合约数据。任一接口出现空关键字段、类型混收或内部字段时，任务会明确报错并停止发布。
