# TODO / чекліст / задачі (живий список, 2026-10-04)

Оновлювати при кожній зміні стану. Джерело порядку: `docs/handoff/plan-rollout-pi.md`. Пріоритет зверху вниз.

## Завдання (кожне окремим комітом з тестами)
- [ ] 0. `PY=<venv з pymavlink> tests/sim/layers.sh` після `ffefcfa` (не перезапускали).
- [ ] 1. B1: `/media/root-ro` лише коли overlayroot змонтований (`gs/gs-init.sh:50`, `gs/gs-applyconf.sh:138-141,222`); тест у sandbox без `/media/root-ro`.
- [ ] 2. B2: невідома плата не переписує таблицю розділів (рекомендація: помилка в `board.sh`/`gs-init.sh`); тест `static/board-detect` з невідомою моделлю.
- [ ] 3. Профіль `rpi3bp` (MBR, brcmfmac, без OTG, H.264); `gs/boards/validate.sh`, храповик UNVERIFIED.
- [ ] 4. Декодер у профілі замість `mppvideodec` (`gs/stream.sh:101-102`), `video_player='gstreamer'` на Pi; golden на командний рядок.
- [ ] 5. `gs-mavlink` у `install.sh`/`gs.sh`, порт OSD окремо (`osd_mavlink_port` 14551), ключі `hdmi_wait_timeout`/`osd_mavlink_port` у `gs.conf`, реєстр, фікстура `legacy-defaults.txt`, `config.out`.
- [ ] 6. `Restart=` для `local_node` (`gs/wfb.sh:62`) і hotplug `wfb_rx` (`gs/wfb.sh:123-124`).
- [ ] 7. Перший запуск на залозі (Pi 4, потім Pi 5): `docs/handoff/TEST-PLAN-HW.md` H0-H11.

## Чекліст перед комітом
`tests/precommit.sh` зелений · `tests/run.sh` як `nobody` на повній копії · `layers.sh` · golden змінені свідомо з поясненням · коміт лише перевірених файлів · трейлери Co-Authored-By/Claude-Session · пуш у `claude/hopeful-brown-k5mmbm`.

## Задачі, монітори, джоби
- PR [dscodetesla/SBC-GS#1](https://github.com/dscodetesla/SBC-GS/pull/1): підписка на події; CI на `916a5fb` без збоїв у сторонніх чеках. Нових PR не створювати.
- Фонових задач і моніторів зараз немає.
- Субагенти: зараз не запущені; сесія `session_01MQQTFBkXpnmjEgXgocKCfY` (Remote Control) простоює без проєкту, повідомлення їй не надсилались.

## Блокери зовнішні (потрібна людина)
- Доступ до плати: правило дозволу для `tailscaled` у `settings.json` або сесія на машині в tailnet; fingerprint плати `SHA256:GtdL8bboT5Bw0RUkDOEnqas2F7RZpPoI8sWNIytjSw0` (від користувача, не перевірений).
- Рішення: тип знижувача 12S, модель зовнішнього TX, відеоплеєр на Pi, GPU/Wayland хоста.
