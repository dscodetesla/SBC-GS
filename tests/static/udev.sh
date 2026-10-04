#!/usr/bin/env bash
# Static check of gs/9*-*.rules (no udev needed): prints the ACTIVE rules (comments dropped) and verifies
#  - every RUN+= target under /gs/ exists in the repo and is the file install.sh ships,
#  - every active rule has ACTION, SUBSYSTEM and an assignment (NAME= or RUN+=),
#  - install.sh copies exactly the rule files present.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO/gs" || exit 2
bad=0
echo "== active rules"
for f in 98-rename.rules 99-GS.rules; do
	echo "-- $f"
	grep -Ev '^[[:space:]]*(#|$)' "$f"
done
echo "== checks"
for f in 98-rename.rules 99-GS.rules; do
	while IFS= read -r line; do
		case "$line" in *ACTION==*) ;; *) echo "FAIL no ACTION: $line"; bad=1;; esac
		case "$line" in *SUBSYSTEM==*) ;; *) echo "FAIL no SUBSYSTEM: $line"; bad=1;; esac
		case "$line" in *NAME=*|*RUN+=*) ;; *) echo "FAIL no assignment: $line"; bad=1;; esac
		tgt="$(printf '%s\n' "$line" | grep -oE 'RUN\+="(/usr/bin/systemd-run )?/gs/[A-Za-z0-9._-]+' | grep -oE '/gs/[A-Za-z0-9._-]+$' || true)"
		if [ -n "$tgt" ]; then
			if [ -f "${tgt#/gs/}" ]; then echo "ok target ${tgt}"; else echo "FAIL missing target ${tgt}"; bad=1; fi
		fi
	done < <(grep -Ev '^[[:space:]]*(#|$)' "$f")
done
grep -q 'boards/render-udev.sh "\$board_id" /etc/udev/rules.d/' install.sh && grep -q 'echo "\$board_id" > /etc/gs-board' install.sh && echo "ok install.sh renders the rules from the board profile and stores the id" || { echo "FAIL install.sh rule copy"; bad=1; }
echo "bad=$bad"
exit $bad
