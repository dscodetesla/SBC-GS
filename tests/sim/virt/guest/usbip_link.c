/* usbip_link: plug/unplug a vudc gadget into the vhci host controller of the SAME kernel (the dummy_hcd replacement, because the Ubuntu
 * generic kernel has CONFIG_USB_DUMMY_HCD unset but ships usbip-vudc + vhci-hcd).
 * plug:   TCP loopback pair, USBIP OP_REQ_IMPORT/OP_REP_IMPORT handshake (what usbipd/usbip do), then
 *         server fd -> /sys/devices/platform/usbip-vudc.0/usbip_sockfd, client fd -> /sys/devices/platform/vhci_hcd.0/attach
 * unplug: write the port to vhci_hcd.0/detach
 * Usage: usbip_link plug [port] | unplug [port]   (default port 0 = first USB2 root-hub port of vhci_hcd.0) */
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define VUDC "/sys/devices/platform/usbip-vudc.0"
#define VHCI "/sys/devices/platform/vhci_hcd.0"

static int wr(const char *path, const char *s)
{
	FILE *f = fopen(path, "w");
	if (!f) { fprintf(stderr, "usbip_link: open %s: ", path); perror(""); return -1; }
	if (fputs(s, f) < 0) { fclose(f); return -1; }
	if (fclose(f)) { fprintf(stderr, "usbip_link: write '%s' to %s: ", s, path); perror(""); return -1; }
	return 0;
}

static int rd_all(int fd, void *buf, size_t n)
{
	size_t got = 0;
	while (got < n) { ssize_t r = read(fd, (char *)buf + got, n - got); if (r <= 0) return -1; got += r; }
	return 0;
}

struct op_hdr { uint16_t version, code; uint32_t status; } __attribute__((packed));
struct udev_t {
	char path[256], busid[32];
	uint32_t busnum, devnum, speed;
	uint16_t idVendor, idProduct, bcdDevice;
	uint8_t bDeviceClass, bDeviceSubClass, bDeviceProtocol, bConfigurationValue, bNumConfigurations, bNumInterfaces;
} __attribute__((packed));

static int plug(int port)
{
	int ls = socket(AF_INET, SOCK_STREAM, 0), cs = socket(AF_INET, SOCK_STREAM, 0), ss, one = 1;
	struct sockaddr_in a = { .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
	socklen_t al = sizeof a;
	struct op_hdr h; char busid[32] = "usbip-vudc.0", got[32]; struct udev_t d;
	char buf[96];
	setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
	if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 1) || getsockname(ls, (struct sockaddr *)&a, &al)) { perror("listen"); return 1; }
	if (connect(cs, (struct sockaddr *)&a, sizeof a)) { perror("connect"); return 1; }
	ss = accept(ls, NULL, NULL);
	if (ss < 0) { perror("accept"); return 1; }
	setsockopt(cs, IPPROTO_TCP, 1 /* TCP_NODELAY */, &one, sizeof one);
	setsockopt(ss, IPPROTO_TCP, 1, &one, sizeof one);
	/* client: OP_REQ_IMPORT */
	h.version = htons(0x0111); h.code = htons(0x8003); h.status = 0;
	if (write(cs, &h, sizeof h) != sizeof h || write(cs, busid, 32) != 32) { perror("send req"); return 1; }
	/* server: read request, answer OP_REP_IMPORT with the device description */
	if (rd_all(ss, &h, sizeof h) || rd_all(ss, got, 32)) { fprintf(stderr, "usbip_link: short request\n"); return 1; }
	memset(&d, 0, sizeof d);
	snprintf(d.path, sizeof d.path, "%s", VUDC); snprintf(d.busid, sizeof d.busid, "%s", got);
	d.busnum = htonl(1); d.devnum = htonl(2); d.speed = htonl(3 /* USB_SPEED_HIGH */);
	h.version = htons(0x0111); h.code = htons(0x0003); h.status = 0;
	if (write(ss, &h, sizeof h) != sizeof h || write(ss, &d, sizeof d) != sizeof d) { perror("send rep"); return 1; }
	/* client: parse the reply */
	if (rd_all(cs, &h, sizeof h) || rd_all(cs, &d, sizeof d) || h.status) { fprintf(stderr, "usbip_link: bad import reply\n"); return 1; }
	/* device side first (it must be in the AVAILABLE state), then the host controller */
	snprintf(buf, sizeof buf, "%d", ss);
	if (wr(VUDC "/usbip_sockfd", buf)) return 1;
	snprintf(buf, sizeof buf, "%d %d %u %u", port, cs, (ntohl(d.busnum) << 16) | ntohl(d.devnum), ntohl(d.speed));
	if (wr(VHCI "/attach", buf)) return 1;
	printf("usbip_link: plugged (port %d, devid 0x%x, speed %u)\n", port, (ntohl(d.busnum) << 16) | ntohl(d.devnum), ntohl(d.speed));
	return 0;
}

int main(int argc, char **argv)
{
	int port = argc > 2 ? atoi(argv[2]) : 0;
	char buf[16];
	if (argc >= 2 && !strcmp(argv[1], "plug")) return plug(port);
	if (argc >= 2 && !strcmp(argv[1], "unplug")) { snprintf(buf, sizeof buf, "%d", port); return wr(VHCI "/detach", buf) ? 1 : (puts("usbip_link: detached"), 0); }
	fprintf(stderr, "usage: usbip_link plug|unplug [port]\n");
	return 2;
}
