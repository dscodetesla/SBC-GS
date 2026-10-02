/* udpt <send_port> <listen_port> <count>: paced UDP sender+receiver, prints delivery. Test helper for the QEMU guest. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <poll.h>
int main(int argc, char **argv) {
  int sp = atoi(argv[1]), lp = atoi(argv[2]), n = atoi(argv[3]);
  int r = socket(AF_INET, SOCK_DGRAM, 0), s = socket(AF_INET, SOCK_DGRAM, 0);
  struct sockaddr_in a = {0}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK); a.sin_port = htons(lp);
  if (bind(r, (void *)&a, sizeof a) < 0) { perror("bind"); return 2; }
  struct sockaddr_in d = a; d.sin_port = htons(sp);
  static char seen[100000]; int got = 0; char buf[2048], pay[1000];
  memset(pay, 'x', sizeof pay);
  for (int i = 0; i < n; i++) {
    memcpy(pay, &i, sizeof i); sendto(s, pay, sizeof pay, 0, (void *)&d, sizeof d);
    struct pollfd p = {r, POLLIN, 0};
    while (poll(&p, 1, 10) > 0) { int m = recv(r, buf, sizeof buf, 0); if (m >= 4) { int k; memcpy(&k, buf, 4); if (k >= 0 && k < n && !seen[k]) { seen[k] = 1; got++; } } }
  }
  struct pollfd p = {r, POLLIN, 0};
  while (poll(&p, 1, 1500) > 0) { int m = recv(r, buf, sizeof buf, 0); if (m >= 4) { int k; memcpy(&k, buf, 4); if (k >= 0 && k < n && !seen[k]) { seen[k] = 1; got++; } } }
  printf("UDPT sent=%d received=%d\n", n, got);
  return got >= n * 9 / 10 ? 0 : 1;
}
