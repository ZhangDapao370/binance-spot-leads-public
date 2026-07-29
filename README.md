# Binance 上新监控台

公开只读地址：<https://zhangdapao370.github.io/binance-spot-leads-public/>

现货 AI JSON：<https://zhangdapao370.github.io/binance-spot-leads-public/data/listings.json>

永续合约 AI JSON：<https://zhangdapao370.github.io/binance-spot-leads-public/data/contracts.json>

仅合约域指标 JSON：<https://zhangdapao370.github.io/binance-spot-leads-public/data/futures_only_metrics.json>

页面和两个 JSON 每天北京时间 09:10 自动更新。

## 收录范围

- 只收录 Binance 官方宣布的新现货币种。
- 永续合约单独收录到合约标签页，不会混入现货记录。
- 永续模块只收新上线的 perpetual contract，不收下架、交割、参数调整和活动公告。
- 一份公告同时上线多个合约时，每个合约拆成一条记录。
- 时间在 JSON 中使用 UTC，在网页上转换为北京时间。
- 永续合约页同时展示仅合约域币对数量、最近完整 UTC 日成交额、我们选中的成交额和占比。

“仅合约域”指目标完整 UTC 日存在 Binance USDⓈ-M 永续日线归档，但当前没有相同基础资产和计价资产的现货交易对；带日期后缀的交割合约不计入。“我们选中的”来自服务器全部已配置账户对应选币源的最新非零权重币对并集。页面和 JSON 会明确显示 `volume_date` 与选币快照时间。

## AI 数据

`data/listings.json`、`data/contracts.json` 和 `data/futures_only_metrics.json` 是稳定的公开只读接口。前两者的记录在 `items`，成交概览在指标 JSON 的 `summary`。真实选币名单只参与云端临时计算；公开指标明细不含 `selected` 标记，AI 不应尝试推断内部名单或给出投资建议。

## 自动更新

GitHub Actions 每天运行公告抓取和成交统计，分别校验现货、永续合约与四项成交指标。任一接口出现空关键字段、类型混收、成交额合计或占比公式不一致时，任务会明确报错并停止发布。
