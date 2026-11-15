# Validation for 0.1.0

Environment: CachyOS x86_64, GNOME 51 / Wayland, GTK 4.24,
libadwaita 1.10, Python 3.14, Google host 157.0.8103.19.

- 14 automated tests: hostile/malformed pasted commands, OAuth failure,
  correct registration arguments, PIN validation, HMAC compatibility against
  OpenSSL, atomic private configuration writes, retained registration fields,
  recoverable disconnect and per-user unit quoting.
- Python syntax compilation and POSIX shell syntax validation.
- Twelve rendered UI previews: overview, first setup, PIN/autostart settings,
  diagnostics; dark/light themes and 540 px narrow layouts. All inspected.
- Fresh application installation with a temporary HOME/XDG_DATA_HOME:
  launcher, desktop entry, SVGs, backend and host packaging files copied;
  no Google registration created and no live service altered.
- Host PKGBUILD built successfully with the pinned official Google download
  and both SHA-256 checks. The packaged host binary exactly matches the
  independently working installed Google host. It was not installed over
  the existing host solely for testing.
- Read-only live backend check: registered current session, service running,
  Google signaling online. Existing registration/PIN reused without changes.

The underlying current-session integration was tested by the owner from a
remote client before Remote++ was packaged. A complete new-account GUI OAuth
flow and installation on a second physical CachyOS machine still require
validation there. GDM login, KDE, X11 and ARM are outside this release's scope.
