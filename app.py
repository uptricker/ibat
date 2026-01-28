import os
import threading
import time
import random
import json
import uuid
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, RateLimitError, ClientError, ClientForbiddenError, 
    ClientNotFoundError, ChallengeRequired, PleaseWaitFewMinutes
)

app = Flask(__name__)

# Global variables
BOT_THREAD = None
STOP_EVENT = threading.Event()
LOGS = []
START_TIME = None
CLIENT = None
SESSION_TOKEN = None
LOGIN_SUCCESS = False
CURRENT_TASK_ID = None
RUNNING_BOTS = {}

STATS = {
    "total_welcomed": 0,
    "today_welcomed": 0,
    "last_reset": datetime.now().date()
}

# COMMANDS BUILT-IN - NO EXTERNAL FILE NEEDED
COMMANDS_CONFIG = {
    "admin_commands": {
        "/spam": "Spam user - /spam @user message",
        "/stopspam": "Stop spam", 
        "/kill": "Kill bot"
    },
    "public_commands": {
        "/ping": "Check bot alive",
        "/uptime": "Running time",
        "/help": "Show commands"
    },
    "spam_active": {},
    "target_spam": {}
}

def generate_task_id():
    return str(uuid.uuid4())[:8].upper()

def uptime():
    if not START_TIME: return "00:00:00"
    delta = datetime.now() - START_TIME
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    lm = f"[{ts}] {msg}"
    LOGS.append(lm)
    if len(LOGS) > 1000: LOGS[:] = LOGS[-1000:]
    print(lm)

def clear_logs():
    global LOGS
    LOGS.clear()
    log("🧹 Logs cleared!")

def create_stable_client():
    cl = Client()
    cl.delay_range = [8, 15]
    cl.request_timeout = 90
    cl.max_retries = 1
    ua = "Instagram 380.0.0.28.104 Android (35/14; 600dpi; 1440x3360; samsung; SM-S936B; dm5q; exynos2500; en_IN; 380000028)"
    cl.set_user_agent(ua)
    return cl

def safe_login(cl, token):
    global LOGIN_SUCCESS, SESSION_TOKEN
    try:
        log("🔐 Login attempt...")
        cl.login_by_sessionid(token)
        account = cl.account_info()
        if account and account.username:
            log(f"✅ Login SUCCESS: @{account.username}")
            LOGIN_SUCCESS = True
            SESSION_TOKEN = token
            time.sleep(3)
            return True, account.username
    except Exception as e:
        log(f"❌ Login failed: {str(e)[:50]}")
    return False, None

def session_health_check():
    global CLIENT, LOGIN_SUCCESS
    try:
        if CLIENT: CLIENT.account_info()
        return True
    except: 
        LOGIN_SUCCESS = False
        return False

def process_command(gid, msg_obj, thread, admin_ids):
    try:
        if not msg_obj or not hasattr(msg_obj, 'user_id'): return
        sender = next((u for u in thread.users if u.pk == msg_obj.user_id), None)
        if not sender or not sender.username: return
        
        text = (msg_obj.text or "").strip().lower()
        sender_username = sender.username.lower()
        is_admin = sender_username in [aid.lower() for aid in admin_ids]
        
        # ADMIN COMMANDS
        if is_admin:
            if text.startswith('/spam '):
                parts = msg_obj.text.split(" ", 2)
                if len(parts) == 3:
                    COMMANDS_CONFIG["target_spam"][gid] = {
                        "username": parts[1].replace("@", ""),
                        "message": parts[2]
                    }
                    COMMANDS_CONFIG["spam_active"][gid] = True
                    CLIENT.direct_send("🔥 Spam ON!", thread_ids=[gid])
                    return
            elif text == '/stopspam':
                COMMANDS_CONFIG["spam_active"][gid] = False
                CLIENT.direct_send("🛑 Spam OFF!", thread_ids=[gid])
                return
            elif text == '/kill':
                global STOP_EVENT
                STOP_EVENT.set()
                CLIENT.direct_send("💀 Bot killed!", thread_ids=[gid])
                return
        
        # PUBLIC COMMANDS
        if text == '/ping':
            CLIENT.direct_send(f"🏓 Pong! Uptime: {uptime()}", thread_ids=[gid])
        elif text == '/uptime':
            CLIENT.direct_send(f"⏱️ Uptime: {uptime()}", thread_ids=[gid])
        elif text == '/help':
            help_msg = "📋 COMMANDS:
/ping
/uptime
/help"
            if is_admin:
                help_msg += "

👑 ADMIN:
/spam @user msg
/stopspam
/kill"
            CLIENT.direct_send(help_msg, thread_ids=[gid])
    except: pass

def run_bot(task_id, session_token, wm, gids, dly, pol, ucn, admin_ids):
    global START_TIME, CLIENT, LOGIN_SUCCESS, CURRENT_TASK_ID
    
    CURRENT_TASK_ID = task_id
    START_TIME = datetime.now()
    RUNNING_BOTS[task_id] = {"status": "running", "start_time": START_TIME}
    
    log(f"🚀 TaskID: {task_id} - Bot STARTED!")
    
    CLIENT = create_stable_client()
    success, username = safe_login(CLIENT, session_token)
    if not success:
        log("💥 Login failed - Bot STOPPED")
        RUNNING_BOTS[task_id]["status"] = "failed"
        return
    
    km = {gid: set() for gid in gids}
    lm = {gid: None for gid in gids}
    
    log(f"📱 Initializing {len(gids)} groups...")
    for i, gid in enumerate(gids):
        try:
            time.sleep(5)
            thread = CLIENT.direct_thread(gid)
            km[gid] = {u.pk for u in thread.users}
            if thread.messages: lm[gid] = thread.messages[0].id
            log(f"✅ Group {i+1}: {gid[:12]}...")
        except Exception as e:
            log(f"⚠️ Group {i+1} error: {str(e)[:30]}")
    
    log(f"🎉 TaskID: {task_id} - Bot running!")
    
    while not STOP_EVENT.is_set():
        for gid in gids:
            if STOP_EVENT.is_set(): break
            
            try:
                if not session_health_check():
                    log("🔄 Session refresh...")
                    break
                
                time.sleep(random.uniform(10, 20))
                thread = CLIENT.direct_thread(gid)
                
                # Check new messages/commands
                if lm[gid] and thread.messages:
                    new_msgs = []
                    for msg in thread.messages[:10]:
                        if msg.id == lm[gid]: break
                        new_msgs.append(msg)
                    
                    for msg_obj in reversed(new_msgs[:3]):
                        process_command(gid, msg_obj, thread, admin_ids)
                    
                    if thread.messages: lm[gid] = thread.messages[0].id

                # Spam command
                if COMMANDS_CONFIG["spam_active"].get(gid):
                    target = COMMANDS_CONFIG["target_spam"].get(gid)
                    if target:
                        try:
                            msg = f"@{target['username']} {target['message']}"
                            CLIENT.direct_send(msg, thread_ids=[gid])
                            time.sleep(3)
                        except: pass

                # Welcome new users
                current_members = {u.pk for u in thread.users}
                new_users = current_members - km[gid]
                
                for user in thread.users:
                    if user.pk in new_users and user.username:
                        try:
                            welcome_msg = f"@{user.username} {wm[0]}" if ucn else wm[0]
                            CLIENT.direct_send(welcome_msg, thread_ids=[gid])
                            STATS["total_welcomed"] += 1
                            STATS["today_welcomed"] += 1
                            log(f"👋 NEW: @{user.username}")
                            time.sleep(dly * 2)
                            break
                        except: break
                km[gid] = current_members

            except RateLimitError:
                log("⏳ Rate limit - waiting...")
                time.sleep(120)
            except Exception as e:
                log(f"⚠️ Error: {str(e)[:40]}")
                time.sleep(15)
        
        time.sleep(pol + random.uniform(2, 5))

    log(f"🛑 TaskID: {task_id} - Bot stopped")
    if task_id in RUNNING_BOTS:
        RUNNING_BOTS[task_id]["status"] = "stopped"

@app.route("/")
def index():
    return render_template_string(PAGE_HTML)

@app.route("/start", methods=["POST"])
def start():
    global BOT_THREAD, CURRENT_TASK_ID
    
    if BOT_THREAD and BOT_THREAD.is_alive():
        return jsonify({"message": "❌ Bot already running!", "task_id": CURRENT_TASK_ID})
    
    try:
        token = request.form.get("session", "").strip()
        welcome = [x.strip() for x in request.form.get("welcome", "").splitlines() if x.strip()]
        gids = [x.strip() for x in request.form.get("group_ids", "").split(",") if x.strip()]
        admins = [x.strip() for x in request.form.get("admin_ids", "").split(",") if x.strip()]
        
        if not all([token, welcome, gids]):
            return jsonify({"message": "❌ Fill all fields!"})

        task_id = generate_task_id()
        global STOP_EVENT
        STOP_EVENT.clear()
        
        BOT_THREAD = threading.Thread(
            target=run_bot,
            args=(task_id, token, welcome, gids,
                  int(request.form.get("delay", 5)),
                  int(request.form.get("poll", 25)),
                  request.form.get("use_custom_name") == "yes",
                  admins),
            daemon=True
        )
        BOT_THREAD.start()
        
        log(f"🚀 NEW TASK: {task_id}")
        return jsonify({"message": f"✅ Bot STARTED! TaskID: {task_id}", "task_id": task_id})
        
    except Exception as e:
        return jsonify({"message": f"❌ Error: {str(e)}"})

@app.route("/stop", methods=["POST"])
def stop():
    global STOP_EVENT, CLIENT, BOT_THREAD
    STOP_EVENT.set()
    CLIENT = None
    if BOT_THREAD: BOT_THREAD.join(timeout=5)
    log("🛑 Bot STOPPED!")
    return jsonify({"message": "✅ Bot stopped!"})

@app.route("/logs")
def logs():
    return jsonify({
        "logs": LOGS[-100:],
        "uptime": uptime(),
        "task_id": CURRENT_TASK_ID,
        "status": "running" if BOT_THREAD and BOT_THREAD.is_alive() else "stopped"
    })

@app.route("/clear_logs", methods=["POST"])
def clear_logs_route():
    clear_logs()
    return jsonify({"message": "✅ Logs cleared!"})

@app.route("/stats")
def stats():
    return jsonify({
        "uptime": uptime(),
        "task_id": CURRENT_TASK_ID,
        "status": "running" if BOT_THREAD and BOT_THREAD.is_alive() else "stopped",
        "total_welcomed": STATS["total_welcomed"],
        "today_welcomed": STATS["today_welcomed"]
    })

PAGE_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🚀 Instagram Bot v5.0</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#1e3a8a,#3b82f6);min-height:100vh;padding:20px;color:#333;}.container{max-width:1200px;margin:0 auto;background:white;border-radius:25px;box-shadow:0 30px 60px rgba(0,0,0,0.2);overflow:hidden;}.header{background:linear-gradient(135deg,#1e40af,#3b82f6);color:white;padding:40px;text-align:center;}.header h1{font-size:3rem;margin-bottom:10px;}.status-bar{padding:25px 35px;background:#f8fafc;border-bottom:3px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:20px;}.status-card{display:flex;align-items:center;gap:15px;padding:15px 25px;background:linear-gradient(135deg,#f0f9ff,#e0f2fe);border-radius:15px;border-left:5px solid #0ea5e9;font-weight:600;}.status-running .status-dot{background:#10b981;animation:pulse 2s infinite;}.status-stopped .status-dot{background:#ef4444;}.status-dot{width:16px;height:16px;border-radius:50%;}@keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.5;}}.content{padding:40px;}.dashboard-grid{display:grid;grid-template-columns:1fr 1fr;gap:30px;margin-bottom:40px;}@media(max-width:768px){.dashboard-grid{grid-template-columns:1fr;}}.form-section{background:#f8fafc;padding:35px;border-radius:20px;border:2px solid #e5e7eb;}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:25px;}@media(max-width:768px){.form-grid{grid-template-columns:1fr;}}.form-group{position:relative;}label{display:block;margin-bottom:12px;font-weight:600;color:#374151;font-size:1.1rem;}input,textarea{width:100%;padding:18px 20px;border:2px solid #e5e7eb;border-radius:15px;font-size:1rem;transition:all 0.3s;}input:focus,textarea:focus{outline:none;border-color:#1e40af;box-shadow:0 0 0 4px rgba(30,64,175,0.1);}textarea{resize:vertical;min-height:120px;}.controls{display:flex;gap:20px;justify-content:center;margin:50px 0;flex-wrap:wrap;}.btn{padding:20px 45px;border:none;border-radius:18px;font-size:1.2rem;font-weight:700;cursor:pointer;transition:all 0.3s;display:flex;align-items:center;gap:15px;box-shadow:0 12px 30px rgba(0,0,0,0.2);}.btn-start{background:linear-gradient(135deg,#10b981,#059669);color:white;}.btn-stop{background:linear-gradient(135deg,#ef4444,#dc2626);color:white;}.btn-clear{background:linear-gradient(135deg,#6b7280,#4b5563);color:white;}.btn:hover{transform:translateY(-5px);}.logs-container{background:linear-gradient(135deg,#1e293b,#334155);border-radius:25px;padding:40px;margin-top:40px;}#logs{background:#0f172a;color:#e2e8f0;border-radius:20px;padding:30px;height:400px;overflow-y:auto;font-family:'Courier New',monospace;font-size:1rem;line-height:1.7;white-space:pre-wrap;border:2px solid #475569;}.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:30px;margin-bottom:40px;}.stat-card{background:linear-gradient(135deg,#f8fafc,#e2e8f0);padding:40px;border-radius:20px;text-align:center;box-shadow:0 15px 35px rgba(0,0,0,0.1);border:1px solid #e5e7eb;}.stat-number{font-size:3.5rem;font-weight:800;background:linear-gradient(135deg,#1e40af,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:15px;}</style>
</head><body>
<div class="container">
<div class="header">
<h1><i class="fas fa-robot"></i> Instagram Bot v5.0</h1>
<p>✅ Welcome • Commands • 24/7 Ready</p>
</div>

<div class="status-bar status-stopped" id="statusBar">
<div class="status-card status-stopped"><div class="status-dot"></div><span>Status: Stopped</span></div>
<div class="status-card"><span id="uptime">00:00:00</span></div>
<div class="status-card"><strong id="taskIdDisplay">-</strong></div>
</div>

<div class="content">
<div class="stats-grid" id="statsGrid" style="display:none;">
<div class="stat-card"><div class="stat-number" id="totalWelcomed">0</div>Total Welcomed</div>
<div class="stat-card"><div class="stat-number" id="todayWelcomed">0</div>Today</div>
<div class="stat-card"><div class="stat-number" id="taskCount">0</div>Tasks</div>
</div>

<div class="dashboard-grid">
<div class="form-section">
<h3 style="color:#1e40af;margin-bottom:25px;font-size:1.5rem;"><i class="fas fa-play"></i> Start Bot</h3>
<form id="botForm">
<div class="form-grid">
<div class="form-group"><label><i class="fas fa-key"></i> Session Token *</label><input type="password" name="session" placeholder="Enter session token" required></div>
<div class="form-group"><label><i class="fas fa-hashtag"></i> Group IDs *</label><input type="text" name="group_ids" placeholder="123456789,987654321" required></div>
<div class="form-group"><label><i class="fas fa-users"></i> Admin Usernames</label><input type="text" name="admin_ids" placeholder="admin1,admin2"></div>
<div class="form-group"><label><i class="fas fa-clock"></i> Welcome Delay</label><input type="number" name="delay" value="5" min="2" max="15"></div>
<div class="form-group"><label><i class="fas fa-sync"></i> Poll (25s recommended)</label><input type="number" name="poll" value="25" min="15" max="60"></div>
<div class="form-group" style="grid-column:1/-1;"><label><i class="fas fa-comment"></i> Welcome Message *</label><textarea name="welcome">Welcome bro! 🔥
Have fun here! 🎉
Enjoy the group! 😊
Follow rules! 👮</textarea></div>
</div>
<div style="margin-top:20px;display:flex;gap:15px;align-items:center;justify-content:center;flex-wrap:wrap;">
<input type="checkbox" id="mention" name="use_custom_name" checked style="width:20px;height:20px;">
<label for="mention" style="font-weight:600;cursor:pointer;"><i class="fas fa-user-tag"></i> Mention @username</label>
</div>
</form>
</div>

<div class="form-section">
<h3 style="color:#dc2626;margin-bottom:25px;font-size:1.5rem;"><i class="fas fa-stop"></i> Controls</h3>
<div style="text-align:center;padding:30px;background:linear-gradient(135deg,#fef3c7,#fde68a);border-radius:20px;border:2px solid #f59e0b;">
<button type="button" class="btn btn-stop" onclick="stopBot()" style="width:100%;margin-bottom:15px;"><i class="fas fa-stop"></i> Emergency Stop</button>
<button type="button" class="btn btn-clear" onclick="clearLogs()" style="width:48%;"><i class="fas fa-trash"></i> Clear Logs</button>
<button type="button" class="btn btn-start" onclick="startBot()" style="width:48%;"><i class="fas fa-play"></i> Start Bot</button>
</div>
</div>
</div>

<div class="logs-container">
<div style="display:flex;justify-content:space-between;align-items:center;color:white;margin-bottom:25px;font-weight:700;font-size:1.1rem;">
<div><i class="fas fa-list"></i> Live Logs</div>
<button onclick="clearLogs()" style="background:#6b7280;color:white;border:none;padding:12px 24px;border-radius:10px;cursor:pointer;font-weight:600;">Clear</button>
</div>
<div id="logs">🚀 Bot Dashboard Ready! Paste session token & group IDs to start ✅</div>
</div>
</div>
</div>

<script>
async function startBot(){try{const formData=new FormData(document.getElementById('botForm'));const response=await fetch('/start',{method:'POST',body:formData});const result=await response.json();alert(result.message);updateStatus();}catch(e){alert('❌ Error: '+e.message);}}
async function stopBot(){try{const response=await fetch('/stop',{method:'POST'});const result=await response.json();alert(result.message);updateStatus();}catch(e){alert('❌ Error: '+e.message);}}
async function clearLogs(){try{await fetch('/clear_logs',{method:'POST'});document.getElementById('logs').textContent='🧹 Logs cleared!';}catch(e){}}
async function updateStatus(){try{const response=await fetch('/stats');const data=await response.json();document.getElementById('uptime').textContent=data.uptime;document.getElementById('taskIdDisplay').textContent=data.task_id||'-';const statusBar=document.getElementById('statusBar');const statusText=statusBar.querySelector('span');if(data.status==='running'){statusBar.className='status-bar status-running';statusText.textContent='Status: Running';document.getElementById('statsGrid').style.display='grid';}else{statusBar.className='status-bar status-stopped';statusText.textContent='Status: Stopped';document.getElementById('statsGrid').style.display='none';}document.getElementById('totalWelcomed').textContent=data.total_welcomed;document.getElementById('todayWelcomed').textContent=data.today_welcomed;document.getElementById('taskCount').textContent=1;}catch(e){}}setInterval(updateStatus,3000);updateStatus();
</script></body></html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
