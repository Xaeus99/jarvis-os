"""Tiny obs-websocket v5 client (OBS 28+ has it built in, port 4455).

Only what JARVIS needs: start/stop streaming and recording, the replay buffer, save replay.
"""
from __future__ import annotations
import base64, hashlib, json, logging, threading, uuid

log = logging.getLogger("jarvis.obs")

class OBSError(Exception):
    pass

def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()

class OBS:
    def __init__(self, host="127.0.0.1", port=4455, password="", timeout=4):
        self.url = f"ws://{host}:{int(port)}"
        self.password = password or ""
        self.timeout = timeout
        self.ws = None
        self.lock = threading.Lock()

    # -- connection --
    def connect(self):
        import websocket  # websocket-client
        ws = websocket.create_connection(self.url, timeout=self.timeout)
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            ws.close(); raise OBSError("not obs-websocket")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        a = hello["d"].get("authentication")
        if a:
            if not self.password:
                ws.close(); raise OBSError("OBS wants a websocket password; it's not in config.json")
            ident["authentication"] = auth_string(self.password, a["salt"], a["challenge"])
        ws.send(json.dumps({"op": 1, "d": ident}))
        try:
            msg = json.loads(ws.recv())
        except Exception:
            ws.close(); raise OBSError("OBS refused the websocket password")
        if msg.get("op") != 2:
            ws.close(); raise OBSError("OBS refused the websocket password")
        self.ws = ws
        return self

    def close(self):
        try:
            if self.ws: self.ws.close()
        finally:
            self.ws = None

    def request(self, rtype, data=None):
        with self.lock:
            if not self.ws:
                self.connect()
            rid = uuid.uuid4().hex[:12]
            self.ws.send(json.dumps({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data or {}}}))
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                    st = msg["d"].get("requestStatus", {})
                    if not st.get("result"):
                        raise OBSError(f"{rtype}: {st.get('code')} {st.get('comment') or ''}".strip())
                    return msg["d"].get("responseData") or {}

    def ok(self, rtype, data=None):
        """request() that returns (True, data) / (False, error text)."""
        try:
            return True, self.request(rtype, data)
        except Exception as e:
            return False, str(e)

    # -- the things JARVIS asks for --
    def stream_active(self):
        return bool(self.request("GetStreamStatus").get("outputActive"))

    def record_active(self):
        return bool(self.request("GetRecordStatus").get("outputActive"))

    def replay_active(self):
        ok, d = self.ok("GetReplayBufferStatus")
        return bool(ok and d.get("outputActive"))

    def ensure_replay_buffer(self, seconds=30):
        """Turn the replay buffer on (and try to set its length). True if it's running."""
        if self.replay_active():
            return True
        for cat in ("SimpleOutput", "AdvOut"):  # best effort: enable it and set its length in the profile
            self.ok("SetProfileParameter", {"parameterCategory": cat, "parameterName": "RecRB", "parameterValue": "true"})
            self.ok("SetProfileParameter", {"parameterCategory": cat, "parameterName": "RecRBTime", "parameterValue": str(int(seconds))})
        ok, err = self.ok("StartReplayBuffer")
        if not ok:
            log.info("replay buffer: %s", err)
        return self.replay_active()

    def save_replay(self):
        self.request("SaveReplayBuffer")
        ok, d = self.ok("GetLastReplayBufferReplay")
        return d.get("savedReplayPath") if ok else None
