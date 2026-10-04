script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set append_kernel_cmdline "quiet loglevel=3"; conf_set system_wide_screen_mode yes; conf_set screen_mode 1920x1080@60
}
