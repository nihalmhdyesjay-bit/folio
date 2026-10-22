# Folio online sync server 2.4.0

Cloud relay for Folio Android and Windows. Deploy one isolated service per business.
No customer data or credentials are included in this repository.

Run the Dockerfile with one replica and a persistent volume mounted at /data.
Expose port 8080 through HTTPS. Set FOLIO_DATA=/data.
Set FOLIO_DESKTOP_KEY to a private random key (30+ characters).
For a fresh business, use FOLIO_SETUP_MODE=new and set FOLIO_FIRST_USERNAME,
FOLIO_FIRST_PASSWORD (10+ characters), and FOLIO_FIRST_NAME.
For migration, use FOLIO_SETUP_MODE=migrate and the original desktop connection
key. The desktop transfers its existing sync database after authentication.
An initialized database cannot be replaced through the migration endpoint.

Keep desktop Folio open and online to validate stock and post phone entries.
Phones can save entries offline and upload when the internet returns.
Back up the persistent volume and the desktop books regularly.
Health endpoint: /health. Do not run multiple replicas against SQLite.
