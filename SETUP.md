# Quilted Tweaks — SellAuth website update

The included index.html is the complete updated page based on the current live quiltedtweaks.com website. Both supplied Premium Utility screenshots, the logo, and the existing site images are embedded in it. No image folder, build command, API key, or new environment variable is required.

## Put it live

1. Open the GitHub repository connected to the Railway service that serves quiltedtweaks.com.
2. Replace the currently served index.html with this index.html, keeping the same location and filename. For example, if the existing file is public/index.html, replace that file.
3. Commit and push the change. Let Railway redeploy, or manually redeploy that service if automatic deployments are disabled.
4. Open the live website in a private browser window. Check both Buy buttons. Each should open the matching cart over the website, with quantity one: Manual Optimization $25 USD and Quilted Premium Utility $15 USD.

Keep your current domain and DNS settings. Connecting the root domain to SellAuth is unnecessary for this embedded integration and would replace your current website.

## Finish product delivery in SellAuth

- Premium Utility: configure automatic key delivery and stock it with the website keys produced by your existing Discord bot's /genwebsitekeys command. Include the current EXE download link and activation instructions in the delivery message. The licensing backend enforces the one-PC rule; this website does not create or validate licenses.
- Manual Optimization: include clear delivery instructions explaining how the buyer contacts you and arranges their session, using their order details. A service session itself still needs to be arranged.
- Confirm that both products have the intended payment methods enabled, enough stock, and correct delivery content. Perform a controlled test purchase yourself after deployment to verify payment detection and delivery.

No real payment was made during development. Do not treat a client-side success event as proof of payment for any custom fulfillment.

## Checkout behavior

The website loads SellAuth's official v3 SDK only when a buyer clicks a purchase button. It requests a dark checkout modal for the selected product, so the parent page stays on quiltedtweaks.com during normal embedded checkout. PayPal, banks, and other payment providers may require their own authorization page or popup. The official SDK also has an external checkout fallback if the embedded frame cannot load; it is not a guarantee that every possible checkout step stays within your domain.

The embedded checkout retains SellAuth's payment UI and branding. Its appearance can be adjusted using the checkout branding options available in your SellAuth account. The website itself keeps the existing mint/teal theme.

## Public product configuration

| Product | Shop ID | Product ID | Variant ID | Website price |
| --- | ---: | ---: | ---: | ---: |
| Manual Optimization | 276718 | 964131 | 1861456 | $25 USD |
| Quilted Premium Utility | 276718 | 964091 | 1861076 | $15 USD |

These identifiers are public and are already embedded in index.html. Never add a private SellAuth API key to this file. SellAuth determines the actual checkout price; update the website's displayed prices when you change them in SellAuth.

This update does not add affiliate tracking, a SellAuth webhook, or automatic sales synchronization with the Discord bot's /dashboard. Existing stock-key delivery works through SellAuth; backend reporting and affiliates can be connected separately later.

## Hosting and troubleshooting

- Serve index.html over HTTPS on your existing website. Opening it as a local file is not a production checkout test.
- Allow https://static.sellauth.com in script-src and https://quiltedtweaks.mysellauth.com in frame-src if you apply a restrictive Content Security Policy. The live site's current broad HTTPS policy already allows these.
- If a content blocker prevents the SDK from loading, the Buy button recovers and shows a retry message.
- If you recreate either product or its variant in SellAuth, update the public IDs in the catalog near the end of index.html.

## Verification

The updated page was checked in a browser, including the supplied screenshots and screenshot tabs. Product IDs and prices were verified against the public store. Automated checks cover the correct cart for every purchase button, modal configuration, concurrent clicks, load failures/retries, timeouts, and keyboard tab navigation. Full payment and delivery verification must be completed by you on the deployed website.
