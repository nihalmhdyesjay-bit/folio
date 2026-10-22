# Folio multi-business relay 2.5.0

One shared service, separate SQLite databases and credentials for each business.
Deploy one replica with a persistent /data volume and HTTPS on port 8080.
FOLIO_DATA=/data. FOLIO_DESKTOP_KEY is the private supplier/operator key.
No customer data, operator keys or activation codes are included in this repository.

Operators issue one-time activation codes through POST /admin/licenses.
Customers activate through the Windows app; each receives a /b/<business-code> URL.
Business desktop keys and staff sessions are isolated by business.

Pending/rejected records remain until desktop resolution. After durable desktop
acknowledgement, transaction bodies and PDFs are removed. Minimal delivery IDs,
hashes and status remain for 90 days; expired new phone submissions are rejected.
Current catalogs expire after 24 hours without desktop refresh. Cleanup runs hourly.
The PC books and backups remain the accounting record. Phone copies are original
snapshots and do not reflect later PC changes. No historical cloud backup is provided.

Initial safeguards: 100 businesses, 1,000 pending records per business, 2 MB catalogs,
5,000 rows per catalog list, storage reserve and activation/login request limits.
Monitor usage and load-test before broad rollout; these limits are not capacity guarantees.
The server does not implement subscription billing or payments.

Health: /health (multibusiness=true). Business health: /b/<code>/health.
Use separate operator credentials and never ship them in customer installers.
