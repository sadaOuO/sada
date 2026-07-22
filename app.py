from flask import Flask, request, abort
import hashlib
import hmac
import base64
import json
import math
import re
import os
import requests
from datetime import datetime

# 讀取 .env
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

app = Flask(__name__)
ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
print(f"TOKEN length: {len(ACCESS_TOKEN)}")

room_data = {}

def get_room(room_id):
    if room_id not in room_data:
        room_data[room_id] = {"total": 0.0, "history": [], "currency": "台幣"}
    return room_data[room_id]

def get_room_id(source):
    if source.get("type") == "group":
        return "group_" + source.get("groupId", "unknown")
    elif source.get("type") == "room":
        return "room_" + source.get("roomId", "unknown")
    else:
        return "user_" + source.get("userId", "unknown")

def fmtn(n):
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return f"{n:,}"

def verify_signature(body, signature):
    mac = hmac.new(CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature)

def reply_message(reply_token, messages):
    if isinstance(messages, str):
        messages = [{"type": "text", "text": messages}]
    resp = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ACCESS_TOKEN}"},
        json={"replyToken": reply_token, "messages": messages},
    )
    print(f"Reply: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error: {resp.text}")

# ── Flex 計算結果卡片 ──────────────────────────────────────
def make_calc_flex(expr, result, prev_total, new_total, currency, note=""):
    sign = "+" if result >= 0 else ""
    color_result = "#FF6B35" if result >= 0 else "#2196F3"

    bubble = {
        "type": "bubble", "size": "kilo",
        "header": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "計算結果", "weight": "bold", "size": "lg", "color": "#FF6B35"},
                {"type": "text", "text": f"{sign}{expr}={fmtn(result)}", "size": "sm", "color": color_result, "margin": "sm"}
            ],
            "backgroundColor": "#FFF8F5", "paddingAll": "16px"
        },
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "上次金額", "size": "sm", "color": "#888888", "flex": 1},
                    {"type": "text", "text": f"{fmtn(prev_total)} {currency}", "size": "sm", "color": "#FF6B35", "align": "end", "flex": 2}
                ], "margin": "sm"},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "本次金額", "size": "sm", "color": "#888888", "flex": 1},
                    {"type": "text", "text": f"{sign}{fmtn(result)} {currency}", "size": "sm", "color": color_result, "align": "end", "flex": 2}
                ], "margin": "sm"},
                {"type": "separator", "margin": "md"},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "目前欠佐田", "size": "sm", "weight": "bold", "flex": 1},
                    {"type": "text", "text": f"{fmtn(new_total)} {currency}", "size": "sm", "weight": "bold", "color": "#FF6B35", "align": "end", "flex": 2}
                ], "margin": "md"},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "備註", "size": "sm", "color": "#888888", "flex": 1},
                    {"type": "text", "text": note if note else " ", "size": "sm",
                     "color": "#FF6B35" if note else "#CCCCCC", "align": "end", "flex": 2, "wrap": True}
                ], "margin": "sm"}
            ],
            "paddingAll": "16px"
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [{"type": "text", "text": "powered by 佐田", "size": "xs", "color": "#AAAAAA", "align": "end"}],
            "paddingAll": "8px"
        }
    }
    return {"type": "flex", "altText": f"計算結果：欠佐田 {fmtn(new_total)} {currency}", "contents": bubble}

# ── 整合（文字格式，穩定不出錯）──────────────────────────
def make_summary_text(history, total, currency):
    if not history:
        return f"目前沒有紀錄 📭\n總額：{fmtn(total)} {currency}"

    lines = ["📋 記帳整合", "━━━━━━━━━━━━━"]
    for i, h in enumerate(history[-15:]):
        note_str = f" 📝{h['note']}" if h.get('note') else ""
        sign = "+" if h['result'] >= 0 else ""
        lines.append(f"{i+1}. {h['time'].replace(chr(10),' ')}｜{h['expr']}={fmtn(h['result'])}{note_str}")

    lines.append("━━━━━━━━━━━━━")
    lines.append(f"✅ 總額：{fmtn(total)} {currency}")
    return "\n".join(lines)

# ── 計算引擎 ──────────────────────────────────────────────
def safe_calc(expr):
    expr = expr.replace("×", "*").replace("÷", "/")
    expr = expr.replace("（", "(").replace("）", ")")
    expr = expr.replace("^", "**")
    expr = re.sub(r"\bsqrt\b", "math.sqrt", expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bpi\b", "math.pi", expr, flags=re.IGNORECASE)
    allowed = re.compile(r"^[\d\s\+\-\*\/\.\(\)\%\,math\.sqrtpie]+$")
    if not allowed.match(expr):
        return None, "不支援的字元"
    try:
        result = eval(expr, {"__builtins__": {}}, {"math": math})
        if not isinstance(result, (int, float)) or math.isnan(result) or math.isinf(result):
            return None, "無法計算"
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return result, None
    except ZeroDivisionError:
        return None, "除以零"
    except:
        return None, "無法解析"

# ── 指令處理 ──────────────────────────────────────────────
def handle(room_id, text):
    rd = get_room(room_id)
    text = text.strip()
    currency = rd["currency"]

    if text in ("說明", "help", "?", "？"):
        return [{"type": "text", "text": (
            "📖 佐田記帳計算機\n"
            "━━━━━━━━━━━━━\n"
            "➕ 加錢：+100\n"
            "➖ 扣錢：-50\n"
            "📝 備註：+100 買飯\n"
            "🔢 算式：+10*3\n\n"
            "📌 指令：\n"
            "  /整合 → 查看記帳紀錄\n"
            "  /撤回 → 取消上一筆\n"
            "  /清帳 → 歸零\n"
            "  /查帳 → 目前總額\n"
            "  /台幣 → 切換台幣\n"
            "  /人民幣 → 切換人民幣"
        )}]

    if text in ("/台幣", "台幣"):
        rd["currency"] = "台幣"
        return [{"type": "text", "text": "✅ 已切換為台幣"}]

    if text in ("/人民幣", "人民幣"):
        rd["currency"] = "人民幣"
        return [{"type": "text", "text": "✅ 已切換為人民幣"}]

    if text in ("/查帳", "查帳", "/總計", "總計"):
        return [{"type": "text", "text": f"📊 目前總額：{fmtn(rd['total'])} {currency}"}]

    if text in ("/整合", "整合"):
        return [{"type": "text", "text": make_summary_text(rd["history"], rd["total"], currency)}]

    if text in ("/清帳", "清帳"):
        rd["total"] = 0.0
        rd["history"] = []
        return [{"type": "text", "text": "🗑️ 已清帳，總額歸零！"}]

    if text in ("/撤回", "撤回", "undo"):
        if not rd["history"]:
            return [{"type": "text", "text": "❌ 沒有可以撤回的紀錄"}]
        last = rd["history"].pop()
        rd["total"] -= last["result"]
        total = rd["total"]
        if isinstance(total, float) and total.is_integer():
            total = int(total)
        return [{"type": "text", "text": f"↩️ 已撤回：{last['expr']} = {fmtn(last['result'])}\n目前總額：{fmtn(total)} {currency}"}]

    # 計算（數字/算式 + 備註）
    match = re.match(r'^([\+\-]?[\d\.\+\-\*\/\(\)\^]+)(.*)?$', text)
    if match:
        calc_part = match.group(1).strip()
        note = match.group(2).strip() if match.group(2) else ""
        result, error = safe_calc(calc_part)
        if error is None:
            prev_total = rd["total"]
            if isinstance(prev_total, float) and prev_total.is_integer():
                prev_total = int(prev_total)
            rd["total"] += result
            new_total = rd["total"]
            if isinstance(new_total, float) and new_total.is_integer():
                new_total = int(new_total)
            now = datetime.now().strftime("%m/%d %H:%M")
            rd["history"].append({"time": now, "expr": calc_part, "result": result, "note": note})
            return [make_calc_flex(calc_part.lstrip("+"), result, prev_total, new_total, currency, note)]

    return [{"type": "text", "text": "❓ 無法識別指令\n輸入「說明」查看使用方式"}]

# ── Webhook ───────────────────────────────────────────────
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    if not verify_signature(body, signature):
        abort(400)
    events = json.loads(body).get("events", [])
    for event in events:
        etype = event.get("type")
        if etype == "join":
            reply_token = event.get("replyToken")
            if reply_token:
                reply_message(reply_token, [{"type": "text", "text": "👋 佐田記帳計算機已加入！\n輸入「說明」查看使用方式"}])
            continue
        if etype == "message" and event["message"].get("type") == "text":
            source = event.get("source", {})
            room_id = get_room_id(source)
            reply_token = event["replyToken"]
            text = event["message"]["text"]
            print(f"Room {room_id[:15]}: {text}")
            messages = handle(room_id, text)
            reply_message(reply_token, messages)
    return "OK"

@app.route("/", methods=["GET"])
def index():
    return "✅ 佐田記帳計算機 運行中！"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
