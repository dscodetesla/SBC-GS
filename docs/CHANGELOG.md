# CHANGELOG (журнал змін гілки `claude/hopeful-brown-k5mmbm`)

Стисло, нове зверху; деталі в повідомленнях комітів і `docs/DECISIONS.md`.

## 2026-10-04
- `916a5fb` пакет перенесення: `docs/HANDOFF-2026-10-04.md`, `docs/handoff/*`, вказівка в `CLAUDE.md`.
- Актуалізація проєктних файлів: `MEMORY.md`, `AGENTS.md` §6a, `docs/STATUS.md` §9, `docs/KNOWLEDGE.md`, `docs/ROADMAP-EXECUTION.md`, нові `docs/IDENTITY.md`, `docs/TODO.md`, `docs/CHANGELOG.md`.

## 2026-10-03
- `ffefcfa` міграція Pi 3/4/5: автовизначення плати, `br0` через nmcli (R7), MBR/config-txt/без OTG у `gs-init.sh`, мітка розділу відео, `Restart=on-failure`, будь-який HDMI-конектор, `osd_mavlink_port`, мертвий плеєр, `button.sh` без GPIO-лінії, udev через `systemd-run`; тести `static/board-detect`, `init/nm_backend` та cases stream/gs/button.
- `deca4e9` watchdog mavp2p, `doctor`/`ingest` читають `gpiodetect` (RP1 за міткою), рішення R2 (перегляд), R5, R6.
- `58684a7` CI: портабельний golden `gsconf`, twin K04/K05 проти oracle.
- Раніше: шари симуляції (models, validate, twin, fuzz, netfetch, powerlab, evdev, virt, dkms, bio), D1-D22, атомарний `gs.conf`, профіль Pi 5, VA-API відео на хості, міст TX12, рішення R1-R4.
