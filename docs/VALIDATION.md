# Validation for 0.2.1

0.2.1 updates interface text and documentation. Capture/authentication behavior
is unchanged from 0.2.0.

Environment: CachyOS x86_64, GNOME 51 / Wayland, GTK 4.24,
libadwaita 1.10, Python 3.14, Google host 157.0.8103.19.

- 20 automated tests: hostile/malformed pasted commands, OAuth failure,
  correct registration arguments, PIN validation, HMAC compatibility against
  OpenSSL, atomic private configuration writes, retained registration fields,
  recoverable disconnect and per-user unit quoting. Native GIO interception
  tested on isolated D-Bus with both call APIs and a custom GLib context;
  wrong/unauthenticated events do not release capture. Duplicate and concurrent
  clients, supervisor reconnect grace period, recycling, child termination on
  normal stop and refusal of an unreviewed Google binary are covered.
  Automatic installer migration covers active/inactive access, a fresh install
  without registration, unchanged autostart and refusal before service changes.
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
- Live on-demand integration on 2026-11-15: D-Bus monitoring showed zero
  Mutter CreateSession calls while signaling was online in standby. The owner
  connected from a phone, verified image/control, disconnected and confirmed
  that the sharing indicator disappeared. Host events showed authentication,
  capture release, last-client disconnect and a new standby host. Mutter's
  session subtree was empty after disconnect; Google signaling was online
  again with autostart still enabled. Idle service memory was about 28 MiB.

The current-session and on-demand integrations were tested by the owner from
a remote client. A complete new-account GUI OAuth
flow and installation on a second physical CachyOS machine still require
validation there. GDM login, KDE, X11 and ARM are outside this release's scope.
