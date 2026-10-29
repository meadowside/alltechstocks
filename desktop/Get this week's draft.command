#!/bin/bash
# Downloads the latest All Tech Stocks weekly draft (editable Markdown).
# Built on Saturdays at about 6:25am UK time. Opens the newest one, whatever day you run it.
cd "$(dirname "$0")" || exit 1
mkdir -p weekly
BASE="https://raw.githubusercontent.com/meadowside/alltechstocks/main/data/weekly"
TMP="weekly/latest.md.tmp"
if curl -fsSL "$BASE/latest.md?nocache=$(date +%s)" -o "$TMP"; then
  # Name the file after the week it covers, read from its first dated line.
  D=$(grep -m1 -o "Week to [A-Za-z]* [0-9]* [A-Za-z]* [0-9]*" "$TMP" | sed 's/Week to [A-Za-z]* //; s/ /-/g')
  F="weekly/${D:-latest}.md"
  if [ -f "$F" ]; then
    rm -f "$TMP"
    echo "This week's draft is already in the weekly folder. Opening it."
  else
    mv "$TMP" "$F"
    echo "Downloaded $F"
  fi
  open -e "$F"
else
  rm -f "$TMP"
  echo "No weekly draft yet. It's built on Saturdays at about 6:25am UK time."
  read -n1 -s -p "Press any key to close."
  exit 1
fi
