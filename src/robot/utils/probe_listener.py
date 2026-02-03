import json, sys, time, datetime, os
import socketio
import threading

# ===== RF v2 =====
ROBOT_LISTENER_API_VERSION = 2

WS_URL = os.environ.get("PROBE_WS_URL", "http://54.252.181.103:8080")
SOCKET_PATH = "/robot-report-logs-realtime"
PROCESS_ID = os.environ.get("PROCESS_ID", "Process_F8fZ8GC")  # Process ID for room routing

def now():
    return datetime.datetime.utcnow().isoformat() + "Z"

_seq = 0
_sio = None
_lock = threading.Lock()
_continue_event = threading.Event()

# STEP_MODE will be read fresh in ProbeListener.__init__ to ensure correct value
# especially when robot is triggered via SSM Run Command
# "all" = continuous, "step" = wait for FE signal
_step_mode = "step"  # Default value, will be updated in __init__


def _connect_sio():
    global _sio, _step_mode
    try:
        _sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=5,
            reconnection_delay=1,
        )

        # Register event handler for "continueStep" from FE (via server)
        @_sio.on("continueStep")
        def on_continue_step(data=None):
            sys.stdout.write(f"[ProbeListener] Received continueStep signal: {data}\n")
            sys.stdout.flush()
            _continue_event.set()

        _sio.connect(
            WS_URL,
            socketio_path=SOCKET_PATH,
            transports=["websocket"],
        )
        
        # Join the process room so we can receive continueStep signals
        _sio.emit("joinProcess", {"processId": PROCESS_ID})
        sys.stderr.write(f"[ProbeListener] Joined room process:{PROCESS_ID}\n")

        sys.stderr.write(f"[ProbeListener] Socket.IO connected {WS_URL} | STEP_MODE={_step_mode} | PROCESS_ID={PROCESS_ID}\n")
    except Exception as e:
        _sio = None
        sys.stderr.write(f"[ProbeListener] Socket.IO connect failed: {e}\n")


def emit(evt_type, **payload):
    global _seq
    _seq += 1
    evt = {
        "seq": _seq,
        "ts": now(),
        "type": evt_type,
        "processId": PROCESS_ID,  # Include processId for room routing
        **payload
    }

    # stdout (debug / pipe)
    sys.stdout.write("EVT " + json.dumps(evt, ensure_ascii=False) + "\n")
    sys.stdout.flush()

    # socket.io emit
    if _sio:
        with _lock:
            try:
                _sio.emit("robotEvent", evt)
            except Exception as e:
                sys.stderr.write(f"[ProbeListener] SI emit failed: {e}\n")

# ===== stack đo duration =====
_tstack = []

# ===== stack lưu log messages của keyword hiện tại =====
_log_stack = []

def _suite_display_name(name, attrs):
    if name:
        return name
    src = attrs.get("source") if isinstance(attrs, dict) else None
    if src:
        return os.path.splitext(os.path.basename(src))[0]
    return ""

def _kw_display_name(name, attrs):
    if isinstance(attrs, dict):
        return attrs.get("kwname") or name
    return name

def _kw_lib(attrs):
    return attrs.get("libname") if isinstance(attrs, dict) else None

def _kw_args(attrs):
    if isinstance(attrs, dict):
        try:
            return list(attrs.get("args", []))
        except Exception:
            return []
    return []

def _status_success(status):
    return "SUCCESS" if status == "PASS" else "ERROR"


class ProbeListener:
    ROBOT_LISTENER_API_VERSION = 2

    def __init__(self):
        global _step_mode, _continue_event
        
        # Read STEP_MODE fresh from environment (set by setup.sh before running robot)
        _step_mode = os.environ.get("STEP_MODE", "step")
        sys.stdout.write(f"[ProbeListener] Initialized with STEP_MODE={_step_mode}\n")
        sys.stdout.flush()
        
        # In "all" mode, always allow continue (no waiting for FE signal)
        if _step_mode == "all":
            _continue_event.set()
        
        _connect_sio()

    # ===== Suite =====
    def start_suite(self, name, attrs):
        emit("RUN_START", suite={"name": _suite_display_name(name, attrs)})

    def end_suite(self, name, attrs):
        status = attrs.get("status") if isinstance(attrs, dict) else None
        emit("RUN_END",
             suite={"name": _suite_display_name(name, attrs)},
             status=_status_success(status))
        if _sio:
            try:
                time.sleep(0.5)
                _sio.disconnect()
            except Exception:
                pass

    # ===== Test =====
    def start_test(self, name, attrs):
        tags = list(attrs.get("tags", [])) if isinstance(attrs, dict) else []
        emit("TEST_START", test={"name": name, "tags": tags})

    def end_test(self, name, attrs):
        status = attrs.get("status") if isinstance(attrs, dict) else None
        message = attrs.get("message", "") if isinstance(attrs, dict) else ""
        emit("TEST_END",
             test={"name": name},
             status=_status_success(status),
             data={"message": message})

    # ===== Keyword =====
    def start_keyword(self, name, attrs):
        global _continue_event
        
        _tstack.append(time.time())
        # Khởi tạo list rỗng để lưu logs cho keyword này
        _log_stack.append([])
        
        kwname = _kw_display_name(name, attrs)
        args = _kw_args(attrs)
        lib = _kw_lib(attrs)

        # Prepare data
        event_data = {
            "lib": lib,
            "args": args
        }

        emit("STEP_START",
             step={"id": kwname, "name": kwname},
             data=event_data)
        
     

    def end_keyword(self, name, attrs):
        global _step_mode
        
        t0 = _tstack.pop() if _tstack else None
        # Lấy logs của keyword này từ stack
        step_logs = _log_stack.pop() if _log_stack else []
        print("STEP LOGS", step_logs)
        print("Attrs", attrs)
        duration_ms = int((time.time() - t0) * 1000) if t0 else None
        status = attrs.get("status") if isinstance(attrs, dict) else None
        message = step_logs[0].get('message') if step_logs else None
        kwname = _kw_display_name(name, attrs)
        args = _kw_args(attrs)
        
        emit("STEP_END",
             step={"id": kwname, "name": kwname},
             status=_status_success(status),
             data={
                 "durationMs": duration_ms,
                 "message": message,
                 "args": args,
                 "logs": step_logs  # Include all logs from this step
             })
        
        # ===== Stop process if step FAIL =====
        if status == "FAIL":
            sys.stdout.write(f"[ProbeListener] FAIL detected in step '{kwname}': {message}\n")
            sys.stdout.flush()
            # Disconnect socket
            if _sio:
                try:
                    time.sleep(0.3)
                    _sio.disconnect()
                except Exception:
                    pass
            # Use os._exit to force stop the process
            os._exit(1)
        
        # ===== STEP MODE: Wait for FE to send "continueStep" =====
        if _step_mode == "step":
            sys.stdout.write(f"[ProbeListener] STEP MODE: Waiting for 'continueStep' signal...\n")
            sys.stdout.flush()
            _continue_event.wait()  # Block here until FE sends signal
            _continue_event.clear()  # Reset for next step
    
    # ===== Log =====
    def log_message(self, message):
        lvl = message.get("level") if isinstance(message, dict) else None
        msg = message.get("message") if isinstance(message, dict) else str(message)
        
        # Lưu log message vào stack của keyword hiện tại
        log_entry = {"level": lvl, "message": msg}
        if _log_stack:
            _log_stack[-1].append(log_entry)
        
        # Emit STEP_LOG event
        emit("STEP_LOG", data=log_entry)
