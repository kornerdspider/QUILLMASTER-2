#!/bin/bash
# Installs Quill Master 2 for this user (no admin password needed).
# Run from the unpacked folder:  ./install.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/quillmaster2"
mkdir -p "$DEST" "$HOME/.local/share/applications" "$HOME/.local/bin"
rm -rf "$DEST/app"
cp -r "$HERE/Quill Master 2" "$DEST/app"
cp "$HERE/icon.png" "$DEST/icon.png"
ln -sf "$DEST/app/Quill Master 2" "$HOME/.local/bin/quillmaster2"
cat > "$HOME/.local/share/applications/quillmaster2.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Quill Master 2
Comment=Manuscript editor with a shared Story Bible
Exec="$DEST/app/Quill Master 2"
Icon=$DEST/icon.png
Terminal=false
Categories=Office;WordProcessor;
EOF
chmod +x "$HOME/.local/share/applications/quillmaster2.desktop"
echo ""
echo "  Quill Master 2 is installed."
echo "  Open your Menu and search for 'Quill Master 2', or type: quillmaster2"
echo ""
