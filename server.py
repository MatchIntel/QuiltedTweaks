"""QT affiliate site. SellAuth owns orders, commission balances and payout requests."""
import argparse
import contextlib
import hashlib
import hmac
import json
import io
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from decimal import Decimal
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
API_USER_AGENT = "QuiltedTweaks-Website/1.1 (+https://quiltedtweaks.com)"
TIERS = (("Bronze", 0, 20, 15),)
CODE = re.compile(r"^[A-Za-z0-9_-]{3,16}$")

class Problem(Exception):
    def __init__(self, message, status=400, retryable=True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable

def unwrap(value):
    return value["data"] if isinstance(value, dict) and isinstance(value.get("data"), dict) else value

def rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return value["data"]
    raise Problem("SellAuth returned an unexpected list format. Contact support.", 502)

def money(value):
    return Decimal(str(value or 0))

def qualifying(invoice):
    return invoice.get("status") == "completed" and money(invoice.get("paid_usd")) > 0 and money(invoice.get("affiliate_commission_usd")) > 0

class App:
    def __init__(self, env=None, transport=None):
        self.env = dict(os.environ if env is None else env)
        self.origin = self.env.get("PUBLIC_URL", "https://quiltedtweaks.com").rstrip("/")
        self.shop = self.env.get("SELLAUTH_SHOP_ID", "276718")
        self.key = self.env.get("SELLAUTH_API_KEY", "")
        self.secret = self.env.get("SESSION_SECRET", "")
        self.resend = self.env.get("RESEND_API_KEY", "")
        self.sender = self.env.get("EMAIL_FROM", "")
        self.transport = transport or self.http_json
        self.data = Path(self.env.get("DATA_DIR", str(ROOT / "data")))
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = self.data / "affiliates.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS otp(email TEXT PRIMARY KEY, digest TEXT, expires REAL, attempts INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY, customer INTEGER, email TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS limits(k TEXT PRIMARY KEY, n INTEGER, expires REAL);
            CREATE TABLE IF NOT EXISTS clicks(code TEXT, visitor TEXT, day TEXT, PRIMARY KEY(code,visitor,day));
            """)
        self.lock = threading.Lock()
        self.snapshot = None
        self.last_sync = 0
        self.sync_error = None
        self.access_block = None

    def connect(self):
        return sqlite3.connect(self.db, timeout=15)

    @property
    def ready(self):
        return bool(self.key and len(self.secret) >= 32 and self.resend and self.sender)

    def digest(self, text):
        return hmac.new(self.secret.encode(), text.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def http_json(url, method, body, headers):
        raw = None if body is None else json.dumps(body).encode()
        req = Request(url, data=raw, method=method, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": API_USER_AGENT, **headers})
        try:
            with urlopen(req, timeout=25) as response:
                return json.load(response)
        except HTTPError as error:
            service = "SellAuth" if urlsplit(url).hostname == "api.sellauth.com" else "Email service"
            retryable = True
            try:
                payload = json.loads(error.read(65536))
                if not isinstance(payload, dict):
                    payload = {}
                if payload.get("cloudflare_error") or payload.get("error_code") == 1010:
                    code = str(payload.get("error_code", "unknown"))
                    code = code if re.fullmatch(r"[0-9]{3,6}", code) else "unknown"
                    ray = str(payload.get("ray_id", ""))
                    ray = ray if re.fullmatch(r"[A-Za-z0-9-]{1,80}", ray) else "unavailable"
                    message = f"{service} request blocked by Cloudflare (HTTP {error.code}, error {code}, reference {ray}). Contact {service} support with this reference."
                    retryable = payload.get("retryable") is not False and code != "1010"
                else:
                    detail = payload.get("message") or payload.get("detail") or payload.get("error") or payload.get("title")
                    message = f"{service} API returned HTTP {error.code}: " + (detail if isinstance(detail, str) else "Request rejected; check API access and shop permissions.")
            except (ValueError, AttributeError):
                message = f"{service} returned HTTP {error.code} with a non-JSON error response."
            token = headers.get("Authorization", "").removeprefix("Bearer ")
            if token:
                message = message.replace(token, "[redacted]")
            message = " ".join(message.split())[:500]
            raise Problem(message, error.code if 400 <= error.code < 500 else 502, retryable=retryable) from None
        except (OSError, ValueError):
            raise Problem("The connected service is temporarily unavailable. Please retry.", 502)

    def merchant(self, path, method="GET", body=None):
        if self.access_block:
            raise self.access_block
        try:
            return self.transport("https://api.sellauth.com/v1/shops/" + self.shop + path, method, body, {"Authorization": "Bearer " + self.key})
        except Problem as error:
            if not error.retryable:
                self.access_block = error
            raise

    def customer(self, cid):
        result = unwrap(self.merchant("/customers/" + str(cid)))
        if not isinstance(result, dict) or int(result.get("id", 0)) != int(cid):
            raise Problem("SellAuth customer verification failed.", 502)
        return result

    def limit(self, key, maximum, seconds):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT n,expires FROM limits WHERE k=?", (key,)).fetchone()
            n, expires = row if row and row[1] > now else (0, now + seconds)
            if n >= maximum:
                raise Problem("Too many requests. Please wait and try again.", 429)
            db.execute("INSERT OR REPLACE INTO limits VALUES(?,?,?)", (key, n + 1, expires))
            db.execute("DELETE FROM limits WHERE expires<?", (now - 3600,))

    def send_code(self, email, ip):
        if not self.ready:
            raise Problem("Affiliate sign-in is not connected yet. Please contact Quilted Tweaks.", 503)
        email = str(email).strip().lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise Problem("Enter a valid email address.")
        self.limit("login-ip:" + self.digest(ip), 15, 3600)
        self.limit("login-email:" + self.digest(email), 1, 60)
        self.limit("login-global", 200, 3600)
        code = str(secrets.randbelow(1000000)).zfill(6)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO otp VALUES(?,?,?,0)", (email, self.digest(email + ":" + code), time.time() + 600))
        try:
            self.transport("https://api.resend.com/emails", "POST", {
                "from": self.sender, "to": [email], "subject": "Your Quilted Tweaks sign-in code",
                "text": "Your QT affiliate sign-in code is " + code + ". It expires in 10 minutes. If you did not request this, ignore this email."
            }, {"Authorization": "Bearer " + self.resend})
        except Exception:
            with self.connect() as db:
                db.execute("DELETE FROM otp WHERE email=?", (email,))
            raise
        return {"message": "Check your email for a six-digit code."}

    def verify_code(self, email, code, ip):
        email = str(email).strip().lower()
        self.limit("verify:" + self.digest(ip), 30, 600)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest,expires,attempts FROM otp WHERE email=?", (email,)).fetchone()
            valid = bool(row and row[1] > time.time() and row[2] < 5 and hmac.compare_digest(row[0], self.digest(email + ":" + str(code))))
            if row:
                db.execute("UPDATE otp SET attempts=attempts+1 WHERE email=?", (email,))
            if valid:
                db.execute("DELETE FROM otp WHERE email=?", (email,))
        if not valid:
            raise Problem("That code is invalid or expired. Request a new code.", 401)
        result = unwrap(self.merchant("/customers", "POST", {"email": email}))
        customer = result.get("customer", result) if isinstance(result, dict) else {}
        if not customer.get("id"):
            raise Problem("SellAuth did not return a customer ID. Contact support.", 502)
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (self.digest(token), int(customer["id"]), email, time.time() + 604800))
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        return token

    def session(self, token):
        if not token:
            raise Problem("Sign in to your affiliate account.", 401)
        with self.connect() as db:
            row = db.execute("SELECT customer,email,expires FROM sessions WHERE digest=?", (self.digest(token),)).fetchone()
        if not row or row[2] <= time.time():
            raise Problem("Your session expired. Please sign in again.", 401)
        return {"id": row[0], "email": row[1]}

    def tiers(self):
        native = rows(self.merchant("/affiliate-tiers"))
        result = {}
        for name, threshold, percent, discount in TIERS:
            entry = next((t for t in native if str(t.get("name", "")).lower() == name.lower()), None)
            if not entry or money(entry.get("percentage")) != percent or money(entry.get("discount_percentage")) != discount:
                raise Problem("The SellAuth Bronze tier must have 20% commission and 15% buyer discount. Contact Quilted Tweaks.", 503)
            result[name] = entry
        return result

    def setup_tiers(self):
        existing = rows(self.merchant("/affiliate-tiers"))
        for index, (name, minimum, rate, discount) in enumerate(TIERS):
            old = next((t for t in existing if str(t.get("name", "")).lower() == name.lower()), None)
            # Reuse the existing default instead of exceeding the one-tier plan limit.
            if old is None and len(existing) == 1 and str(existing[0].get("name", "")).lower() == "default":
                old = existing[0]
            if old is None and existing:
                raise Problem("Rename your existing default tier to Bronze in SellAuth before running setup.", 503)
            body = {"name": name, "percentage": rate, "discount_percentage": discount, "is_public": True, "sort_order": index, "attribution_mode": "per_order", "attribution_window_days": 30}
            self.merchant("/affiliate-tiers" + ("/" + str(old["id"]) if old else ""), "PUT" if old else "POST", body)

    def sync(self, force=False):
        if not self.key:
            return
        if not force and time.time() - self.last_sync < 25:
            return
        if not self.lock.acquire(blocking=False):
            return
        try:
            self.tiers()  # Validate Bronze only; no sales-based promotions are enabled.
            invoice_map = {}
            for archived in (False, True):
                page = 1
                while True:
                    query = {"perPage": 100, "page": page, "all": "true"}
                    if archived:
                        query["archived"] = "true"
                    result = self.merchant("/invoices?" + urlencode(query))
                    for invoice in rows(result):
                        invoice_map[int(invoice["id"])] = invoice
                    last = int(result.get("last_page", result.get("meta", {}).get("last_page", 0)))
                    if not last:
                        raise Problem("SellAuth invoice pagination is missing; tier synchronization stopped safely.", 502)
                    if page >= last:
                        break
                    if page >= 1000:
                        raise Problem("Invoice history needs a larger synchronization window.", 503)
                    page += 1
            invoices = list(invoice_map.values())
            self.snapshot = invoices
            self.last_sync = time.time()
            self.sync_error = None
        except Exception as error:
            self.sync_error = str(error)
            raise
        finally:
            self.lock.release()

    def dashboard(self, session):
        self.sync()
        if self.snapshot is None:
            raise Problem("Sales are still synchronizing. Please retry shortly.", 503)
        c = self.customer(session["id"])
        tiers = self.tiers()
        code = c.get("affiliate_code")
        suspended = c.get("affiliate_status") == "suspended"
        if suspended:
            raise Problem("Your affiliate access is suspended. Contact support.", 403)
        tier = next((t for t in tiers.values() if str(t["id"]) == str(c.get("affiliate_tier_id"))), None)
        orders = [x for x in self.snapshot if str(x.get("affiliate_customer_id")) == str(session["id"]) and qualifying(x)]
        clean_sales = sorted([{"id": x["id"], "date": x.get("completed_at") or x.get("created_at"), "total": str(x.get("price_usd") or 0), "commission": str(x["affiliate_commission_usd"]), "status": x["status"]} for x in orders], key=lambda x: (x["date"] or "", x["id"]), reverse=True)
        # No buyer email, address, IP, delivered key or other invoice secrets leave this service.
        payouts = []
        for page in range(1, 1001):
            result = self.merchant("/affiliate-payouts?" + urlencode({"perPage": 100, "page": page}))
            batch = rows(result)
            payouts.extend({"id": x["id"], "date": x.get("created_at"), "amount": str(x.get("amount_usd", x.get("amount", 0))), "status": x.get("status")} for x in batch if str(x.get("customer_id", x.get("shop_customer_id"))) == str(session["id"]))
            last = int(result.get("last_page", result.get("meta", {}).get("last_page", 1))) if isinstance(result, dict) else 1
            if page >= last:
                break
        with self.connect() as db:
            clicks = db.execute("SELECT count(*) FROM clicks WHERE code=?", (str(code or "").lower(),)).fetchone()[0]
        return {"email": session["email"], "code": code, "tier": tier.get("name") if tier else None, "commission": float(tier["percentage"]) if tier else None, "discount": float(tier["discount_percentage"]) if tier else None, "balance": str(c.get("affiliate_balance") or 0), "earned": str(sum((money(x["commission"]) for x in clean_sales), Decimal(0))), "clicks": clicks, "salesCount": len(orders), "sales": clean_sales, "payouts": payouts, "syncedAt": self.last_sync, "syncWarning": self.sync_error}

    def customer_action(self, session, path, body):
        self.limit("customer-action:" + str(session["id"]), 4, 60)
        token_result = unwrap(self.merchant("/customers/" + str(session["id"]) + "/token", "POST", {"expires_in": 60}))
        token = token_result.get("token") or token_result.get("access_token")
        if not isinstance(token, str) or not token:
            raise Problem("SellAuth customer token could not be created.", 502)
        headers = {"Authorization": "Bearer " + token}
        try:
            return self.transport("https://api.sellauth.com/v1/customer-dashboard/affiliate/" + path, "POST", body, headers)
        finally:
            with contextlib.suppress(Exception):
                self.transport("https://api.sellauth.com/v1/customer-dashboard/logout", "POST", {}, headers)

    def set_code(self, session, code):
        code = str(code).strip()
        if not CODE.fullmatch(code):
            raise Problem("Use 3–16 letters, numbers, underscores or hyphens.")
        tiers = self.tiers()
        before = self.customer(session["id"])
        self.customer_action(session, "edit-code", {"affiliate_code": code})
        if not before.get("affiliate_code"):
            bronze = tiers["Bronze"]
            self.merchant("/affiliates/" + str(session["id"]) + "/tier", "PUT", {"tier_id": bronze["id"]})
        return {"message": "Your referral code is ready. The same code carries your SellAuth buyer discount."}

    def track(self, code, visitor, ip):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", str(code)) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(visitor)):
            raise Problem("Invalid referral.")
        self.limit("click:" + self.digest(ip), 100, 3600)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO clicks VALUES(?,?,?)", (code.lower(), self.digest(visitor), time.strftime("%Y-%m-%d", time.gmtime())))
        return {"ok": True}

def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never write emails, cookies, OTPs or tokens to access logs.

        def respond(self, status, value, cookie=None):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def session(self):
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            return app.session(cookies["qt_session"].value if "qt_session" in cookies else None)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                raw = (ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            try:
                if path == "/health":
                    return self.respond(200, {"ok": True})
                if path == "/api/affiliates/status":
                    return self.respond(200, {"configured": app.ready})
                if path == "/api/affiliates/dashboard":
                    session = self.session()
                    app.limit("dashboard:" + str(session["id"]), 12, 60)
                    return self.respond(200, app.dashboard(session))
                self.respond(404, {"message": "Not found."})
            except Problem as e:
                self.respond(e.status, {"message": str(e)})
            except Exception:
                self.respond(502, {"message": "Affiliate data could not be synchronized. Please retry or contact support."})

        def do_POST(self):
            try:
                if self.headers.get("Origin") != app.origin:
                    raise Problem("Request origin is not allowed.", 403)
                if "application/json" not in self.headers.get("Content-Type", ""):
                    raise Problem("JSON is required.", 415)
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 8192:
                    raise Problem("Request is too large.", 413)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Problem("Invalid request.")
                path = urlsplit(self.path).path
                # Railway owns the public edge; forwarding headers are used only for abuse limits.
                ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
                if path == "/api/affiliates/login":
                    return self.respond(200, app.send_code(body.get("email"), ip))
                if path == "/api/affiliates/verify":
                    token = app.verify_code(body.get("email"), body.get("code"), ip)
                    secure = "; Secure" if app.origin.startswith("https://") else ""
                    return self.respond(200, {"message": "Signed in."}, "qt_session=" + token + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800" + secure)
                if path == "/api/affiliates/track":
                    return self.respond(200, app.track(body.get("code"), body.get("visitor"), ip))
                session = self.session()
                if path == "/api/affiliates/logout":
                    cookies = SimpleCookie(self.headers.get("Cookie", ""))
                    with app.connect() as db:
                        db.execute("DELETE FROM sessions WHERE digest=?", (app.digest(cookies["qt_session"].value),))
                    return self.respond(200, {"message": "Signed out."}, "qt_session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0")
                if path == "/api/affiliates/code":
                    return self.respond(200, app.set_code(session, body.get("code")))
                if path == "/api/affiliates/payout":
                    try:
                        amount = money(body.get("amount"))
                    except Exception:
                        raise Problem("Enter a valid payout amount.")
                    details = str(body.get("details", "")).strip()
                    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal(".01")) or not 3 <= len(details) <= 2000:
                        raise Problem("Enter a positive amount with at most two decimals and valid payout details.")
                    return self.respond(200, app.customer_action(session, "payout-request", {"amount": float(amount), "payout_details": details}))
                if path == "/api/affiliates/cancel-payout":
                    request_id = body.get("id")
                    if not isinstance(request_id, int) or request_id < 1:
                        raise Problem("Invalid payout request.")
                    return self.respond(200, app.customer_action(session, "payout-request/" + str(request_id) + "/cancel", {}))
                self.respond(404, {"message": "Not found."})
            except Problem as e:
                self.respond(e.status, {"message": str(e)})
            except (ValueError, TypeError):
                self.respond(400, {"message": "Invalid request."})
            except Exception:
                self.respond(502, {"message": "This action could not be completed. Please retry or contact support."})
    return Handler

def wsgi_for(app):
    """Use the same tested request controller under a production WSGI server."""
    class Controller(handler_for(app)):
        def __init__(self, environ):
            self.path = environ.get("PATH_INFO", "/")
            if environ.get("QUERY_STRING"):
                self.path += "?" + environ["QUERY_STRING"]
            self.headers = Message()
            for key, value in environ.items():
                if key.startswith("HTTP_"):
                    self.headers[key[5:].replace("_", "-")] = value
            for key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                if environ.get(key):
                    self.headers[key.replace("_", "-")] = environ[key]
            self.client_address = (environ.get("REMOTE_ADDR", "unknown"), 0)
            self.rfile = environ["wsgi.input"]
            self.wfile = io.BytesIO()
            self.status = 200
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status = code

        def send_header(self, name, value):
            self.response_headers.append((name, str(value)))

        def end_headers(self):
            pass

    def application(environ, start_response):
        controller = Controller(environ)
        method = environ.get("REQUEST_METHOD", "GET")
        if method == "GET":
            controller.do_GET()
        elif method == "POST":
            controller.do_POST()
        else:
            controller.respond(405, {"message": "Method not allowed."})
        start_response(str(controller.status) + " " + HTTPStatus(controller.status).phrase, controller.response_headers)
        return [controller.wfile.getvalue()]
    return application

def validate_production(app):
    if os.environ.get("NODE_ENV") == "production" or os.environ.get("RAILWAY_ENVIRONMENT"):
        if not app.ready or not os.environ.get("DATA_DIR"):
            raise RuntimeError("Set SELLAUTH_API_KEY, SESSION_SECRET (32+ chars), RESEND_API_KEY, EMAIL_FROM, and DATA_DIR on a persistent Railway volume.")

def start_sync_worker(app):
    def worker():
        while True:
            try:
                app.sync()
            except Problem as error:
                print("SellAuth sync failed: " + str(error), flush=True)
                if not error.retryable:
                    print("SellAuth automatic sync paused. Resolve access with SellAuth support, then restart this service.", flush=True)
                    return
            except Exception as error:
                print("SellAuth sync failed with " + type(error).__name__ + ". Run python server.py --check for diagnostics.", flush=True)
            time.sleep(30)
    threading.Thread(target=worker, daemon=True).start()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup-tiers", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    app = App()
    if args.setup_tiers:
        if not app.key:
            raise SystemExit("Set SELLAUTH_API_KEY before configuring tiers.")
        app.setup_tiers()
        print("Bronze configured in SellAuth: 20% commission, 15% buyer discount. No additional tiers created.")
        return
    if args.check:
        if not app.ready:
            raise SystemExit("Set all required configuration variables before checking.")
        try:
            app.tiers()
            rows(app.merchant("/invoices?perPage=1&all=true"))
            rows(app.merchant("/affiliate-payouts?perPage=1"))
        except Problem as error:
            raise SystemExit("SellAuth check failed: " + str(error)) from None
        print("SellAuth tier, invoice and payout-list access checked. Email delivery and a live purchase still need testing.")
        return
    validate_production(app)
    start_sync_worker(app)
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), handler_for(app))
    server.daemon_threads = True
    print("QT affiliate website listening.", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
