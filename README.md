# Usage

Simple Python class to create folders with quota on XFS filesystem.

Could probably be extended easily to support EXT4.

No asyncio support yet, but could be added in minutes.

## resolve-ioctl-val.c

Simple C file to dump Linux kernel headers constants as hex value, used while developping the class, but absolutely useless at runtime

## xfs\_prjquota.py

Python class wrapping `xfs_quota` command and `FS_IOC_FSGETXATTR/FS_IOC_FSSETXATTR` IOCTL to assign project id to path and quota to project.

A simple `__main__` is embedded so you can test it yourself and implement easily what you need.

## check\_xfs\_proj\_quota.py

All-in-one script to be used as a Nagios check:

```
python3 check_xfs_proj_quota.py --path /var/log --warning 75 --critical 85

OK: Quota used 46% (6.9GiB/15.0GiB) for path /var/log is below warning 75% limit|used_percent=46%;75;85;0;100 used_bytes=7415558144B;12079595520;13690208256;0;16106127360
```
