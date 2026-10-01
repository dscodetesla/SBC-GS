# SBC-GS: контекст для асистента

Це форк збірника образу наземної станції OpenIPC для Radxa Zero 3W. Триває порт на Raspberry Pi 3B+/4/5.

**Спершу прочитати:** `docs/KNOWLEDGE.md` (перевірені факти, помилки, досвід, відкриті питання), далі `docs/ROADMAP-EXECUTION.md` (поточний план), `docs/GAPS.md`, `docs/PI-PORT.md`, `docs/CHAINS.md`, `docs/BENCH-HARDWARE.md`, `docs/GUIDE.md`, `bench/README.md`.

## Правила роботи
- Факти подавати з позначкою достовірності (SRC/REPO/SNIP/INF/HW) і джерелом. Непідтверджене не видавати за факт.
- Ключові цитати перечитувати напряму (`raw.githubusercontent.com`), не покладатися лише на підсумки `WebFetch`.
- Не обходити політику мережі (403/407) і не вимикати перевірку TLS.
- Безпека: не підключати `bench/gs_mav.py` і `fake_fc.py` до реального апарата з гвинтами; RC через wfb-ng не може бути єдиним каналом керування.
- Працювати в гілці `claude/hopeful-brown-k5mmbm`; PR не створювати без прямого прохання.

## Перевірка без заліза
`cd bench && PY=<python з pymavlink> ./bench.sh loopback`; скрипти перевіряти `shellcheck -x *.sh` з каталогу `bench/`.
