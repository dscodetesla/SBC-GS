. "$HERE/cases/button/_common.inc"
# non-pixelpilot player: the script also starts loops for up/down/left/right/center/q1; none has a handler -> all exit
script_args=()
case_setup() {
	button_sb host
	conf_set video_player gstreamer
	for b in btn_q1 btn_q2 btn_q3; do conf_set ${b}_single_press ''; conf_set ${b}_long_press ''; done
}
