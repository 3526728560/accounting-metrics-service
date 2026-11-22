# 小程序端接入说明

本文说明如何把记账事件从微信小程序上报到本服务，并验证指标生效。

## 1. 为什么走旁路上报

小程序运行在微信云开发（Serverless）环境，业务代码在云函数里，主机指标、容器指标都拿不到。最轻的做法是在**业务成功之后**补一条 HTTP 事件，由自建服务转成 Prometheus 指标：

```
小程序页面 → 云函数 addRecord（写库成功） → 前端 POST /report → 本服务累加指标
                                                            → Prometheus 抓取
                                                            → Alertmanager 告警
```

三个约束：

- 只在**成功之后**上报，失败记账不能产生业务量指标
- 上报**不参与**业务事务，超时/失败均静默
- 上报地址做成配置项，留空即关闭

## 2. 服务端准备

```bash
docker compose up -d --build
docker compose ps                      # accounting-metrics 状态 Up
curl -s http://127.0.0.1:9300/health    # {"status":"ok"}
```

查本机局域网地址（手机与电脑需在同一 WiFi）：

```bash
ip addr show | grep 'inet 192'
```

记下形如 `192.168.1.100` 的地址。

## 3. 小程序端

新建 `utils/tracker.js`：

```js
let BASE = 'http://192.168.1.100:9300'

function report(kind, payload) {
  if (!BASE) return
  wx.request({
    url: BASE + '/report',
    method: 'POST',
    data: Object.assign({ kind }, payload),
    timeout: 2000,
    fail() {},
    complete() {},
  })
}

module.exports = { report }
```

页面里记账成功后调用：

```js
await db.collection('records').add({ data: record })
tracker.report('record_add', {
  type: record.type,                 // expense | income
  category: record.category,         // dining / traffic / shopping ...
  amount_cents: record.amount_cents, // 单位：分
  photo_count: record.photos ? record.photos.length : 0,
})
```

`project.config.json`：

```json
{
  "setting": {
    "urlCheck": false
  }
}
```

`urlCheck: false` 只影响开发者工具；真机预览需要在「详情 → 本地设置」里勾选"不校验合法域名"。

## 4. 验证

```bash
# 指标是否出现
curl -s http://127.0.0.1:9300/metrics | grep -E 'accounting_(records|amount|photo|reports)'

# 手动补一笔，便于核对数值
curl -s -X POST http://127.0.0.1:9300/report \
  -H "Content-Type: application/json" \
  -d '{"type":"income","category":"salary","amount_cents":520000,"photo_count":0}'
```

核对口径：`accounting_records_total` 按 `type` + `category` 分标签累加；`accounting_amount_cents_total` 只累计 `amount_cents > 0` 的金额；`accounting_photo_uploads_total` 按 `photo_count` 累加。

## 5. 故障注入

```bash
# 上传失败告警（约 1 分钟后 AccountingUploadErrors 触发）
curl -s -X POST http://127.0.0.1:9300/api/accounting/photo-fail \
  -H "Content-Type: application/json" -d '{}'

# 服务不可用告警（critical，约 1 分钟）
docker compose stop accounting
docker compose up -d accounting
```

排查顺序：先看 `/metrics` 有没有指标 → 再看 Prometheus `targets` 是否为 UP → 最后看 Alertmanager 是否收到告警。

## 6. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `/metrics` 无 `accounting_*` | 没有真实上报 | 先用 `/report` 手动补一条 |
| 小程序请求失败 | 开发者工具域名校验 | `urlCheck: false` |
| 手机上报不到 | 不在同一局域网 / 防火墙 | 换同网段 WiFi，放行 9300 |
| 用户数/笔数只增不减 | Counter 语义 | 需要"当前值"时用 `increase()` 或另建 Gauge |
