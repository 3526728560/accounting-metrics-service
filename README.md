# Accounting Metrics · 小程序记账埋点服务

给微信云开发（Serverless）记账小程序做**旁路埋点**的轻量接收服务：小程序记完一笔账，顺手 POST 一条事件；服务把事件累加成 `accounting_*` 指标，通过 `/metrics` 暴露给 Prometheus。

不侵入记账主流程，不依赖云函数，本地一条 Compose 命令即可跑起来。

## 特性

- **旁路不阻塞**：上报是 fire-and-forget，不等待返回，2 秒超时，失败静默
- **零依赖接入**：小程序端只需一个 `wx.request`，`urlCheck` 关掉即可走局域网 HTTP
- **指标成族**：业务量、金额、凭证图片、接口时延、最后上报时间，一次上报全部覆盖
- **可直接告警**：配套 4 条规则（服务不可用 / 上传失败 / 长时间无上报 / P95 超时）
- **故障可注入**：内置模拟接口，本地就能验证告警闭环

## 快速开始

```bash
docker compose up -d --build
curl -s http://127.0.0.1:9300/health
curl -s http://127.0.0.1:9300/metrics | grep accounting_

# 模拟一笔支出（餐饮 25 元，2 张凭证）
curl -s -X POST http://127.0.0.1:9300/report \
  -H "Content-Type: application/json" \
  -d '{"type":"expense","category":"dining","amount_cents":2500,"photo_count":2}'
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/report` | **核心**：接收小程序上报 `{type, category, amount_cents, photo_count}` |
| POST | `/api/accounting/record` | 模拟记一笔（不含金额），用于本地造数据 |
| POST | `/api/accounting/photo-fail` | 模拟图片上传失败，返回 500，用于触发告警 |
| GET | `/metrics` | Prometheus 指标 |

## 指标

| 指标 | 类型 | 标签 | 含义 |
|---|---|---|---|
| `accounting_records_total` | Counter | `type`, `category` | 累计记账笔数 |
| `accounting_amount_cents_total` | Counter | `type` | 累计金额（分） |
| `accounting_photo_uploads_total` | Counter | — | 累计凭证图片数 |
| `accounting_upload_failures_total` | Counter | — | 图片上传失败次数 |
| `accounting_api_duration_seconds` | Histogram | `endpoint` | 接口耗时 |
| `accounting_last_report_timestamp_seconds` | Gauge | — | 最近一次上报时间戳 |
| `accounting_reports_total` | Counter | — | 累计收到的上报次数 |

## 小程序端接入

`utils/tracker.js`：

```js
let BASE = 'http://192.168.1.100:9300'   // 埋点服务地址，未配置则不上报

function report(kind, payload) {
  if (!BASE) return                      // 开关：不配地址 = 零开销
  wx.request({
    url: BASE + '/report',
    method: 'POST',
    data: Object.assign({ kind }, payload),
    timeout: 2000,                       // 2 秒超时
    fail() {},                           // 失败静默，绝不影响记账
  })
}

module.exports = { report }
```

在记账成功之后调用（**不要**放在写库之前）：

```js
tracker.report('record_add', {
  type: 'expense',
  category: 'dining',
  amount_cents: 2500,
  photo_count: 2,
})
```

工程配置 `project.config.json` 里把 `urlCheck` 设为 `false`，才能在开发者工具里请求局域网 HTTP 地址。

更完整的接入说明见 [docs/integration.md](docs/integration.md)。

## 接入 Prometheus

```yaml
scrape_configs:
  - job_name: accounting-app
    static_configs:
      - targets: ['192.168.1.100:9300']
```

告警规则把 `alert-rules-accounting.yml` 中的 `accounting` 组追加到 `alert-rules.yml` 即可：

| 告警 | 级别 | 条件 |
|---|---|---|
| AccountingAppDown | critical | `up{job="accounting-app"} == 0`，1m |
| AccountingUploadErrors | warning | 5m 内 `accounting_upload_failures_total` 增长 |
| AccountingNoReports | warning | 10m 无上报 |
| AccountingSlowApi | warning | P95 > 1s |

## 设计原则

1. **旁路而非串行**：上报不进入记账事务，失败不影响业务
2. **失败静默**：超时、网络错误全部吞掉，避免监控链路成为单点
3. **开关可控**：地址为空即关闭，未接入时零开销
4. **同构复用**：与任意 Flask + prometheus-client 服务同一套埋点写法，只换指标前缀

## License

MIT
