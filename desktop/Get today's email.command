#!/bin/bash
# Downloads today's All Tech Stocks email (editable Markdown) and its Substack cover image.
cd "$(dirname "$0")" || exit 1
mkdir -p emails
D=$(date +%Y-%m-%d)
F="emails/$D.md"
C="emails/$D-cover.png"
BASE="https://raw.githubusercontent.com/meadowside/alltechstocks/main/data/emails"
if [ -f "$F" ]; then
  echo "Today's email is already in the emails folder. Opening it."
else
  if curl -fsSL "$BASE/$D.md?nocache=$(date +%s)" -o "$F.tmp"; then
    mv "$F.tmp" "$F"
    echo "Downloaded $D.md"
  else
    rm -f "$F.tmp"
    echo "Today's email isn't ready yet. It's built just after 6:30am, Monday to Friday."
    echo "Try again in a few minutes."
    read -n1 -s -p "Press any key to close."
    exit 1
  fi
fi
# Cover 1, 2 and 3 match title ideas 1, 2 and 3.
for N in "" "-2" "-3"; do
  CN="emails/$D-cover$N.png"
  [ -f "$CN" ] && continue
  if curl -fsSL "$BASE/$D-cover$N.png?nocache=$(date +%s)" -o "$CN.tmp"; then
    mv "$CN.tmp" "$CN"
  else
    rm -f "$CN.tmp"
  fi
done
open -e "$F"
[ -f "$C" ] && open -R "$C"
