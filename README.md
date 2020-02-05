# Usage

Simple Python class to create folders with quota on XFS filesystem.

Could probably be extended easily to support EXT4.

No asyncio support yet, but could be added in minutes.

## resolve-ioctl-val.c

Simple C file to dump Linux kernel headers constants as hex value, used while developping the class, but absolutely useless at runtime

## xfs\_prjquota.py

Python class wrapping `xfs_quota` command and `FS_IOC_FSGETXATTR/FS_IOC_FSSETXATTR` IOCTL to assign project id to path and quota to project.

A simple `__main__` is embedded so you can test it yourself and implement easily what you need.
