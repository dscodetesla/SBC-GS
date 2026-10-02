/* inj <tx_if> <rx_if>: inject 20 radiotap+802.11 frames on tx_if, count them on rx_if (both monitor). Guest-side test helper. */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <linux/if_packet.h>
#include <linux/if_ether.h>
#include <poll.h>
#include <arpa/inet.h>
static int bindif(const char *n, int proto){
  int fd = socket(AF_PACKET, SOCK_RAW, htons(proto)); if (fd<0){perror("socket");exit(2);}
  struct ifreq ifr; memset(&ifr,0,sizeof ifr); strncpy(ifr.ifr_name,n,IFNAMSIZ-1);
  if (ioctl(fd,SIOCGIFINDEX,&ifr)<0){perror("ifindex");exit(2);}
  struct sockaddr_ll sll; memset(&sll,0,sizeof sll); sll.sll_family=AF_PACKET; sll.sll_ifindex=ifr.ifr_ifindex; sll.sll_protocol=htons(proto);
  if (bind(fd,(void*)&sll,sizeof sll)<0){perror("bind");exit(2);}
  return fd;
}
int main(int argc,char**argv){
  int rx = bindif(argv[2], ETH_P_ALL), tx = bindif(argv[1], ETH_P_ALL);
  /* radiotap (8 bytes, no fields) + 802.11 data frame header, addr2/3 carry wfb magic 'WB' */
  unsigned char f[8+24+16]={0,0,8,0,0,0,0,0, 0x08,0x01,0,0, 0xff,0xff,0xff,0xff,0xff,0xff, 'W','B',0,0,0,0, 'W','B',0,0,0,0, 0,0};
  memcpy(f+32,"HELLO-WFB-SIM!!",16);
  int sent=0,got=0,tag=0; char sig=0;
  for(int i=0;i<20;i++){ f[30]=i; if(write(tx,f,sizeof f)>0) sent++; usleep(20000);
    struct pollfd p={rx,POLLIN,0};
    while(poll(&p,1,20)>0){ unsigned char b[2048]; int n=read(rx,b,sizeof b); if(n<=0)break;
      if(n>40){ int rl=b[2]|(b[3]<<8); if(rl<n-24 && memmem(b+rl,n-rl,"HELLO-WFB-SIM",13)){ got++; tag=rl; } } } }
  printf("INJECT sent=%d received_on_peer=%d rx_radiotap_len=%d\n",sent,got,tag);
  return got>0?0:1;
}
