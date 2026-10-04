# SBC-GS: контекст для асистента

Це форк збірника образу наземної станції OpenIPC для Radxa Zero 3W. Триває порт на Raspberry Pi 3B+/4/5.

**Нова сесія: спершу `docs/HANDOFF-2026-10-04.md`.**

**Спершу прочитати:** `docs/KNOWLEDGE.md` (перевірені факти, помилки, досвід, відкриті питання), далі `docs/ROADMAP-EXECUTION.md` (поточний план), `docs/GAPS.md`, `docs/PI-PORT.md`, `docs/CHAINS.md`, `docs/BENCH-HARDWARE.md`, `docs/GUIDE.md`, `bench/README.md`; правила агентів `AGENTS.md`, пам'ять проєкту `MEMORY.md`, знімок стану й план перевірки на залізі `docs/STATUS.md`; нове: `docs/MAVLINK-ROUTER.md`, `docs/GS-MAVLINK.md`, `docs/BOARD-RPI4.md`, `docs/SECURITY-DEFAULTS.md`, `docs/REPRODUCIBLE-BUILD.md`.

## Правила роботи
- Факти подавати з позначкою достовірності (SRC/REPO/SNIP/INF/HW) і джерелом. Непідтверджене не видавати за факт.
- Ключові цитати перечитувати напряму (`raw.githubusercontent.com`), не покладатися лише на підсумки `WebFetch`.
- Не обходити політику мережі (403/407) і не вимикати перевірку TLS.
- Безпека: не підключати `bench/gs_mav.py` і `fake_fc.py` до реального апарата з гвинтами; RC через wfb-ng не може бути єдиним каналом керування.
- Працювати в гілці `claude/hopeful-brown-k5mmbm`; PR не створювати без прямого прохання.

## Перевірка без заліза
`cd bench && PY=<python з pymavlink> ./bench.sh loopback`; скрипти перевіряти `shellcheck -x *.sh` з каталогу `bench/`.
`tests/run.sh` (golden + static). Набір ганяти також як `nobody` через `setpriv` на копії репозиторію (під root тести можуть бути хибно зеленими).

## Кінцевий результат і дисципліна
Мета: працюючий контур AIR<->GS (OpenIPC 720p + RTL8812AU, ArduPilot Matek H743 v3, ELRS Gemini; GS Pi 5 + TX12 MKII + хост Ubuntu 26+), а не обв'язка. Контур і невідомі: `MEMORY.md` §2. Факт без тегу й джерела не подавати; SYNTH/модель не видавати за вимір. Делегуючи, давати неперетинні файли й забороняти `git commit`; результат агента перевіряти самому (`AGENTS.md` §4).

## Симуляція (стан 2026-10-03)
Усі шари офлайн: `PY=<venv з pymavlink> tests/sim/layers.sh` (models, validate, twin, fuzz, bio, dkms; ≈40 с), `tests/sim/virt/run.sh all` (QEMU, довго), `tests/precommit.sh` перед комітом. Опис і межі: `docs/SIM-SCENARIOS.md`, `SIM-VALIDATION.md`, `SIM-TWIN.md`, `SIM-FUZZ.md`, `SIM-VIRT-DEVICES.md`, `SIM-DKMS.md`; рішення: `docs/DECISIONS.md`; динамічна конфігурація: `docs/CONFIG.md`. Усі числа моделей SYNTH/UNMEASURED, не виміри; залізо (понеділок) окремий pipeline: `docs/STATUS.md` §4, `bench/doctor.sh`, `tests/sim/models/calib.py`.
