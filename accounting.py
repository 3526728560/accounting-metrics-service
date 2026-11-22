"""记账业务埋点接收服务（小程序 / Serverless 业务）
=====================================================
用途：
  1. 接收小程序记账成功后的旁路上报，累加 accounting_* 指标；
  2. 暴露 :9300/metrics 供 Prometheus 采集；
  3. 提供 /api/accounting/* 模拟接口，用于故障注入与本地演示。

数据流：
  小程序记账成功 -> 云函数 / 前端 -> POST /report -> 本服务累加指标
                                                -> Prometheus 抓取 /metrics
                                                -> Alertmanager 按规则告警
"""
import time
from flask import Flask, jsonify, request
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# ---------------- accounting_* 业务指标（白盒埋点） ----------------
RECORDS = Counter("accounting_records_total", "累计记账笔数", ["type", "category"])
RECORD_AMOUNT = Counter("accounting_amount_cents_total", "累计记账金额(分)", ["type"])
PHOTO_UPLOADS = Counter("accounting_photo_uploads_total", "累计凭证图片上传数")
UPLOAD_FAILURES = Counter("accounting_upload_failures_total", "图片上传失败次数")
API_DURATION = Histogram("accounting_api_duration_seconds", "接口耗时(秒)", ["endpoint"])
LAST_REPORT_TS = Gauge("accounting_last_report_timestamp_seconds", "最近一次真实埋点上报时间戳")

# 记账请求速率告警用的内部计数（真实上报 + 模拟接口都会累加）
REPORT_TOTAL = Counter("accounting_reports_total", "累计收到的埋点上报次数")


@app.before_request
def _before():
    request.start = time.time()


@app.after_request
def _after(resp):
    dt = time.time() - request.start
    API_DURATION.labels(endpoint=request.path).observe(dt)
    return resp


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ============ 核心接口：接收真实小程序云函数的埋点上报 ============
@app.route("/report", methods=["POST"])
def report():
    """
    真实小程序（微信云函数 addRecord）记账成功后的埋点上报。
    body 示例：{"type":"expense","category":"dining","amount_cents":2500,
                "has_photo":true,"photo_count":2}
    """
    data = request.get_json(silent=True) or {}

    rtype = data.get("type", "expense")
    if rtype not in ("expense", "income"):
        rtype = "expense"
    category = str(data.get("category", "other"))
    amount_cents = int(data.get("amount_cents", 0) or 0)
    photo_count = int(data.get("photo_count", 0) or 0)

    RECORDS.labels(type=rtype, category=category).inc()
    if amount_cents > 0:
        RECORD_AMOUNT.labels(type=rtype).inc(amount_cents)
    if photo_count > 0:
        PHOTO_UPLOADS.inc(photo_count)

    REPORT_TOTAL.inc()
    LAST_REPORT_TS.set(time.time())
    return jsonify({"ok": True})


# ============ 模拟接口：故障注入 / 备用演示 ============
@app.route("/api/accounting/record", methods=["POST"])
def record():
    """模拟「记一笔」（备用：真实小程序不可用时仍可演示指标增长）"""
    data = request.get_json(silent=True) or {}
    rtype = data.get("type", "expense")
    if rtype not in ("expense", "income"):
        rtype = "expense"
    RECORDS.labels(type=rtype, category=str(data.get("category", "other"))).inc()
    REPORT_TOTAL.inc()
    LAST_REPORT_TS.set(time.time())
    return jsonify({"ok": True, "type": rtype})


@app.route("/api/accounting/photo-fail", methods=["POST"])
def photo_fail():
    """模拟上传失败，用于触发 AccountingUploadErrors 告警"""
    UPLOAD_FAILURES.inc()
    return jsonify({"error": "upload failed"}), 500


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9300, threaded=True)
