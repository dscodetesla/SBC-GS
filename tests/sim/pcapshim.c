/*
 * LD_PRELOAD shim for TESTS ONLY: lets wfb_rx (svpcom/wfb-ng) open an Ethernet veth as if it were a
 * monitor-mode radiotap interface. wfb_rx refuses anything but DLT_IEEE802_11_RADIO and installs a BPF
 * filter written for radiotap; on a veth the only traffic is what air_relay.py injects, so the filter is skipped.
 * Not a model of the Wi-Fi driver or of RF.
 */
#include <stddef.h>
int pcap_datalink(void *p) { (void)p; return 127; }                    /* DLT_IEEE802_11_RADIO */
int pcap_setfilter(void *p, void *f) { (void)p; (void)f; return 0; }
