# Quilted Tweaks affiliate system — required setup

The updated index.html contains the complete website and affiliate interface. Real email login, private balances, payout requests and Bronze enrollment require the included server.py. Replacing only index.html enables referral-code forwarding to checkout, but does not activate the authenticated dashboard.

No private credentials are included. Do not put private keys in index.html or paste them into a chat.

## 1. Enable the native SellAuth affiliate program

In your shop's SellAuth dashboard, open Affiliates and enable the program:

- Enrollment: Open, so anyone can join without buying first.
- Allow affiliates to edit their code: enabled.
- Attribution: Per order. Referral window: 30 days.
- Minimum order value: 0. Maximum commission cap: 0 (uncapped), if those match your intended offer.
- Balance: Separate affiliate balance.
- Payout requests: enabled. Choose your minimum payout amount and supported payout methods.
- Confirm both Manual Optimization and Premium Utility qualify for commissions. Remove unintended per-product exclusions or commission overrides.

The shop plan must support API access, one affiliate tier, and your expected number of affiliates. If SellAuth blocks a setting because of your plan, upgrade or adjust it there before launching.

## 2. Set up the single Bronze tier

Edit your existing Default tier; do not add another tier:

| Setting | Value |
|---|---|
| Name | Bronze |
| Commission | 20% |
| Buyer discount | 15% |
| Sort order | 1 |
| Public tier | Checked |
| Attribution mode | Per order |
| Attribution window | 30 days |

Save Bronze as the default tier for new affiliates. This package requires only this one tier. Both the public affiliate page and the signed-in overview say "More tiers coming very soon...". There are no active Gold/Diamond tiers, automatic promotions or promised sales thresholds.

If you prefer the optional setup command, run this in the deployed service environment after setting the merchant key:

    python server.py --setup-tiers

The command updates Bronze, or renames your single existing Default tier to Bronze without creating a second tier. It only creates Bronze if no tiers exist. An unrecognized existing tier requires manual review instead of creating another tier. It does not enable the affiliate program or configure enrollment, wallets or payouts.

The service verifies Bronze's 20% commission and 15% buyer discount before syncing; mismatched rates show a configuration error.

## Get your SellAuth API key

1. Sign in at https://dash.sellauth.com/api (Dashboard > Account > API Access).
2. Copy your existing API key. If no key exists/is visible, use the page's generation control; SellAuth's official instructions call it Regenerate.
3. Regenerating an existing key can invalidate integrations using that key. Reuse your existing key when available.
4. In the Railway website service, open Variables and add SELLAUTH_API_KEY with that key as its value. Never put it in index.html, the GitHub repository, a screenshot or a chat.
5. Confirm SELLAUTH_SHOP_ID is 276718 for this shop; the API Access page also shows the Shop ID.

## 3. Configure sign-in email

Create a Resend account, verify a sending domain or subdomain you control, and create a sending-only API key. Add the DNS records Resend supplies without replacing the DNS records that point quiltedtweaks.com to Railway.

Example sender: QT Affiliates <login@quiltedtweaks.com>. Use a sender on the domain you actually verified. A Resend test sender cannot send login codes to arbitrary customers.

## 4. Deploy the included service on Railway

Upload the package contents to the repository for the website service. Keep index.html, server.py, wsgi.py, gunicorn.conf.py, requirements.txt and Dockerfile together in the service root. Railway must build this Dockerfile rather than continue serving the old static-only image. Replace an existing Dockerfile if it still serves only static HTML.

Set the Railway start command to the Dockerfile default, or explicitly:

    gunicorn --config gunicorn.conf.py wsgi:application

Create a persistent Railway volume mounted at /data. Run one replica of this service; its SQLite database stores sign-in sessions, login challenges, request limits and click totals. Orders, commissions, affiliate identities and payouts remain in SellAuth. No second database is required.

Add these Railway variables:

| Variable | Value |
|---|---|
| PUBLIC_URL | https://quiltedtweaks.com |
| SELLAUTH_SHOP_ID | 276718 |
| SELLAUTH_API_KEY | Your private SellAuth merchant API key |
| SESSION_SECRET | A new random secret with at least 32 characters |
| RESEND_API_KEY | Your private Resend sending API key |
| EMAIL_FROM | Your verified sender, for example QT Affiliates <login@quiltedtweaks.com> |
| DATA_DIR | /data |

Railway provides PORT. Set the health check path to /health. Keep the existing public domain pointing at this website service. If www.quiltedtweaks.com is also used, redirect it to quiltedtweaks.com so login origins and referral cookies are consistent.

After configuring tiers and variables, run the read-only API access check in the service environment:

    python server.py --check

For local development only, python server.py runs the development HTTP server. Production uses Gunicorn through the Dockerfile.

You can generate SESSION_SECRET locally with:

    python -c "import secrets; print(secrets.token_hex(32))"

The merchant key needs access to customer records/customer token creation, invoice reads, affiliate tiers, affiliate assignments and payout-request reads. Scope it to this shop if your account supports that. The key is sent only from the server to the official SellAuth API.

Login emails have a 60-second cooldown per address, an IP-based hourly limit and a global ceiling of 200 per hour to limit abuse and email costs.

## 5. Test before announcing the program

1. Open the affiliate page on your deployed site and sign in with an email you control. Verify that the email code arrives, expires, and cannot be reused.
2. Create a custom code. Check the corresponding customer and Bronze assignment in SellAuth.
3. Copy your link. In a separate browser profile, open https://quiltedtweaks.com/?ref=YOURCODE and open either product's checkout.
4. Use a different buyer email: SellAuth rejects self-referrals. Check that the buyer discount is correct before paying.
5. Complete a controlled purchase yourself, then check the SellAuth invoice's affiliate code, commission and affiliate wallet. Check that the website displays the same commission.
6. Confirm the account stays Bronze and shows "More tiers coming very soon...". This release does not promote affiliates after a number of sales.
7. Submit a payout request, check it in SellAuth, and test cancellation while it is pending.

A browser success callback never credits money or creates a fake sale. The service reads paid invoices from SellAuth and excludes orders without a native affiliate commission. Repeated syncs deduplicate invoices, including archived invoices, by ID.

## Tier and discount behavior

Bronze is the only active program tier: 20% commission and 15% audience discount. There are no sales-based promotions. The server never creates or requires Gold/Diamond during startup or normal operation. Seller-assigned external tiers are not overwritten by background synchronization.

Sales are counted as orders, not individual line items. Unpaid, free, refunded and charged-back orders are excluded from the dashboard's paid-referral totals. Synchronization runs about every 30 seconds. SellAuth owns the actual commission balance, calculation and refund adjustments.

The affiliate code is the native SellAuth referral identifier. Its tier's audience discount is applied by the embedded checkout when the link's code is passed with the order. This is not a separate generic coupon database. Share the referral link to ensure the discount and attribution travel together. SellAuth controls stacking rules, commission calculation and rounding.

Cashout requests are stored by SellAuth and reserve funds in the affiliate wallet. You still pay approved cashouts outside SellAuth and mark them paid there. This package never initiates a PayPal or bank transfer.

## What is included

- The requested website theme and affiliate dashboard layout.
- Overview, sales, cashouts, banner download, social sharing and code settings.
- Email-verified sign-in; no shared administrator password or user-selectable customer ID.
- Native SellAuth referral code enrollment, verified balances and payout actions.
- Referral capture on page arrival and forwarding through the existing embedded checkout.
- Daily browser-visit click deduplication. Clicks are approximate analytics and do not generate commission.
- Bronze-only enrollment, with native SellAuth commission balances and no client-side financial ledger.
- The category ticker removed and +300 FPS / -90 process badges labeled as performance targets.

## Verification limits

Automated tests use synthetic SellAuth and email-service responses. They cover one-tier-plan compatibility, safe Default-to-Bronze setup, no promotions after many sales, Bronze enrollment, invalid/unpaid/refunded orders, private invoice data filtering, email-code verification, replay protection, request limits, session persistence, clicks, payout forwarding and token revocation. No live customer, tier, invoice, payout or payment was changed during development.

The affiliate list/payout and token APIs do not all publish detailed response examples. The service handles common documented envelopes and fails with a configuration/service message if a live response differs. A controlled live integration test with your configured credentials is required before calling the program production-verified.

## Support and recovery

- Back up the /data volume. Losing it signs users out and loses click totals; SellAuth commission balances and orders remain intact.
- Keep one replica because SQLite is local to this volume.
- Redeploy after changing environment variables. A changed SESSION_SECRET invalidates existing sessions.
- The service logs no email codes, cookies, private keys, payout details or full invoices.
- If sign-in says not connected, verify every variable, the sending domain and Railway's Dockerfile/start command.
- If synchronization fails, check the tier names/rates, API permissions, plan access and SellAuth availability. The service does not replace a failed sync with invented statistics.
- If attribution fails, verify the program is enabled, the code belongs to an active affiliate, and the buyer email differs from the affiliate email.

Official references:

- https://docs.sellauth.com/guides/affiliates
- https://docs.sellauth.com/developers/embed
- https://docs.sellauth.com/customer-api/introduction
- https://resend.com/docs

## After uploading this ZIP

1. Extract it and replace the files at the root of the existing website repository; uploading the ZIP itself without extracting does not deploy the app.
2. Add all Railway variables listed above, including the SellAuth key and Resend email configuration.
3. Attach the persistent /data volume, use one replica and the included Dockerfile, and redeploy.
4. Run python server.py --check in the deployed service environment. Then test email login and one controlled referral purchase with a different buyer email. The API check does not prove email delivery or payment processing.

This source has automated tests, but no access to your private credentials. It is not a live-verified deployment. A SellAuth API key alone is insufficient for website email sign-in: RESEND_API_KEY, a verified EMAIL_FROM and SESSION_SECRET are also required.

## Diagnose SellAuth HTTP 403 errors

This revision reports the upstream HTTP status and safe API error details rather than the previous generic tier-sync failure. It recognizes Cloudflare error 1010 and includes the response's support reference. It never logs the API Authorization header, email codes, request bodies or full API responses.

If Cloudflare explicitly blocks a request as non-retryable (including error 1010), the service pauses automatic SellAuth requests until restart. It does not change client signatures or bypass security controls. Contact SellAuth support with the reference shown in YOUR Railway logs, shop ID 276718, the failed endpoint /v1/shops/276718/affiliate-tiers and the timestamp. Ask them to review authorized server-to-server API access from your Railway deployment. Restart the service and run python server.py --check after SellAuth resolves the access restriction.

A Cloudflare 1010 response was observed in the development environment without using a merchant key. That observation alone does not prove your Railway request receives the same response. The updated logs establish the specific failure from your deployment. A plain HTTP 403 without a Cloudflare error can still indicate API-key permissions, shop access or another restriction.

For this update, replace server.py with the ZIP's server.py and redeploy; the included frontend remains Bronze only. Your existing Railway variables and persistent volume stay in place. This diagnostic update does not itself grant access through SellAuth's firewall or prove the live integration works.
