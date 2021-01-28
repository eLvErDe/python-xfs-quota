#!/usr/bin/python3


# pylint: disable=line-too-long,bad-continuation


"""
Check XFS quota usage for given path
"""


import sys

try:
    import os
    import re
    import fcntl
    import array
    import shutil
    import pathlib
    import argparse
    import subprocess
    from typing import Dict, Union, NamedTuple
except Exception as exc:  # pylint: disable=broad-except
    print("UNKNOWN: Got exception: %s: %s" % (exc.__class__.__name__, exc))
    sys.exit(3)

#: Hex value of IOCTL to get extended attributes
FS_IOC_FSGETXATTR = 0x801C581F

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


class XfsProjQuotaCheck:
    """
    Class to check XFS filesystems project (folder) quota in Python

    Could be easily ported to Ext4 as it uses FS_IOC_FSGETXATTR/FS_IOC_FSSETXATTR IOCTL to assign project id to folder
    But it currently relies on xfs_quota binary to assign quota to a project and report usage but this could probably done
    by calling another binary or figure out which IOCTL to send to do that but that's too much work atm for me

    Be careful, as the class relies on xfs_quota shell calls it cannot be considered as thread/process safe

    :param mnt_point: Filesystem mount point to handle quota for
    :type mnt_point: str or pathlib.Path
    """

    def __init__(self, mnt_point: str) -> None:
        assert isinstance(mnt_point, (str, pathlib.Path)) and str(mnt_point).startswith(
            "/"
        ), "mount_point parameter must be a non-emtpy string (or pathlib.Path) starting with /"
        self.mnt_point = pathlib.Path(mnt_point) if isinstance(mnt_point, str) else mnt_point

        xfs_quota = shutil.which("xfs_quota")
        assert xfs_quota, "xfs_quota command not found, may I suggest apt install xfsprogs ?"
        self.xfs_quota = xfs_quota

    def get_proj_id_for_path(self, path: Union[str, pathlib.Path]) -> int:
        """
        Get project id (int) for given path

        I did not found any way to do this with xfs_quota command so I went the hard way

        Send "FSGETXATTR" IOCTL and parse the returned struct which contains project id at index 3
        (see linux/fs.h fsxattr struct definition)

        :param path: Path to get project id for
        :type path: str or pathlib.Path
        :raises AssertionError: If provided path in not sub path of self.mnt_point or not an existing directory
        :return: Project id associated to this folder (0 means default project)
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

    @staticmethod
    def _parse_xfs_quota_report(stdout: bytes) -> Dict[int, ProjectQuota]:
        """
        Parse xfs_quota -x -c 'report -p -n -N' output

        See list_proj_quota method instead

        :param stdout: Raw stdout of xfs_quota command
        :type stdout: bytes
        :return: Dict with project id as key and namedtuple as value with soft/hard/used values in bytes
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

        :return: Dict with project id as key and namedtuple as value with soft/hard/used values in bytes
        :rtype: dict
        """

        # -p project quota, -n numeric project id, -N hide header
        cmd = [self.xfs_quota, "-x", "-c", "report -p -n -N", str(self.mnt_point)]
        stdout = subprocess.check_output(cmd)

        return self._parse_xfs_quota_report(stdout)

    @staticmethod
    def sizeof_fmt(num: Union[int, float], suffix: str = "B") -> str:
        """
        Format size in bytes into human readable string
        Stolen from https://stackoverflow.com/a/1094933

        :param num: Size in bytes
        :type num: int or float
        :param suffix: Suffix to append to size, usually B
        :type suffix: str, defaults to B
        :return: Human readable size, e.g: 15GiB
        :rtype: str
        """

        for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
            if abs(num) < 1024.0:
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, "Yi", suffix)

    @staticmethod
    def find_mount_point(path: str) -> str:
        """
        Find mount point for given path, i.e: the actual volume the provided path belongs to

        :param path: Path to find root volume for
        :type path: str
        :return: Path where the volume provided path belongs to is mounted
        :rtype: str
        """

        path = os.path.abspath(path)
        while not os.path.ismount(path):
            path = os.path.dirname(path)
        return path


class NagiosArgumentParser(argparse.ArgumentParser):
    """
    Inherit from ArgumentParser but exit with Nagios code 3 (Unknown) in case of argument error
    """

    def error(self, message: str):
        print("UNKNOWN: Bad arguments (see --help): %s" % message)
        sys.exit(3)


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments

    :return: argparse.Namespace object with all command line arguments as attributes (dash replace by underscore)
    :type: argparse.Namespace
    """

    argparser = NagiosArgumentParser(description=__doc__.strip())
    argparser.add_argument("-P", "--path", type=str, required=True, metavar="/var/log", help="Patht to be checked")
    argparser.add_argument("-W", "--warning", type=int, default=75, metavar="75", help="Percentage of FDs use raising a warning")
    argparser.add_argument("-C", "--critical", type=int, default=85, metavar="85", help="Percentage of FDs use raising an error")
    args = argparser.parse_args()

    if args.warning > args.critical:
        argparser.error("Warning threshold cannot be greater than critical one")

    if args.warning < 0 or args.warning > 100 or args.critical < 0 or args.critical > 100:
        argparser.error("Warning/critical tresholds must be a percentage between and 100")

    return args


def main(config: argparse.Namespace) -> None:  # pylint: disable=too-many-locals
    """
    Process everything

    :param config: argparse.Namespace instance representing command line arguments
    :rtype config: argparse.Namespace
    """

    # Need to find mount point of the volume the path to be checked belongs to
    volume_path = XfsProjQuotaCheck.find_mount_point(config.path)
    xfs_proj_quota = XfsProjQuotaCheck(volume_path)

    # Then we get path project id and find matching XFS quota
    project_id = xfs_proj_quota.get_proj_id_for_path(config.path)
    quotas = xfs_proj_quota.list_proj_quota()
    assert project_id in quotas, "No quotas have been found for project_id=%d, are you sure provided path %s has quota enabled ?" % (project_id, config.path)
    quota = quotas[project_id]

    # Use soft limit first, if unset uses hard
    used = quota.used
    limit = quota.soft if quota.soft != 0 else quota.hard

    # If limit == 0, no quota set, fallback to volume max size
    quota_found = True
    if limit == 0:
        vol_total, _vol_used, _vol_free = shutil.disk_usage(volume_path)
        limit = vol_total
        quota_found = False

    # Compute used percentage
    used_percent = int(round(used * 100 / limit))

    # Provide human readable size for state message
    used_human = xfs_proj_quota.sizeof_fmt(used)
    limit_human = xfs_proj_quota.sizeof_fmt(limit)

    # Compute Nagios perfdata for capacity planning
    warning_bytes = config.warning * limit / 100
    critical_bytes = config.critical * limit / 100
    perfdata = [
        "used_percent=%d%%;%d;%d;0;100" % (used_percent, config.warning, config.critical),
        "used_bytes=%dB;%d;%d;%d;%d" % (used, warning_bytes, critical_bytes, 0, limit),
    ]

    # Verify thresholds
    if used_percent > config.critical:
        message = "CRITICAL: Quota used %d%% (%s/%s) for path %s is above critical %d%% limit%s" % (
            used_percent,
            used_human,
            limit_human,
            config.path,
            config.critical,
            " (WARNING: No quota configured)" if not quota_found else "",
        )
        code = 2
    elif used_percent > config.warning:
        message = "WARNING: Quota used %d%% (%s/%s) for path %s is above warning %d%% limit%s" % (
            used_percent,
            used_human,
            limit_human,
            config.path,
            config.warning,
            " (WARNING: No quota configured)" if not quota_found else "",
        )
        code = 1
    else:
        message = "OK: Quota used %d%% (%s/%s) for path %s is below warning %d%% limit%s" % (
            used_percent,
            used_human,
            limit_human,
            config.path,
            config.warning,
            " (WARNING: No quota configured)" if not quota_found else "",
        )
        code = 0

    print("%s|%s" % (message, " ".join(perfdata)))
    sys.exit(code)


if __name__ == "__main__":

    CONFIG = parse_args()
    try:
        main(CONFIG)
    except Exception as exc:  # pylint: disable=broad-except
        print("UNKNOWN: Got exception: %s: %s" % (exc.__class__.__name__, exc))
        sys.exit(3)
