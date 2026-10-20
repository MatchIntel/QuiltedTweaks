import copy
import contextlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import time
import io
import unittest
from unittest.mock import patch
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs

spec = importlib.util.spec_from_file_location("qt_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class Native:
    def __init__(self):
        self.calls = []
        self.sent = []
        self.c = {"id": 7, "email": "partner@example.com", "affiliate_code": "PARTNER", "affiliate_balance": "12.50", "affiliate_status": "active", "affiliate_tier_id": 1}
        self.tiers = [{"id": i+1, "name": t[0], "percentage": t[2], "discount_percentage": t[3]} for i,t in enumerate(server.TIERS)]
        self.invoices = []
        self.payouts = []

    def __call__(self, url, method, body, headers):
        self.calls.append((url, method, copy.deepcopy(body)))
        path = urlsplit(url).path
        if "api.resend.com" in url:
            self.sent.append(body)
            return {"id": "mail-fixture"}
        if "/affiliate-tiers/" in path and method == "PUT":
            item = next(t for t in self.tiers if str(t["id"]) == path.rsplit("/",1)[1])
            item.update(body)
            return {"data": item}
        if path.endswith("/affiliate-tiers") and method == "POST":
            if self.tiers:
                raise AssertionError("One-tier plan: cannot create another tier")
            self.tiers.append({"id": 1, **body})
            return {"data": self.tiers[0]}
        if path.endswith("/affiliate-tiers"):
            return {"data": self.tiers}
        if path.endswith("/invoices"):
            query = parse_qs(urlsplit(url).query)
            if query.get("all") != ["1"]:
                raise server.Problem("The all field must be true or false.", 422)
            if "archived" in query and query["archived"] != ["1"]:
                raise server.Problem("Invalid archived filter.", 422)
            return {"data": self.invoices, "last_page": 1}
        if path.endswith("/affiliate-payouts"):
            return {"data": self.payouts, "last_page": 1}
        if path.endswith("/customers/7") or (path.endswith("/customers") and method == "POST"):
            return copy.deepcopy(self.c)
        if path.endswith("/customers/88"):
            return {**self.c, "id":88, "email":"other@example.com"}
        if path.endswith("/affiliates/7/tier"):
            self.c["affiliate_tier_id"] = body["tier_id"]
            return {"success": True}
        if path.endswith("/customers/7/token"):
            return {"token": "PRIVATE_CUSTOMER_TOKEN"}
        if path.endswith("/affiliate/edit-code"):
            self.c["affiliate_code"] = body["affiliate_code"]
            return {"success": True}
        if path.endswith("/affiliate/payout-request"):
            self.payouts.append({"id": 81, "customer_id": 7, "amount_usd": body["amount"], "status": "pending"})
            return {"success": True, "message": "Payout requested."}
        if path.endswith("/logout"):
            return {"success": True}
        raise AssertionError("Unexpected upstream call: " + path)

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.native = Native()
        self.env = {"DATA_DIR": self.temp.name, "SESSION_SECRET": "x"*64, "SELLAUTH_API_KEY": "PRIVATE_SELLER_KEY", "RESEND_API_KEY": "PRIVATE_EMAIL_KEY", "EMAIL_FROM": "QT <login@example.com>"}
        self.app = server.App(self.env, self.native)
        self.session = {"id": 7, "email": "partner@example.com"}

    def tearDown(self):
        self.temp.cleanup()

    def invoice(self, number, status="completed", paid="15", commission="3", cid=7):
        return {"id": number, "status": status, "paid_usd": paid, "price_usd": "15", "affiliate_commission_usd": commission, "affiliate_customer_id": cid, "completed_at": "2026-10-20", "email": "PRIVATE_BUYER_EMAIL", "delivered": ["PRIVATE_LICENSE_KEY"], "billing_address": "PRIVATE_BUYER_ADDRESS"}

    def login(self):
        self.app.send_code("partner@example.com", "test-ip")
        code = re.search(r"\b[0-9]{6}\b", self.native.sent[-1]["text"])[0]
        return self.app.verify_code("partner@example.com", code, "test-ip")

    def test_single_bronze_tier_works_with_many_sales_and_never_promotes(self):
        self.native.invoices = [self.invoice(i) for i in range(1,26)]
        self.app.sync(force=True)
        data = self.app.dashboard(self.session)
        self.assertEqual(data["tier"], "Bronze")
        self.assertEqual(data["commission"], 20)
        self.assertEqual(data["discount"], 15)
        self.assertEqual(data["salesCount"], 25)
        self.assertFalse(any(method != "GET" for _,method,_ in self.native.calls))

    def test_invoice_query_flags_are_accepted_for_current_and_archived_orders(self):
        self.app.invoice_page(page=2, per_page=50)
        self.app.invoice_page(page=3, archived=True)
        first, second = [parse_qs(urlsplit(url).query) for url,_,_ in self.native.calls]
        self.assertEqual(first, {"page":["2"], "perPage":["50"], "all":["1"]})
        self.assertEqual(second, {"page":["3"], "perPage":["100"], "all":["1"], "archived":["1"]})

    def test_api_check_uses_same_validated_queries_as_sync(self):
        with patch.object(server, "App", return_value=self.app), patch("sys.argv", ["server.py", "--check"]), contextlib.redirect_stdout(io.StringIO()):
            server.main()
        calls = [parse_qs(urlsplit(url).query) for url,_,_ in self.native.calls if urlsplit(url).path.endswith("/invoices")]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(q["all"] == ["1"] and q["perPage"] == ["1"] for q in calls))
        self.assertNotIn("archived", calls[0])
        self.assertEqual(calls[1]["archived"], ["1"])

    def test_pagination_covers_both_buckets_and_deduplicates_invoice_ids(self):
        seen = []
        def transport(url, method, body, headers):
            if not urlsplit(url).path.endswith("/invoices"):
                return self.native(url, method, body, headers)
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["all"], ["1"])
            archived = query.get("archived") == ["1"]
            page = int(query["page"][0])
            seen.append((archived, page))
            ids = {(False,1):1, (False,2):2, (True,1):2, (True,2):3}
            return {"data":[self.invoice(ids[(archived,page)])], "last_page":2}
        self.app.transport = transport
        self.app.sync(force=True)
        self.assertEqual(seen, [(False,1),(False,2),(True,1),(True,2)])
        self.assertEqual(sorted(x["id"] for x in self.app.snapshot), [1,2,3])

    def test_cloudflare_block_reports_reference_and_stops_further_requests(self):
        payload = {"cloudflare_error": True, "error_code": 1010, "ray_id": "test-reference", "retryable": False}
        failure = HTTPError("https://api.sellauth.com/v1/shops/276718/affiliate-tiers", 403, "Forbidden", {}, io.BytesIO(json.dumps(payload).encode()))
        app = server.App(self.env)
        with patch.object(server, "urlopen", side_effect=failure) as request:
            for _ in range(2):
                with self.assertRaises(server.Problem) as error:
                    app.sync(force=True)
                self.assertIn("Cloudflare", str(error.exception))
                self.assertIn("1010", str(error.exception))
                self.assertIn("test-reference", str(error.exception))
                self.assertFalse(error.exception.retryable)
                self.assertNotIn(self.env["SELLAUTH_API_KEY"], str(error.exception))
            self.assertEqual(request.call_count, 1)

    def test_merchant_request_identifies_qt_and_preserves_api_auth(self):
        app = server.App(self.env)
        response = io.BytesIO(b'{"data": []}')
        with patch.object(server, "urlopen", return_value=response) as request:
            self.assertEqual(app.merchant("/affiliate-tiers"), {"data": []})
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.sellauth.com/v1/shops/276718/affiliate-tiers")
        self.assertEqual(req.get_header("User-agent"), server.API_USER_AGENT)
        self.assertEqual(req.get_header("Authorization"), "Bearer " + self.env["SELLAUTH_API_KEY"])
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertEqual(req.get_method(), "GET")

    def test_api_detail_is_reported_without_exposing_credentials(self):
        key = self.env["SELLAUTH_API_KEY"]
        payload = {"detail": "Permission denied " + key}
        failure = HTTPError("https://api.sellauth.com/v1/shops/276718/affiliate-tiers", 403, "Forbidden", {}, io.BytesIO(json.dumps(payload).encode()))
        with patch.object(server, "urlopen", side_effect=failure):
            with self.assertRaises(server.Problem) as error:
                server.App.http_json(failure.url, "GET", None, {"Authorization": "Bearer " + key})
            self.assertIn("HTTP 403", str(error.exception))
            self.assertIn("Permission denied", str(error.exception))
            self.assertNotIn(key, str(error.exception))

    def test_non_json_upstream_error_is_identified(self):
        failure = HTTPError("https://api.sellauth.com/v1/shops", 403, "Forbidden", {}, io.BytesIO(b"<html>Denied</html>"))
        with patch.object(server, "urlopen", side_effect=failure):
            with self.assertRaises(server.Problem) as error:
                server.App.http_json(failure.url, "GET", None, {})
            self.assertIn("non-JSON", str(error.exception))

    def test_setup_reuses_default_tier_and_is_idempotent_on_one_tier_plan(self):
        self.native.tiers[0]["name"] = "Default"
        self.app.setup_tiers()
        self.app.setup_tiers()
        self.assertEqual(len(self.native.tiers), 1)
        self.assertEqual(self.native.tiers[0]["name"], "Bronze")
        self.assertEqual(self.native.tiers[0]["percentage"], 20)
        self.assertEqual(self.native.tiers[0]["discount_percentage"], 15)
        self.assertFalse(any(method == "POST" for _,method,_ in self.native.calls))

    def test_missing_bronze_fails_without_creating_tiers_during_sync(self):
        self.native.tiers[0]["name"] = "Default"
        with self.assertRaises(server.Problem):
            self.app.sync(force=True)
        self.assertIsNone(self.app.snapshot)
        self.assertFalse(any(method != "GET" for _,method,_ in self.native.calls))

    def test_setup_does_not_add_a_tier_beside_an_unknown_existing_tier(self):
        self.native.tiers[0]["name"] = "Custom"
        with self.assertRaises(server.Problem):
            self.app.setup_tiers()
        self.assertEqual(len(self.native.tiers), 1)

    def test_new_enrollment_uses_bronze_only(self):
        self.native.c["affiliate_code"] = None
        self.native.c["affiliate_tier_id"] = None
        self.app.set_code(self.session, "NEWCODE")
        self.assertEqual(self.native.c["affiliate_code"], "NEWCODE")
        self.assertEqual(self.native.c["affiliate_tier_id"], 1)
        assignments = [body for url,method,body in self.native.calls if url.endswith("/tier")]
        self.assertEqual(assignments, [{"tier_id": 1}])

    def test_unpaid_free_refunded_chargebacks_and_unattributed_do_not_qualify(self):
        self.native.invoices = [self.invoice(1), self.invoice(2,"pending"), self.invoice(3,"refunded"), self.invoice(4,"charged_back"), self.invoice(5,paid="0"), self.invoice(6,commission="0"), self.invoice(7,cid=None)]
        self.app.sync(force=True)
        data = self.app.dashboard(self.session)
        self.assertEqual(data["salesCount"],1)
        self.assertEqual(data["earned"],"3")
        self.assertEqual(data["balance"],"12.50")
        self.assertEqual(self.native.c["affiliate_tier_id"],1)

    def test_no_cross_customer_or_invoice_private_data_is_exposed(self):
        self.native.invoices = [self.invoice(1),self.invoice(2,cid=88)]
        self.native.payouts = [{"id":1,"customer_id":88,"amount":100,"status":"paid","payout_details":"OTHER_PRIVATE_DETAILS"},{"id":2,"customer_id":7,"amount":5,"status":"pending","payout_details":"OWN_PRIVATE_DETAILS"}]
        self.app.sync(force=True)
        data=self.app.dashboard(self.session)
        raw=json.dumps(data)
        self.assertNotIn("PRIVATE_",raw)
        self.assertEqual([x["id"] for x in data["sales"]],[1])
        self.assertEqual([x["id"] for x in data["payouts"]],[2])
        self.assertNotIn("PRIVATE_SELLER_KEY",raw)

    def test_login_requires_email_proof_and_codes_are_single_use(self):
        with self.assertRaises(server.Problem):
            self.app.verify_code("partner@example.com","000000","test-ip")
        token=self.login()
        self.assertEqual(self.app.session(token)["id"],7)
        code=re.search(r"\b[0-9]{6}\b",self.native.sent[-1]["text"])[0]
        with self.assertRaises(server.Problem):
            self.app.verify_code("partner@example.com",code,"test-ip")
        with self.assertRaises(server.Problem):
            self.app.session(token+"tampered")
        self.assertEqual(server.App(self.env,self.native).session(token)["id"],7)

    def test_five_bad_codes_lock_an_otp(self):
        self.app.send_code("partner@example.com","test-ip")
        code=re.search(r"\b[0-9]{6}\b",self.native.sent[-1]["text"])[0]
        for _ in range(5):
            with self.assertRaises(server.Problem):
                self.app.verify_code("partner@example.com","abcdef","test-ip")
        with self.assertRaises(server.Problem):
            self.app.verify_code("partner@example.com",code,"test-ip")

    def test_email_cooldown(self):
        self.app.send_code("partner@example.com","test-ip")
        with self.assertRaises(server.Problem) as error:
            self.app.send_code("partner@example.com","test-ip")
        self.assertEqual(error.exception.status,429)

    def test_clicks_deduplicate_by_code_visitor_day_and_persist(self):
        for _ in range(2):
            self.app.track("PARTNER","a"*32,"ip")
        self.app.track("partner","b"*32,"ip")
        self.app.sync(force=True)
        self.assertEqual(self.app.dashboard(self.session)["clicks"],2)
        restored=server.App(self.env,self.native)
        restored.sync(force=True)
        self.assertEqual(restored.dashboard(self.session)["clicks"],2)

    def test_customer_payout_uses_native_wallet_and_revokes_token(self):
        result=self.app.customer_action(self.session,"payout-request",{"amount":5,"payout_details":"test"})
        self.assertTrue(result["success"])
        self.assertTrue(self.native.calls[-1][0].endswith("/logout"))
        self.assertEqual(self.native.payouts[0]["customer_id"],7)

    def test_native_rate_mismatch_fails_closed(self):
        self.native.tiers[0]["percentage"]=99
        with self.assertRaises(server.Problem):
            self.app.sync(force=True)
        self.assertIsNone(self.app.snapshot)

    def test_seller_assigned_external_tiers_are_not_overwritten(self):
        self.native.c["affiliate_tier_id"]=444
        self.app.sync(force=True)
        self.assertEqual(self.native.c["affiliate_tier_id"],444)

    def test_invalid_code_is_rejected_before_upstream(self):
        before=len(self.native.calls)
        with self.assertRaises(server.Problem):
            self.app.set_code(self.session,'<script>alert(1)</script>')
        self.assertEqual(len(self.native.calls),before)

    def test_http_blocks_cross_origin_posts_and_private_files(self):
        http=server.ThreadingHTTPServer(("127.0.0.1",0),server.handler_for(self.app))
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        base="http://127.0.0.1:"+str(http.server_port)
        try:
            req=Request(base+"/api/affiliates/login",data=b'{"email":"partner@example.com"}',headers={"Origin":"https://attacker.invalid","Content-Type":"application/json"})
            with self.assertRaises(HTTPError) as error:
                urlopen(req)
            self.assertEqual(error.exception.code,403)
            for path in ["/server.py","/.env","/data/affiliates.sqlite3","/api/affiliates/dashboard"]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(base+path)
                self.assertEqual(error.exception.code,401 if path.endswith("/dashboard") else 404)
        finally:
            http.shutdown();http.server_close();worker.join()

    def test_production_wsgi_adapter_preserves_auth_and_origin_checks(self):
        wsgi=server.wsgi_for(self.app)
        captured={}
        def start(status,headers):
            captured["status"]=status;captured["headers"]=dict(headers)
        raw=b'{"email":"partner@example.com"}'
        response=wsgi({"REQUEST_METHOD":"POST","PATH_INFO":"/api/affiliates/login","CONTENT_LENGTH":str(len(raw)),"CONTENT_TYPE":"application/json","HTTP_ORIGIN":"https://attacker.invalid","wsgi.input":io.BytesIO(raw)},start)
        self.assertTrue(captured["status"].startswith("403"))
        self.assertIn(b"origin",b"".join(response))
        wsgi({"REQUEST_METHOD":"GET","PATH_INFO":"/api/affiliates/dashboard","wsgi.input":io.BytesIO()},start)
        self.assertTrue(captured["status"].startswith("401"))
        wsgi({"REQUEST_METHOD":"GET","PATH_INFO":"/health","wsgi.input":io.BytesIO()},start)
        self.assertTrue(captured["status"].startswith("200"))

if __name__=="__main__":
    unittest.main()
