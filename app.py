from flask import Flask, request, abort
import hashlib
import hmac
import base64
import json
import math
import re
import os
import requests
from datetime import datetime, timedelta

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
SUPER_ADMIN_ID = os.environ.get("SUPER_ADMIN_ID", "")  # 你的 LINE user ID
print(f"TOKEN length: {len(ACCESS_TOKEN)}")

# ── 資料儲存 ───────────────────────────────────────────────
# 管理員 { userid: {"name": "佐田", "role": "super"/"admin", "expire": None/datetime} }
admins = {}
if SUPER_ADMIN_ID:
    admins[SUPER_ADMIN_ID] = {"name": "佐田", "role": "super", "expire": None}
    print(f"[INIT] SUPER_ADMIN_ID 已載入：{SUPER_ADMIN_ID}")
else:
    print("[WARN] 未設定 SUPER_ADMIN_ID，將沒有最高管理員！")

# 群組資料 { room_id: {"total": 0.0, "history": [], "currency": "台幣", "boss": "", "enabled": True} }
room_data = {}

def get_room(room_id):
    if room_id not in room_data:
        room_data[room_id] = {"total": 0.0, "history": [], "currency": "台幣", "boss": "佐田", "enabled": True}
    return room_data[room_id]

def get_room_id(source):
    if source.get("type") == "group":
        return "group_" + source.get("groupId", "unknown")
    elif source.get("type") == "room":
        return "room_" + source.get("roomId", "unknown")
    else:
        return "user_" + source.get("userId", "unknown")

def fmtn(n):
    if isinstance(n, float):
        n = round(n, 2)
        if n.is_integer():
            n = int(n)
    return f"{n:,}"

# ── 權限檢查 ───────────────────────────────────────────────
def is_super(uid):
    return uid in admins and admins[uid]["role"] == "super"

def is_admin(uid):
    if uid not in admins:
        return False
    admin = admins[uid]
    if admin["role"] == "super":
        return True
    # 檢查是否過期
    if admin["expire"] and datetime.now() > admin["expire"]:
        return False
    return True

def check_expired_admins():
    """通知即將到期的管理員（可選）"""
    now = datetime.now()
    for uid, info in list(admins.items()):
        if info["expire"] and now > info["expire"] and info["role"] != "super":
            pass  # 已過期，不刪除只是擋住

# ── 簽名驗證 ───────────────────────────────────────────────
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

# ── Flex 計算結果卡片 ───────────────────────────────────────
def make_calc_flex(expr, result, prev_total, new_total, currency, boss, note=""):
    sign = "+" if result >= 0 else ""
    color_result = "#FF6B35" if result >= 0 else "#2196F3"
    if new_total >= 0:
        total_label = f"目前欠{boss}"
        total_display = new_total
    else:
        total_label = f"{boss}欠"
        total_display = -new_total

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
                    {"type": "text", "text": total_label, "size": "sm", "weight": "bold", "flex": 1},
                    {"type": "text", "text": f"{fmtn(total_display)} {currency}", "size": "sm", "weight": "bold", "color": "#FF6B35", "align": "end", "flex": 2}
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
    return {"type": "flex", "altText": f"計算結果：{total_label} {fmtn(total_display)} {currency}", "contents": bubble}

# ── 整合文字 ───────────────────────────────────────────────
def make_summary_text(history, total, currency, boss):
    if not history:
        return f"目前沒有紀錄 📭\n總額：{fmtn(total)} {currency}"
    lines = ["📋 記帳整合", "━━━━━━━━━━━━━"]
    for i, h in enumerate(history[-15:]):
        note_str = f" 📝{h['note']}" if h.get('note') else ""
        sign = "+" if h['result'] >= 0 else ""
        lines.append(f"{i+1}. {h['time']}｜{h['expr']}={fmtn(h['result'])}{note_str}")
    lines.append("━━━━━━━━━━━━━")
    lines.append(f"✅ 總額：{fmtn(total)} {currency}")
    return "\n".join(lines)

# ── 計算引擎 ───────────────────────────────────────────────
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

# ── 指令處理 ───────────────────────────────────────────────
def handle(uid, room_id, text):
    text = text.strip()

    # ── 任何人都能用 ──
    # /ID
    if text in ("/ID", "/id"):
        return [{"type": "text", "text": f"你的LINE ID：\n{uid}"}]

    # 說明
    if text in ("說明", "help", "?", "？"):
        if is_admin(uid):
            return [{"type": "text", "text": (
                "📖 佐田記帳計算機\n"
                "━━━━━━━━━━━━━\n"
                "➕ 加錢：+100\n"
                "➖ 扣錢：-50\n"
                "📝 備註：+100 買飯\n\n"
                "📌 記帳指令：\n"
                "  /整合 → 查看紀錄\n"
                "  /撤回 → 取消上一筆\n"
                "  /查帳 → 目前總額\n"
                "  /洁帳 → 歸零\n"
                "  /台幣 /人民幣 → 切換幣別\n\n"
                "📌 管理指令：\n"
                "  /ID → 查看自己的ID\n"
                "  /管理員 → 查看管理員列表\n"
                "  /所有 → 查看所有群組帳"
            )}]
        else:
            return [{"type": "text", "text": "❌ 你沒有使用權限\n請聯繫管理員授權\n\n傳送 /ID 取得你的ID"}]

    # ── 最高管理員專用 ──
    # 0421@新增主管理員@名字@userid
    if text.startswith("0421@新增主管理員@") and is_super(uid):
        parts = text.split("@")
        if len(parts) == 4:
            name = parts[2]
            new_uid = parts[3].strip()
            admins[new_uid] = {"name": name, "role": "super", "expire": None}
            return [{"type": "text", "text": f"✅ 已新增最高管理員\n名字：{name}\nID：{new_uid}"}]
        else:
            return [{"type": "text", "text": "❌ 格式錯誤\n正確格式：\n0421@新增主管理員@名字@userid"}]

    # @新增副管理員@名字@userid@天數
    if text.startswith("@新增副管理員@") and is_super(uid):
        parts = text.split("@")
        if len(parts) == 5:
            name = parts[2]
            new_uid = parts[3].strip()
            try:
                days = int(parts[4])
            except ValueError:
                return [{"type": "text", "text": "❌ 天數必須是數字\n正確格式：\n@新增副管理員@名字@userid@天數"}]
            expire = datetime.now() + timedelta(days=days)
            admins[new_uid] = {"name": name, "role": "admin", "expire": expire}
            expire_str = expire.strftime("%Y/%m/%d")
            return [{"type": "text", "text": f"✅ 已新增副管理員\n名字：{name}\nID：{new_uid}\n到期時間：{expire_str}"}]
        else:
            return [{"type": "text", "text": f"❌ 格式錯誤（需要{5}段，目前{len(parts)}段）\n正確格式：\n@新增副管理員@名字@userid@天數\n例如：\n@新增副管理員@AK@U開頭的ID@30"}]

    # @刪除管理員@名字
    if text.startswith("@刪除管理員@") and is_super(uid):
        name = text.split("@")[2]
        deleted = None
        for k, v in list(admins.items()):
            if v["name"] == name and v["role"] != "super":
                deleted = k
                del admins[k]
                break
        if deleted:
            return [{"type": "text", "text": f"✅ 已刪除管理員：{name}"}]
        else:
            return [{"type": "text", "text": f"❌ 找不到管理員：{name}"}]

    # /管理員
    if text == "/管理員" and is_admin(uid):
        if not admins:
            return [{"type": "text", "text": "目前沒有管理員"}]
        lines = ["👥 管理員列表", "━━━━━━━━━━━━━"]
        for k, v in admins.items():
            role = "👑最高" if v["role"] == "super" else "🔑副"
            expire = v["expire"].strftime("%Y/%m/%d") if v["expire"] else "永久"
            lines.append(f"{role} {v['name']}\n  到期：{expire}\n  ID：{k[:16]}...")
        return [{"type": "text", "text": "\n".join(lines)}]

    # /所有
    if text == "/所有" and is_super(uid):
        if not room_data:
            return [{"type": "text", "text": "目前沒有任何群組帳"}]
        lines = ["📊 所有群組帳", "━━━━━━━━━━━━━"]
        for rid, rd in room_data.items():
            lines.append(f"群組：{rid[-8:]}\n總額：{fmtn(rd['total'])} {rd['currency']}")
        return [{"type": "text", "text": "\n".join(lines)}]

    # 刪除群帳@群組名稱（用room_id後8碼）
    if text.startswith("刪除群帳@") and is_super(uid):
        target = text.split("@")[1]
        deleted = None
        for rid in list(room_data.keys()):
            if rid.endswith(target) or rid == target:
                del room_data[rid]
                deleted = rid
                break
        if deleted:
            return [{"type": "text", "text": f"✅ 已刪除群組帳：{target}"}]
        else:
            return [{"type": "text", "text": f"❌ 找不到群組：{target}"}]

    # ── 權限檢查 ──
    if not is_admin(uid):
        # 檢查是否過期
        if uid in admins and admins[uid]["expire"] and datetime.now() > admins[uid]["expire"]:
            name = admins[uid]["name"]
            return [{"type": "text", "text": f"{name}\n{uid}\n副管理員已到期"}]
        return None  # 無權限靜默不回應

    # ── 管理員 + 副管理員都能用 ──
    rd = get_room(room_id)
    currency = rd["currency"]
    boss = rd["boss"]

    # 設定群組資訊 /設定群組資訊@老闆名稱@幣別
    if text.startswith("/設定群組資訊@"):
        parts = text.split("@")
        if len(parts) >= 3:
            rd["boss"] = parts[1]
            rd["currency"] = parts[2]
            return [{"type": "text", "text": f"✅ 群組資訊已設定\n老闆：{parts[1]}\n幣別：{parts[2]}"}]

    # 刪除群組資訊
    if text == "/刪除群組資訊":
        rd["boss"] = "佐田"
        rd["currency"] = "台幣"
        return [{"type": "text", "text": "✅ 群組資訊已重置"}]

    # 切換幣別
    if text in ("/台幣", "台幣"):
        rd["currency"] = "台幣"
        return [{"type": "text", "text": "✅ 已切換為台幣"}]
    if text in ("/人民幣", "人民幣"):
        rd["currency"] = "人民幣"
        return [{"type": "text", "text": "✅ 已切換為人民幣"}]

    # 查帳
    if text in ("/查帳", "查帳"):
        return [{"type": "text", "text": f"📊 目前總額：{fmtn(rd['total'])} {currency}"}]

    # 整合
    if text in ("/整合", "整合"):
        return [{"type": "text", "text": make_summary_text(rd["history"], rd["total"], currency, boss)}]

    # 洁帳/清帳
    if text in ("/洁帳", "洁帳", "/清帳", "清帳"):
        rd["total"] = 0.0
        rd["history"] = []
        return [{"type": "text", "text": "🗑️ 已清帳，總額歸零！"}]

    # 撤回
    if text in ("/撤回", "撤回"):
        if not rd["history"]:
            return [{"type": "text", "text": "❌ 沒有可以撤回的紀錄"}]
        last = rd["history"].pop()
        rd["total"] -= last["result"]
        total = rd["total"]
        if isinstance(total, float) and total.is_integer():
            total = int(total)
        return [{"type": "text", "text": f"↩️ 已撤回：{last['expr']} = {fmtn(last['result'])}\n目前總額：{fmtn(total)} {currency}"}]

    # 計算
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
            return [make_calc_flex(calc_part.lstrip("+"), result, prev_total, new_total, currency, boss, note)]

    return None  # 不認識的指令靜默

# ── Webhook ────────────────────────────────────────────────
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
                reply_message(reply_token, [{"type": "text", "text": "👋 佐田記帳計算機已加入！\n傳送 /ID 取得你的ID\n傳送「說明」查看使用方式"}])
            continue
        if etype == "message" and event["message"].get("type") == "text":
            source = event.get("source", {})
            uid = source.get("userId", "")
            room_id = get_room_id(source)
            reply_token = event["replyToken"]
            text = event["message"]["text"]
            print(f"User {uid} | is_admin={is_admin(uid)} | is_super={is_super(uid)} | text={text}")
            messages = handle(uid, room_id, text)
            if messages:
                reply_message(reply_token, messages)
    return "OK"

@app.route("/", methods=["GET"])
def index():
    return "✅ 佐田記帳計算機 運行中！"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
