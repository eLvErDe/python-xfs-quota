/*

Resolve constants value (as hex) from linux/fs.h

Please `apt install linux-libc-dev libc6-dev` if you don't have the file

Then build with `gcc resolve-ioctl-val.c -o resolve-ioctl-val`

And run `./resolve-ioctl-val`

*/

#include <linux/fs.h>
#include <stdio.h>

void main() {
  printf("FS_IOC_FSGETXATTR: 0x%x\n", FS_IOC_FSGETXATTR);
  printf("FS_IOC_FSSETXATTR: 0x%x\n", FS_IOC_FSSETXATTR);
  printf("FS_XFLAG_PROJINHERIT: 0x%x\n", FS_XFLAG_PROJINHERIT);
}
