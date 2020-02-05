# pylint: disable=line-too-long


"""
Class to handle XFS filesystems project (folder) quota in Python

Could be easily ported to Ext4 as it uses FS_IOC_FSGETXATTR/FS_IOC_FSSETXATTR IOCTL to assign project id to folder
But it currently relies on xfs_quota binary to assign quota to a project and report usage but this could probably done
by calling another binary or figure out which IOCTL to send to do that but that's too much work atm for me

TODO: Implement async API for asyncio usage

Be careful, as the class relies on xfs_quota shell calls it cannot be considered as thread/process safe
"""


import re
import os
import array
import fcntl
import shutil
import logging
import pathlib
import subprocess
from typing import Union, Optional, NamedTuple, Dict

import psutil  # type: ignore

#: Hex value of IOCTL to get extended attributes (see resolve-ioctl-val.c for more details)
FS_IOC_FSGETXATTR = 0x801C581F

#: Hex value of IOCTL to set extended attributes (see resolve-ioctl-val.c for more details)
FS_IOC_FSSETXATTR = 0x401C5820

#: Hex value for fsx_xflags attr of struct fsxattr to set project id inheritance
FS_XFLAG_PROJINHERIT = 0x200

#: RE matcher for xfs_quota report entries
RE_QUOTA_REPORT = re.compile(r"^#(?P<proj_id>[0-9]+)\s+(?P<used>[0-9]+)\s+(?P<soft>[0-9]+)\s+(?P<hard>[0-9]+)\s+(?P<warn>[0-9]+)\s+\[(?P<grace>.+)\]$")


class ProjectQuota(NamedTuple):
    """
    NamedTuple representing quota usage for a given project id
    """

    proj_id: int
    used: int
    soft: int
    hard: int
    warn: int
    grace: str


class XfsPrjQuotaNoSpace(Exception):
    """
    Raised when requested quota cannot be fulfilled

    :param message: Exception error message
    :type message: str
    :param max_available_bytes: Maximum available bytes on this storage
    :type max_available_bytes: int
    """

    def __init__(self, message: str, max_available_bytes: int) -> None:
        super().__init__(message)
        self.max_available_bytes = max_available_bytes


class XfsPrjQuota:
    """
    Class to handle XFS filesystems project (folder) quota in Python

    Could be easily ported to Ext4 as it uses FS_IOC_FSGETXATTR/FS_IOC_FSSETXATTR IOCTL to assign project id to folder
    But it currently relies on xfs_quota binary to assign quota to a project and report usage but this could probably done
    by calling another binary or figure out which IOCTL to send to do that but that's too much work atm for me

    Be careful, as the class relies on xfs_quota shell calls it cannot be considered as thread/process safe

    TODO: Implement async API for asyncio usage

    :param mnt_point: Filesystem mount point to handle quota for
    :type mnt_point: str or pathlib.Path
    """

    def __init__(self, mnt_point: Union[str, pathlib.Path]) -> None:

        self.logger = logging.getLogger(self.__class__.__name__)

        assert isinstance(mnt_point, (str, pathlib.Path)) and str(mnt_point).startswith(
            "/"
        ), "mount_point parameter must be a non-emtpy string (or pathlib.Path) starting with /"
        self.mnt_point = pathlib.Path(mnt_point) if isinstance(mnt_point, str) else mnt_point

        xfs_quota = shutil.which("xfs_quota")
        assert xfs_quota, "xfs_quota command not found, may I suggest apt install xfsprogs ?"
        self.xfs_quota = xfs_quota

        self._check_part_mounted()  # check partition is here and properly mounted

    def _check_part_mounted(self) -> None:
        """
        Verify provided partition path is mounted and has proper prjquota options

        :raises AssertionError: If provided mnt_point cannot be used with XFS project quotas
        """

        mounted = psutil.disk_partitions()
        target_mounted = [x for x in mounted if x.mountpoint == str(self.mnt_point)]
        assert target_mounted, "mount_point %s does not seems to be mounted" % self.mnt_point
        assert target_mounted[0].fstype == "xfs", "mount_point %s is not an XFS partition" % self.mnt_point
        assert "prjquota" in target_mounted[0].opts.split(","), "mount_point %s is not mounted with prjquota options" % self.mnt_point

    def get_proj_id_for_path(self, path: Union[str, pathlib.Path]) -> int:
        """
        Get project id (int) for given path

        I did not found any way to do this with xfs_quota command so I went the hard way

        Send "FSGETXATTR" IOCTL and parse the returned struct which contains project id at index 3
        (see linux/fs.h fsxattr struct definition)

        :param path: Path to get project id for
        :type path: str or pathlib.Path
        :raises AssertionError: If provided path in not sub path of self.mnt_point or not an existing directory
        :returns: Project id associated to this folder (0 means default project)
        :rtype: int
        """

        assert isinstance(path, (str, pathlib.Path)) and path, "path parameter must be a non-emtpy string (or pathlib.Path)"
        path = pathlib.Path(path) if isinstance(path, str) else path
        assert self.mnt_point in path.parents or self.mnt_point == path, "provided path %s is not a sub path of %s" % (path, self.mnt_point)
        assert path.is_dir(), "provided path %s is not an existing directory" % path

        path_fd = os.open(path, os.O_DIRECTORY)
        ret_struct = array.array("I", [0, 0, 0, 0, 0])  # __u32
        fcntl.ioctl(path_fd, FS_IOC_FSGETXATTR, ret_struct, True)  # type: ignore

        return ret_struct[3]  # project id stored at index 3, see fsxattr struct definition

    def set_proj_id_for_path(self, path: Union[str, pathlib.Path], proj_id: int) -> None:
        """
        Set project id (int) for given path

        Send "FSSETXATTR" IOCTL and enable FS_XFLAG_PROJINHERIT so subfolders/subfiles inherit from this setting
        (see linux/fs.h fsxattr struct definition)

        :param path: Path to set project id for
        :type path: str or pathlib.Path
        :param proj_id: Project id to set
        :type proj_id: int
        :raises AssertionError: If provided path in not sub path of self.mnt_point or not an existing directory
        """

        assert isinstance(path, (str, pathlib.Path)) and path, "path parameter must be a non-emtpy string (or pathlib.Path)"
        path = pathlib.Path(path) if isinstance(path, str) else path
        assert self.mnt_point in path.parents or self.mnt_point == path, "provided path %s is not a sub path of %s" % (path, self.mnt_point)
        assert path.is_dir(), "provided path %s is not an existing directory" % path
        assert isinstance(proj_id, int) and proj_id >= 0, "proj_id parameter must be a positive or zero integer"

        # Get current values first
        path_fd = os.open(path, os.O_DIRECTORY)
        fsxattr_struct = array.array("I", [0, 0, 0, 0, 0])  # __u32
        fcntl.ioctl(path_fd, FS_IOC_FSGETXATTR, fsxattr_struct, True)  # type: ignore

        # Set new value
        fsxattr_struct[0] = fsxattr_struct[0] | FS_XFLAG_PROJINHERIT
        fsxattr_struct[3] = proj_id
        fcntl.ioctl(path_fd, FS_IOC_FSSETXATTR, fsxattr_struct, True)  # type: ignore

        self.logger.debug("Project id %d assigned to folder %s", proj_id, str(path))

    @staticmethod
    def _parse_xfs_quota_report(stdout: bytes) -> Dict[int, ProjectQuota]:
        """
        Parse xfs_quota -x -c 'report -p -n -N' output

        See list_proj_quota method instead

        :param stdout: Raw stdout of xfs_quota command
        :type stdout: bytes
        :returns: Dict with project id as key and namedtuple as value with soft/hard/used values in bytes
        :rtype: dict
        """

        parsed: Dict[int, ProjectQuota] = {}
        for line in str(stdout, "utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            re_match = RE_QUOTA_REPORT.match(line)
            assert re_match, "unable to parser xfs_quota report line: %r" % line

            as_named_tuple = ProjectQuota(
                proj_id=int(re_match.group("proj_id")),
                used=int(re_match.group("used")) * 1024,  # For some reason setting quota in bytes, listing state in KiB
                soft=int(re_match.group("soft")) * 1024,
                hard=int(re_match.group("hard")) * 1024,
                warn=int(re_match.group("warn")),
                grace=re_match.group("grace"),
            )
            parsed[as_named_tuple.proj_id] = as_named_tuple

        return parsed

    def list_proj_quota(self) -> Dict[int, ProjectQuota]:
        """
        Query mnt_point with xfs_quota and return a dict indexed by project id and usage/limit values

        :returns: Dict with project id as key and namedtuple as value with soft/hard/used values in bytes
        :rtype: dict
        """

        # -p project quota, -n numeric project id, -N hide header
        cmd = [self.xfs_quota, "-x", "-c", "report -p -n -N", str(self.mnt_point)]
        stdout = subprocess.check_output(cmd)

        return self._parse_xfs_quota_report(stdout)

    @property
    def next_available_project_id(self) -> int:
        """
       Return next available project id (current used greatest project id + 1)

       :returns: Int of next free poject id
       :rtype: int
       """

        used_ids = self.list_proj_quota().keys()
        return max(used_ids) + 1

    def raise_not_enough_space(self, quota: int) -> None:
        """
        Raise exception if requested quota cannot be fulfilled

        :param quota: Quota to verify in bytes (0 or positive integer)
        :type quota: int
        :raises XfsPrjQuotaNoSpace: If requested quota exceed available non reserved space
        """

        assert quota is None or (isinstance(quota, int) and quota >= 0), "quota parameter must be a positive integer or zero"

        free_space = psutil.disk_usage(self.mnt_point).free
        reserved_by_quotas = sum([max(x.soft, x.hard) for x in self.list_proj_quota().values()])
        available_space = free_space - reserved_by_quotas

        if quota > available_space:
            err_msg = "Cannot allocate %d bytes soft quota, max available is %d bytes" % (quota, available_space)
            self.logger.error(err_msg)
            raise XfsPrjQuotaNoSpace(err_msg, max_available_bytes=available_space)

    def set_quota_for_proj_id(self, proj_id: int, soft: Optional[int] = None, hard: Optional[int] = None, safe_space: bool = True) -> None:
        """
        Set soft/hard quotas for given project_id

        Will be done using xfs_quota command

        :param proj_id: Project id to set
        :type proj_id: int
        :param soft: Assign given soft quota (bytes)
        :type soft: int, defaults to None
        :param hard: Assign given hard quota (bytes)
        :type hard: int, defaults to None
        :param safe_space: Set to True if you want to check there is enough free space (reserved quota taken in account too)
        :type safe_space: bool, defaults to True
        :raises XfsPrjQuotaNoSpace: If safe_space == True but request quota exceed available non reserved space
        """

        assert isinstance(proj_id, int) and proj_id >= 0, "proj_id parameter must be a positive or zero integer"
        assert soft is None or (isinstance(soft, int) and soft > 0), "soft parameter must be a positive integer or None"
        assert hard is None or (isinstance(hard, int) and hard > 0), "hard parameter must be a positive integer or None"
        assert safe_space is True or safe_space is False, "safe_space parameter must be True or False"

        valid_soft = 0 if soft is None else soft
        valid_hard = 0 if hard is None else hard

        if safe_space:
            self.raise_not_enough_space(valid_soft)
            self.raise_not_enough_space(valid_hard)

        cmd = [self.xfs_quota, "-x", "-c", "limit -p bsoft=%d bhard=%d %d" % (valid_soft, valid_hard, proj_id), str(self.mnt_point)]
        subprocess.check_call(cmd)


if __name__ == "__main__":
    """
    Test the class yourself here by calling this file as a script with TEST_MNT_POINT environment variable
    pointing to an XFS device mounted with prjquota options

    Be carefull, some random test folders will be created at the root of the mount point
    """  # pylint: disable=pointless-string-statement

    from pprint import pprint

    MNT_POINT = os.environ.get("TEST_MNT_POINT", None)
    assert MNT_POINT is not None, "Please call this script with TEST_MNT_POINT environment variable set"

    QUOTA = XfsPrjQuota(MNT_POINT)
    TEST_FOLDERS = [
        ("python_xfs_quota_001", 1, 15),
        ("python_xfs_quota_002", 2, 99),
        ("python_xfs_quota_003", 3, 1024),
        ("python_xfs_quota_004", None, 15 * 1024),
    ]

    def test_sync():
        """
        Test sync API
        """

        quota_details = QUOTA.list_proj_quota()
        pprint(quota_details)

        for folder, new_proj_id, size_mb in TEST_FOLDERS:

            full_path = os.path.join(MNT_POINT, folder)
            os.makedirs(full_path, exist_ok=True)
            cur_proj_id = QUOTA.get_proj_id_for_path(full_path)

            print("Folder %s has project id %d" % (full_path, cur_proj_id))

            new_proj_id = new_proj_id if new_proj_id is not None else QUOTA.next_available_project_id
            QUOTA.set_proj_id_for_path(full_path, new_proj_id)
            print("Folder %s has been set to project id %d" % (full_path, new_proj_id))

            QUOTA.set_quota_for_proj_id(new_proj_id, soft=size_mb * 1024 * 1024, hard=size_mb * 1024 * 1024)

        quota_details = QUOTA.list_proj_quota()
        pprint(quota_details)

        # Remove completely a quota and release used projectId
        # Yes you need to do this, otherwise projectId never get released
        folder_1_path = os.path.join(MNT_POINT, TEST_FOLDERS[1][0])
        folder_1_proj_id = QUOTA.get_proj_id_for_path(folder_1_path)
        QUOTA.set_proj_id_for_path(folder_1_path, 0)
        QUOTA.set_quota_for_proj_id(folder_1_proj_id, soft=None, hard=None)
        print("Folder %s and project id %d have been released" % (folder_1_path, folder_1_proj_id))

        quota_details = QUOTA.list_proj_quota()
        pprint(quota_details)

    test_sync()
