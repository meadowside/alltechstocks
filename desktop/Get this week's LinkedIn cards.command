#!/bin/bash
# Downloads this week's two LinkedIn cards (all tech stocks, and UK tech stocks) with their post text.
# Built on Mondays at about 7:30am UK time. Gets the newest ones, whatever day you run it.
cd "$(dirname "$0")" || exit 1
mkdir -p linkedin
D=$(date +%Y-%m-%d)
BASE="https://raw.githubusercontent.com/meadowside/alltechstocks/main/data/linkedin"

get() {  # get <file in repo> <save as>; succeeds only if the file was there
  if curl -fsSL "$BASE/$1?nocache=$(date +%s)" -o "$2.tmp"; then
    mv "$2.tmp" "$2"
  else
    rm -f "$2.tmp"
    return 1
  fi
}

if get latest-all.png "linkedin/$D-all.png"; then
  get latest-all.txt "linkedin/$D-all.txt"
  get latest-uk.png "linkedin/$D-uk.png" && get latest-uk.txt "linkedin/$D-uk.txt"
  echo "Downloaded the cards and post text into the linkedin folder."
  for f in "linkedin/$D-all.txt" "linkedin/$D-uk.txt"; do [ -f "$f" ] && open -e "$f"; done
  open -R "linkedin/$D-all.png"
else
  echo "No LinkedIn cards yet. They're built on Mondays at about 7:30am UK time."
  echo "If it's later than that, check the internet connection and try again."
  read -n1 -s -p "Press any key to close."
  exit 1
fi
