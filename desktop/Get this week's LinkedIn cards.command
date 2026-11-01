#!/bin/bash
# Downloads this week's two LinkedIn cards (all tech stocks, and UK tech stocks) with their post text.
# Built on Mondays at about 7:30am UK time. Gets the newest ones, whatever day you run it.
# Uses the same github-token.txt as "Get today's email".
cd "$(dirname "$0")" || exit 1
mkdir -p linkedin
API="https://api.github.com/repos/meadowside/alltechstocks/contents/data/linkedin"

if [ ! -f github-token.txt ]; then
  echo "The GitHub key is missing."
  echo "Save it in a file called github-token.txt next to this button, then try again."
  read -n1 -s -p "Press any key to close."
  exit 1
fi
TOKEN=$(tr -d '[:space:]' < github-token.txt)
D=$(date +%Y-%m-%d)

get() {  # get <file in repo> <save as>
  curl -sSL -o "$2" -w "%{http_code}" -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github.raw" "$API/$1?ref=main"
}

CODE=$(get latest-all.png "linkedin/$D-all.png")
if [ "$CODE" = "200" ]; then
  get latest-all.txt "linkedin/$D-all.txt" > /dev/null
  if [ "$(get latest-uk.png "linkedin/$D-uk.png")" = "200" ]; then
    get latest-uk.txt "linkedin/$D-uk.txt" > /dev/null
  else
    rm -f "linkedin/$D-uk.png"
  fi
  echo "Downloaded the cards and post text into the linkedin folder."
  for f in "linkedin/$D-all.txt" "linkedin/$D-uk.txt"; do [ -f "$f" ] && open -e "$f"; done
  open -R "linkedin/$D-all.png"
else
  rm -f "linkedin/$D-all.png"
  if [ "$CODE" = "401" ] || [ "$CODE" = "403" ]; then
    echo "GitHub turned the key down. It may have expired."
    echo "Make a new one and paste it into github-token.txt."
  elif [ "$CODE" = "404" ]; then
    echo "No LinkedIn cards yet. They're built on Mondays at about 7:30am UK time."
  else
    echo "Couldn't reach GitHub (code $CODE). Check the internet connection and try again."
  fi
  read -n1 -s -p "Press any key to close."
  exit 1
fi
