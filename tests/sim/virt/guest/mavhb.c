#define _XOPEN_SOURCE 600
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
/* mavhb: tiny MAVLink 2 heartbeat generator / UDP receiver for the virtual-USB FC test.
 *   mavhb gen <tty> <sysid> <period_ms> <seconds>   write HEARTBEATs to a tty (the gadget side /dev/ttyGS0), re-opening it if it vanishes
 *   mavhb rx  <udp_port> <sysid> <seconds>           act as a GCS client of mavp2p's "udps" endpoint: send a hello heartbeat, then print
 *                                                    "HB <uptime_s> <sysid>" for every HEARTBEAT of <sysid> that arrives (all other sysids ignored)
 *   mavhb ptygen <link> <sysid> <period_ms> <seconds>
 *                                                    emulated serial FC: create a pty, symlink <link> -> its slave, feed HEARTBEATs into the master;
 *                                                    on exit the master is closed (reader gets EIO/hangup) and the link removed = "unplug"
 * HEARTBEAT = msgid 0, 9-byte payload, CRC_EXTRA 50 (MAVLink common.xml). */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static uint16_t crc_acc(uint16_t crc, uint8_t b)
{
	uint8_t t = b ^ (uint8_t)(crc & 0xff);
	t ^= (t << 4);
	return (crc >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4);
}

static int mk_hb(uint8_t *o, uint8_t seq, uint8_t sysid, uint8_t type)
{
	uint8_t p[9] = { 0, 0, 0, 0, type, 3 /* MAV_AUTOPILOT_ARDUPILOTMEGA */, 0, 4 /* MAV_STATE_ACTIVE */, 3 /* mavlink_version */ };
	uint16_t crc = 0xffff;
	int i;
	o[0] = 0xFD; o[1] = 9; o[2] = 0; o[3] = 0; o[4] = seq; o[5] = sysid; o[6] = 1; o[7] = 0; o[8] = 0; o[9] = 0;
	memcpy(o + 10, p, 9);
	for (i = 1; i < 19; i++) crc = crc_acc(crc, o[i]);
	crc = crc_acc(crc, 50);
	o[19] = crc & 0xff; o[20] = crc >> 8;
	return 21;
}

static double up(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

static int gen(const char *tty, int sysid, int per_ms, int secs)
{
	double end = up() + secs;
	int fd = -1; uint8_t seq = 0, f[32];
	while (up() < end) {
		if (fd < 0) {
			fd = open(tty, O_WRONLY | O_NONBLOCK | O_NOCTTY);
			if (fd >= 0) { struct termios t; if (!tcgetattr(fd, &t)) { cfmakeraw(&t); tcsetattr(fd, TCSANOW, &t); } }
		}
		if (fd >= 0) {
			int n = mk_hb(f, seq++, sysid, 2 /* MAV_TYPE_QUADROTOR */);
			if (write(fd, f, n) < 0 && errno != EAGAIN) { close(fd); fd = -1; }
		}
		usleep(per_ms * 1000);
	}
	return 0;
}

static int ptygen(const char *link, int sysid, int per_ms, int secs)
{
	int m = posix_openpt(O_RDWR | O_NOCTTY | O_NONBLOCK); uint8_t seq = 0, f[32]; double end = up() + secs; struct termios t;
	if (m < 0 || grantpt(m) || unlockpt(m)) { perror("pty"); return 1; }
	if (tcgetattr(m, &t) == 0) { cfmakeraw(&t); tcsetattr(m, TCSANOW, &t); }
	unlink(link);
	if (symlink(ptsname(m), link)) { perror("symlink"); return 1; }
	while (up() < end) { int n = mk_hb(f, seq++, sysid, 2); if (write(m, f, n) < 0 && errno != EAGAIN && errno != EIO) break; usleep(per_ms * 1000); }
	close(m); unlink(link);
	return 0;
}

static int rx(int port, int sysid, int secs)
{
	int s = socket(AF_INET, SOCK_DGRAM, 0);
	struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port), .sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
	uint8_t b[512], f[32]; double end = up() + secs, lastsend = 0; uint8_t seq = 0;
	setvbuf(stdout, NULL, _IOLBF, 0);
	while (up() < end) {
		struct pollfd p = { s, POLLIN, 0 };
		if (up() - lastsend >= 1.0) { sendto(s, f, mk_hb(f, seq++, 255, 6 /* GCS */), 0, (struct sockaddr *)&a, sizeof a); lastsend = up(); }
		if (poll(&p, 1, 100) > 0) {
			int n = recv(s, b, sizeof b, 0), i;
			for (i = 0; i + 10 < n; ) {
				if (b[i] != 0xFD) { i++; continue; }
				int len = b[i + 1], tot = 12 + len + ((b[i + 2] & 1) ? 13 : 0);
				if (i + tot > n) break;
				if ((b[i + 7] | b[i + 8] << 8 | b[i + 9] << 16) == 0 && b[i + 5] == sysid) printf("HB %.2f %d\n", up(), sysid);
				i += tot;
			}
		}
	}
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 6 && !strcmp(argv[1], "gen")) return gen(argv[2], atoi(argv[3]), atoi(argv[4]), atoi(argv[5]));
	if (argc == 6 && !strcmp(argv[1], "ptygen")) return ptygen(argv[2], atoi(argv[3]), atoi(argv[4]), atoi(argv[5]));
	if (argc == 5 && !strcmp(argv[1], "rx")) return rx(atoi(argv[2]), atoi(argv[3]), atoi(argv[4]));
	fprintf(stderr, "usage: mavhb gen <tty> <sysid> <period_ms> <secs> | ptygen <link> <sysid> <period_ms> <secs> | rx <udp_port> <sysid> <secs>\n");
	return 2;
}
