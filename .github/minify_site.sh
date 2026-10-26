#!/usr/bin/env bash
# .github/minify_site.sh — run just before "firebase deploy", AFTER the commit step: the files on the website are compressed and
# carry no code notes, while the repo keeps the readable versions. (The repo is public, so this only stops casual
# "view source" copying of the live site.)
#   • every .html in public/: whitespace + comments out, inline <script> and <style> compressed (html-minifier-terser)
#   • auth.js and game2048.js (ES modules), hdr.js, sw.js: compressed, comments out, names shortened (terser)
# Any file that fails to compress is left exactly as it was (the deploy never breaks because of this step).
set -uo pipefail
cd "$(dirname "$0")/../public" || exit 0
HM="npx -y html-minifier-terser@7.2.0"
TS="npx -y terser@5.36.0"
ok=0; bad=0
for f in *.html; do
  [ -f "$f" ] || continue
  if $HM --collapse-whitespace --conservative-collapse --remove-comments --minify-css true \
        --minify-js '{"compress":{"passes":1},"mangle":true,"format":{"comments":false}}' \
        -o "$f.min" "$f" 2>/dev/null && [ -s "$f.min" ]; then
    mv "$f.min" "$f"; ok=$((ok+1))
  else rm -f "$f.min"; bad=$((bad+1)); echo "  kept as is: $f"; fi
done
for f in auth.js hdr.js sw.js game2048.js; do
  [ -f "$f" ] || continue
  MOD=""; case "$f" in auth.js|game2048.js) MOD="--module" ;; esac
  if $TS "$f" $MOD -c -m --comments false -o "$f.min" 2>/dev/null && [ -s "$f.min" ]; then
    mv "$f.min" "$f"; ok=$((ok+1))
  else rm -f "$f.min"; bad=$((bad+1)); echo "  kept as is: $f"; fi
done
echo "Compressed $ok files for the website ($bad kept as they were)"
# proof in the run log: code notes left in what is about to be deployed (should be 0 everywhere)
for f in *.html auth.js hdr.js sw.js game2048.js; do
  [ -f "$f" ] || continue
  n=$(grep -o '<!--' "$f" | wc -l); m=$(grep -oE '/\*[^*]|[;{}(,]\s*// ' "$f" | wc -l)
  echo "  $f: $(wc -c < "$f") bytes, html notes $n, code notes $m"
done
exit 0
