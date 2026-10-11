#!/bin/sh
set -eu

ADDON_ID=service.kodi.usbmanager
SOURCE_DIR=$(unset CDPATH; cd -- "$(dirname -- "$0")" && pwd)
DEST_DIR=/storage/.kodi/addons/$ADDON_ID
RESTIC_VERSION=0.19.1
RESTIC_ARCHIVE=restic_${RESTIC_VERSION}_linux_amd64.bz2
RESTIC_URL=https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/${RESTIC_ARCHIVE}
RESTIC_SHA256=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
TMP_DIR=$(mktemp -d /tmp/kodi-usb-manager.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM

if [ "$(id -u)" -ne 0 ] || [ ! -d /storage/.kodi ]; then
  echo "Dieses Skript muss als root direkt auf LibreELEC laufen." >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64) ;;
  *) echo "Der Installer unterstützt derzeit LibreELEC x86_64." >&2; exit 1 ;;
esac

echo "Installiere $ADDON_ID …"
mkdir -p "$DEST_DIR/resources/lib" "$DEST_DIR/resources/bin"
cp "$SOURCE_DIR/addon.xml" "$SOURCE_DIR/service.py" "$DEST_DIR/"
cp "$SOURCE_DIR/resources/lib/"*.py "$DEST_DIR/resources/lib/"
chmod 0755 "$DEST_DIR/service.py" "$DEST_DIR/resources/lib/"*_runner.py

if [ ! -x "$DEST_DIR/resources/bin/restic" ]; then
  echo "Lade restic $RESTIC_VERSION …"
  curl -fL "$RESTIC_URL" -o "$TMP_DIR/$RESTIC_ARCHIVE"
  printf '%s  %s\n' "$RESTIC_SHA256" "$TMP_DIR/$RESTIC_ARCHIVE" | sha256sum -c -
  bzip2 -dc "$TMP_DIR/$RESTIC_ARCHIVE" >"$TMP_DIR/restic"
  chmod 0755 "$TMP_DIR/restic"
  "$TMP_DIR/restic" version
  mv "$TMP_DIR/restic" "$DEST_DIR/resources/bin/restic"
fi

sync
echo "Installation abgeschlossen. Kodi wird neu gestartet."
systemctl restart kodi
