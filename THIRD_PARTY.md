# Third-party components

Remote++ is an independent installer and configuration utility. It is not an
official Google product and is not affiliated with Google.

**Chrome Remote Desktop** is developed by Google / The Chromium Authors.
Google binaries, account credentials and host registrations are not included
in this repository or in Remote++ releases. On installation, Remote++ downloads
the pinned Debian package directly from `https://dl.google.com` and verifies
its SHA-256 checksum with `makepkg` before installing a local Arch package.
The original copyright/license notice is installed under
`/usr/share/licenses/remote-plus-plus-host/copyright`.
Use of Google's service is subject to Google's terms and privacy policy.

The current-session integration uses the Google host's existing GNOME/Mutter
backend. PIN hashing follows Chromium's `GetSharedSecretHash` and `MakeHostPinHash`
protocol: HMAC-SHA256 with host ID as key and PIN as message. References:

- https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/protocol/auth_util.cc
- https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/pin_hash.cc
- https://chromium.googlesource.com/chromium/src/+/refs/heads/main/remoting/host/linux/gnome_remote_desktop_session.cc

The Remote++ application icon is an original SVG by this project, released
under the MIT license. Its rounded-square and `++` badge follow the owner's
Disk++, Torrent++, and ROG++ application family. No Google/Chrome logo is used.

GTK, libadwaita, PyGObject, systemd, pacman and makepkg are provided by the
operating system and retain their respective licenses.
