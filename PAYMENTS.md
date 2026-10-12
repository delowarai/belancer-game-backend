# Belancer payments and access

All 33 built-in games default to free. Five ranked games always remain free. Admin game policies can price practice releases in integer minor units: 100 BDT = 10000, 5 USD = 500. Prices come from the database, never the checkout request.

Run `alembic upgrade head` after backing up the database. Revision 0004 adds policies, entitlements, orders, practice history and cosmetic campaign saves. Configure server environment variables using `.env.example`; never put secrets in frontend variables or source control.

Bangladesh checkout uses SSLCommerz. The available bKash/Nagad channels depend on the merchant agreement and gateway configuration. International checkout uses Stripe. A single game grants permanent access; the premium pass grants a fixed number of days and is renewed manually. There is no automatic subscription billing.

Set PAYMENT_PUBLIC_ORIGIN to the canonical HTTPS site origin. Reverse proxy `/api` to FastAPI and serve frontend routes from the same origin. Configure Stripe events `checkout.session.completed` and `checkout.session.async_payment_succeeded` to `/api/billing/stripe/webhook`. Set STRIPE_WEBHOOK_SECRET for that exact endpoint. SSLCommerz initiation supplies `/api/billing/sslcommerz/ipn` and `/api/billing/sslcommerz/return`.

Stripe requires an authenticated raw-body signature and matching order/session/amount/currency/environment. SSLCommerz requires server-side transaction validation and matching amount/currency/transaction/validation ID. Browser returns do not grant access. Duplicate notifications grant access once. Sandbox/test payments are recorded but never grant premium access.

Admin Monetization controls provider switches, pass duration and prices, refund text and support address. Secrets remain in server environment variables. Administrators can inspect orders, grant dated access and revoke access with an audit reason. Keep payments disabled until merchant onboarding and real provider sandbox testing are complete.

Refunds currently require the merchant provider dashboard and a corresponding audited access revocation in Belancer admin. Automated refunds, disputes, recurring subscriptions and settlement reconciliation are not implemented. Client game code is downloadable; access checks gate normal entry and server APIs, not DRM. Only the five server-scored games have authoritative ranked results. Cosmetic campaign progress and activity history never count as verified competitive scores or purchased currency.

Before public launch: provision HTTPS, database backups and monitoring, set COOKIE_SECURE=true and the correct PUBLIC_ORIGIN, verify both real gateway sandbox flows, publish approved support/privacy/terms/refund information, and obtain deployment authorization. No live payment or deployment is performed by this change.

Provider references: [SSLCommerz API](https://developer.sslcommerz.com/doc/v4/), [Stripe webhook signature](https://docs.stripe.com/webhooks/signature).
