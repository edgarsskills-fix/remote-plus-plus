# Capture on demand

Remote++ 0.2 uses the original Google host plus a small native adapter. It does
not rebuild Chromium, modify Google's binary, or change GNOME Shell.

## Why an adapter is needed

The Google host eagerly initializes `GnomeRemoteDesktopSession` inside
`HostProcess::StartHost()`, before any client is authenticated. Initialization
creates and starts a Mutter RemoteDesktop/ScreenCast session. GNOME correctly
shows the sharing indicator for this session, including while the host is idle.

The same asynchronous initialization is awaited by `GnomeInteractionStrategy`
when a client requests its desktop, so it can be deferred without disabling
Google's signaling or PIN authentication.

Upstream source reviewed:

- [Host startup](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/remoting_me2me_host.cc)
- [GNOME session](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/linux/gnome_remote_desktop_session.cc)
- [Client initialization](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/linux/gnome_interaction_strategy.cc)
- [Authentication lifecycle](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/chromoting_host.cc)
- [Authenticated client events](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/host_event_logger_posix.cc)

## Implementation

The installer applies the adapter automatically. On a new machine it is used
by the first normal activation of access. On an already configured machine it
upgrades the existing integration and restarts an active service. An inactive
or masked service remains inactive; the autostart setting is not changed.
No additional commands, Google authorization or PIN changes are needed.

`capture_gate.py` checks the SHA-256 of `libremoting_core.so` before launch.
Only Google host 157.0.8103.19 with SHA-256
`a5088ff991395e5877216b48bc54f488927382daf29f734668c8efc996a59d7c`
is accepted. An unreviewed update is refused rather than falling back to
eager capture.

`native/capture_gate.c` is compiled locally with gcc and GIO development
headers, then loaded with `LD_PRELOAD` into this host process only. Both GIO
asynchronous call variants (with and without Unix FDs) are covered. Only
`org.gnome.Mutter.RemoteDesktop.CreateSession` on the well-known Mutter bus
and object is deferred. All other D-Bus calls pass through.

The original callback context, variants, cancellable and Unix FD list are
retained. The deferred call is released only after Google's
`HostEventLoggerPosix::OnClientAuthenticated()` emits its NOTICE event.
Initial connection attempts, access denials and ordinary INFO logs do not
release it. Original syslog events are forwarded unchanged.

Authenticated clients are tracked by signaling ID in process memory. Duplicate
events do not increase the count; an unknown disconnect does not reduce it.
After the last authenticated client leaves, a private socket notifies the
supervisor. A 350 ms grace period accommodates immediate reconnects. The
supervisor stops the child, waits for exit and starts a fresh host in standby.
Mutter releases the old capture when its D-Bus client exits. Reconnecting can
take a few seconds while Google signaling comes back online.

The socket contains only state events, not account names, tokens or PINs.
Missing supervisor communication terminates the host. Linux parent-death
signaling prevents an active child from surviving an unexpectedly killed
supervisor. Normal service stop forwards termination to the child and does
not recycle it.

## Limits and rollback

- This adapter is tied to the audited Google binary and event format.
- It preserves the logged-in desktop, but restarts the host after the last
  client; it is not a change to Google's internal singleton lifetime.
- The system sharing indicator is controlled by GNOME normally. Other screen
  sharing applications can independently keep it visible.
- Credentials, PIN, Samba and other services are unaffected.
- To stop, use Remote++ → «Выключить доступ».
- For rollback, stop the service, reinstall release 0.1.0, then turn access
  back on. The existing Google registration is reused.

Tests use an isolated D-Bus service with the same method signature and a custom
thread-default GLib context. They verify no capture call before authentication,
forwarding of unrelated calls, both GIO APIs, callback completion, duplicate
events and multiple clients. Supervisor tests cover recycling and service stop.
