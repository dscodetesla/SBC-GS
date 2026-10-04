/* hwsimctl: create/destroy mac80211_hwsim radios at runtime over generic netlink (family MAC80211_HWSIM).
 * Usage: hwsimctl new            (the kernel does not return the new radio id; read it from /sys/class/ieee80211 or iw)
 *        hwsimctl del <radio_id>
 * Static, no libnl. Constants are from drivers/net/wireless/virtual/mac80211_hwsim.h (HWSIM_CMD_NEW_RADIO=4, DEL_RADIO=5, ATTR_RADIO_ID=10). */
#include <linux/genetlink.h>
#include <linux/netlink.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define HWSIM_CMD_NEW_RADIO 4
#define HWSIM_CMD_DEL_RADIO 5
#define HWSIM_ATTR_RADIO_ID 10

struct req { struct nlmsghdr n; struct genlmsghdr g; char buf[256]; };

static int add_attr(struct req *r, int type, const void *data, int len)
{
	struct nlattr *a = (struct nlattr *)((char *)r + NLMSG_ALIGN(r->n.nlmsg_len));
	a->nla_type = type;
	a->nla_len = NLA_HDRLEN + len;
	memcpy((char *)a + NLA_HDRLEN, data, len);
	r->n.nlmsg_len = NLMSG_ALIGN(r->n.nlmsg_len) + NLA_ALIGN(a->nla_len);
	return 0;
}

/* send, then wait for the ACK/error; returns 0 on success, -errno otherwise, and the first attribute-less reply is dropped */
static int xfer(int fd, struct req *r, char *reply, int rl)
{
	if (send(fd, r, r->n.nlmsg_len, 0) < 0) return -1;
	for (;;) {
		int n = recv(fd, reply, rl, 0);
		struct nlmsghdr *h = (struct nlmsghdr *)reply;
		if (n < 0) return -1;
		for (; NLMSG_OK(h, (unsigned)n); h = NLMSG_NEXT(h, n)) {
			if (h->nlmsg_type == NLMSG_ERROR) return ((struct nlmsgerr *)NLMSG_DATA(h))->error;
			if (h->nlmsg_type == NLMSG_DONE) return 0;
		}
	}
}

static int family_id(int fd)
{
	struct req r; char reply[4096];
	memset(&r, 0, sizeof r);
	r.n.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN);
	r.n.nlmsg_type = GENL_ID_CTRL; r.n.nlmsg_flags = NLM_F_REQUEST;
	r.g.cmd = CTRL_CMD_GETFAMILY; r.g.version = 1;
	add_attr(&r, CTRL_ATTR_FAMILY_NAME, "MAC80211_HWSIM", sizeof "MAC80211_HWSIM");
	if (send(fd, &r, r.n.nlmsg_len, 0) < 0) return -1;
	int n = recv(fd, reply, sizeof reply, 0);
	struct nlmsghdr *h = (struct nlmsghdr *)reply;
	if (n < 0 || !NLMSG_OK(h, (unsigned)n) || h->nlmsg_type == NLMSG_ERROR) return -1;
	struct nlattr *a = (struct nlattr *)((char *)NLMSG_DATA(h) + GENL_HDRLEN);
	int left = h->nlmsg_len - NLMSG_LENGTH(GENL_HDRLEN);
	while (left >= (int)NLA_HDRLEN && a->nla_len >= NLA_HDRLEN && a->nla_len <= left) {
		if ((a->nla_type & NLA_TYPE_MASK) == CTRL_ATTR_FAMILY_ID) return *(unsigned short *)((char *)a + NLA_HDRLEN);
		left -= NLA_ALIGN(a->nla_len);
		a = (struct nlattr *)((char *)a + NLA_ALIGN(a->nla_len));
	}
	return -1;
}

int main(int argc, char **argv)
{
	struct req r; char reply[4096];
	int fd, fam, rc;
	if (argc < 2 || (!strcmp(argv[1], "del") && argc < 3) || (strcmp(argv[1], "new") && strcmp(argv[1], "del"))) {
		fprintf(stderr, "usage: hwsimctl new | del <radio_id>\n");
		return 2;
	}
	fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_GENERIC);
	if (fd < 0) { perror("socket"); return 1; }
	fam = family_id(fd);
	if (fam < 0) { fprintf(stderr, "hwsimctl: generic netlink family MAC80211_HWSIM not found (module not loaded?)\n"); return 1; }
	memset(&r, 0, sizeof r);
	r.n.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN);
	r.n.nlmsg_type = fam; r.n.nlmsg_flags = NLM_F_REQUEST | NLM_F_ACK;
	r.g.version = 1;
	if (!strcmp(argv[1], "new")) {
		r.g.cmd = HWSIM_CMD_NEW_RADIO;
	} else {
		unsigned int id = (unsigned int)atoi(argv[2]);
		r.g.cmd = HWSIM_CMD_DEL_RADIO;
		add_attr(&r, HWSIM_ATTR_RADIO_ID, &id, sizeof id);
	}
	rc = xfer(fd, &r, reply, sizeof reply);
	printf("hwsimctl %s rc=%d\n", argv[1], rc);
	return rc ? 1 : 0;
}
