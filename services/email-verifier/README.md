# BuyerReach Email Verifier

Internal HTTP wrapper around `AfterShip/email-verifier` v1.4.1. It validates syntax, disposable and role accounts, MX, SMTP recipients and catch-all domains without sending `DATA`.

Required configuration: `VERIFIER_TOKEN`. Production SMTP also requires outbound TCP 25 or `SMTP_PROXY_URL` using a SOCKS URI. Use a dedicated verification IP; never share the outbound IP with campaign sending.

The service is private to the Docker network. Call `POST /v1/verify` with a Bearer token. Full email addresses and credentials are never written to application logs.

Do not treat `unknown`, timeouts or connection failures as invalid mailboxes. Catch-all results are always risky and require a downstream verifier or manual review.
