# Copyright (C) 2010-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
This is the burnin class that tests the Snapshots functionality
"""

import os
import stat
import time
import base64
import random
import socket
import datetime
import paramiko
import tempfile

from synnefo_tools.burnin.common import Proper, QPITHOS, QADD, GB
from synnefo_tools.burnin.cyclades_common import CycladesTests, Retry

from kamaki.clients import ClientError

# NOTE: If these here chane the script below should change too
TMPFS_SIZE = "20m"
TMPFS_MOUNT = "/mnt/tmp"

FIO_TEST_FILE = "/fio_test_file"
JOBFILE = TMPFS_MOUNT + "/%s.fio"
JOBLOG = TMPFS_MOUNT + "/%s.log"
LOCAL_JOBLOG = "FioTestSuite_%s.log"
RUN_SCRIPT = TMPFS_MOUNT + "/run.sh"
WAIT_SCRIPT = TMPFS_MOUNT + "/wait.sh"

SOURCES_LIST = """
deb http://ftp.gr.debian.org/debian/ wheezy main

deb http://security.debian.org/ wheezy/updates main
"""

FIO_JOB_SIMPLE_VERIFY = """
[global]
bs=1M
direct=1
ioengine=libaio
filename={0}
thread=1
verify=md5
verify_interval=512
verify_fatal=1

[write-phase]
fill_fs=1 ; run until filesystem gets full
runtime=60 ; or at most 60 seconds
rw=write
do_verify=0

[verify-phase]
stonewall ; wait for previous job
create_serialize=0
rw=read
do_verify=1
""".format(FIO_TEST_FILE)

FIO_JOB_BENCH = """
[global]
bs=4k
direct=1
ioengine=libaio
filename={0}
size=1G
runtime=60
thread=1
iodepth=128
norandommap ; Do not look at past I/O history (consume less memory)

[randwrite]
stonewall
rw=randwrite

[randread]
stonewall
rw=randread

[readrw]
stonewall
rw=randrw
rwmixread=80
rwmixwrite=20
""".format(FIO_TEST_FILE)

FIO_JOB_PREPARE_VERIFY = """
[global]
bs=4K
direct=1
ioengine=libaio
thread=1
verify=md5
verify_interval=512
verify_fatal=1

[write-phase]
filename={0}
fill_fs=1 ; run until filesystem gets full
runtime=120 ; run for 120 secs while we are taking snapshots
rw=write
do_verify=0

[randrw]
size=1G
runtime=120
rw=randrw
rwmixread=80
rwmixwrite=20
""".format(FIO_TEST_FILE)

FIO_JOB_VERIFY = """
[global]
bs=4K
direct=1
ioengine=libaio
filename={0}
thread=1
verify=md5
verify_interval=512
verify_fatal=1

[verify-phase]
rw=read
do_verify=1
""".format(FIO_TEST_FILE)

RUN_SCRIPT_CODE = """
#!/bin/bash

# The first argument should be a unique identifier for the command
# If not set the we use a default one
name=${{1:-snf-burnin-fio}}

# Related files
job={0}/$name.fio
log={0}/$name.log
ok={0}/$name.ok
failed={0}/$name.failed

# Cleanup stale status files
rm -f $ok $failed

# Redirect everything to a log file as well.
# NOTE: This mignt not be possible in case we run in the background.
#exec &> >(tee -a $log)

# We could have used the oneliner:
#   (command && touch ok || touch failed) &
# but we would have missed the return value of the command.
fio $job >$log 2>&1
ret=$?
if [ $ret -ne 0 ]; then
    touch $failed
else
    touch $ok
fi

exit $ret
""".format(TMPFS_MOUNT)

WAIT_SCRIPT_CODE = """
#!/bin/bash

name=${{1:-snf-burnin-fio}}

ok={0}/$name.ok
failed={0}/$name.failed

while true; do
    test -f $ok && exit 0
    test -f $failed && exit 1
    sleep 1
done
""".format(TMPFS_MOUNT)


# This class gets replicated into actual TestCases dynamically
class FioTestSuite(CycladesTests):
    """Test Snapshot functionality"""
    personality = Proper(value=None)
    account = Proper(value=None)
    servers = Proper(value=[])
    snapshots = Proper(value=[])
    use_flavor = Proper(value=None)
    use_image = Proper(value=None)
    server_ip = Proper(value=None)
    server_username = Proper(value=None)
    server_password = Proper(value=None)
    server_volume = Proper(value=None)

    @staticmethod
    def _create_tmpfile(data):
        """Create a tmp file with data."""
        (fd, path) = tempfile.mkstemp()
        os.write(fd, data)
        os.close(fd)
        return path

    def _setup_ssh_connection(self):
        """Create an SSH connection with the VM."""
        def check_fun():
            """Try setup an SSH connection."""
            try:
                transport = paramiko.Transport((self.server_ip, 22))
                transport.connect(username=self.server_username,
                                  password=self.server_password)
            except (paramiko.SSHException, socket.error, EOFError) as err:
                self.warning("%s", err.message)
                raise Retry()

            return transport

        opmsg = "Establishing SSH connection %s@%s" % \
            (self.server_username, self.server_ip)
        return self._try_until_timeout_expires(opmsg, check_fun)

    def _get_ssh_client(self):
        """Return an SSH client on top of an SSH connection."""
        connection = self._setup_ssh_connection()
        return connection.open_session()

    def _get_sftp_client(self):
        """Return an SFTP client on top of an SSH connection."""
        connection = self._setup_ssh_connection()
        return paramiko.SFTPClient.from_transport(connection)

    def _get_server_ip(self, server):
        """Find the server's IP."""
        server_details = self.clients.cyclades.get_server_details(server['id'])
        server_ips = self._get_ips(server_details, version=4)
        self.assertTrue(len(server_ips) >= 1)
        server_ip = server_ips[0]
        self._insist_on_ping(server_ip, version=4)
        return server_ip

    def _create_linux_server(self):
        """Create a linux server to play with."""
        account = self._get_uuid()
        use_image = random.choice(self._parse_images())
        use_flavor = random.choice(self._parse_flavors())
        personality = [{
            'path': "/root/test_inj_file",
            'owner': "root",
            'group': "root",
            'mode': stat.S_IRUSR | stat.S_IWUSR,
            'contents': base64.b64encode("This is a personality file")
        }, {
            'path': "/etc/apt/sources.list",
            'owner': "root",
            'group': "root",
            'mode': stat.S_IRUSR | stat.S_IWUSR,
            'contents': base64.b64encode(SOURCES_LIST)
        }]

        self.assertTrue(self._image_is(use_image, "linux"))
        self.info("Using image %s with id %s",
                  use_image['name'], use_image['id'])
        self.info("Using flavor %s with id %s",
                  use_flavor['name'], use_flavor['id'])

        server = self._create_server(use_image,
                                     use_flavor,
                                     personality=personality,
                                     network=True)
        self._insist_on_server_transition(server, ["BUILD"], "ACTIVE")

        server_username = self._get_connection_username(server)
        server_password = server['adminPass']
        server_ip = self._get_server_ip(server)

        volume = server['volumes'][0]

        # Update class level variables
        self.servers.append(server)
        self.account = account
        self.use_image = use_image
        self.use_flavor = use_flavor
        self.personality = personality
        self.server_ip = server_ip
        self.server_username = server_username
        self.server_password = server_password
        self.server_volume = volume

    def _inject_file(self, remotepath, data, executable=False):
        """Inject a file/script with data to the server."""
        sftp = self._get_sftp_client()
        localpath = self._create_tmpfile(data)
        self.info("Injecting `%s' to `%s'", localpath, remotepath)
        sftp.put(localpath, remotepath)
        mode = 0750 if executable else 0640
        sftp.chmod(remotepath, mode)
        os.remove(localpath)
        sftp.close()

    def _fetch_file(self, remotepath, localpath):
        """Fetch a file from the server."""
        sftp = self._get_sftp_client()
        self.info("Fetching `%s' to `%s'", remotepath, localpath)
        sftp.get(remotepath, localpath)
        sftp.close()

    def _run_ssh_command(self, command, background=False, should_fail=False):
        """Run a command via ssh."""
        ssh = self._get_ssh_client()

        # Support passing command as a list
        if isinstance(command, list):
            command = " ".join(command)

        # Add & in case we should run it in the background
        command += " &" if background else ""

        self.info("Running command `%s'", command)
        try:
            ssh.exec_command(command)
        except (paramiko.SSHException, socket.error) as err:
            msg = "Command %s failed: %s" % (command, str(err))
            raise AssertionError(msg)

        # FIXME: How about stdout/stderr
        status = ssh.recv_exit_status()
        if should_fail:
            self.assertTrue(status != 0)
        else:
            self.assertTrue(status == 0)

        ssh.close()

    def _run_job(self, name, background=False):
        """Run a job via the wrapper script."""
        self._inject_file(RUN_SCRIPT, RUN_SCRIPT_CODE, executable=True)
        self._run_ssh_command([RUN_SCRIPT, name],
                              background=background)

    def _wait_job(self, name):
        """Wait a job via the wait script and collect remote logs."""
        self._inject_file(WAIT_SCRIPT,
                          WAIT_SCRIPT_CODE, executable=True)
        self._run_ssh_command([WAIT_SCRIPT, name])
        # Collect the remote logs from /tmp/<name>.log and store them locally
        # under /var/lib/burnin/<run_id>/FioTestSuite_<name>.log
        remotepath = JOBLOG % name
        localpath = os.path.join(self.state_dir, LOCAL_JOBLOG % name)
        self._fetch_file(remotepath, localpath)

    def _run_fio_test(self, name, jobfile_code, background=False):
        """Run a fio test."""
        self.info("Runing fio test `%s'", name)
        self._inject_file(JOBFILE % name, jobfile_code)
        self._run_job(name, background)

    def _generate_snapshot_name(self):
        """Generate a unique snapshot name."""
        now = datetime.datetime.now()
        timestamp = datetime.datetime.strftime(now, "%Y%m%d%H%M%S")
        snapshot_name = 'snf-burnin-snapshot_%s_%s' % (self.server_volume,
                                                       timestamp)
        return snapshot_name

    def _snapshot_root_volume(self):
        """Create a snapshot of the root volume."""
        volume = self.server_volume
        snapshot_name = self._generate_snapshot_name()

        self.info("Creating snapshot with name '%s', for volume %s",
                  snapshot_name, volume)
        snapshot = self.clients.block_storage.create_snapshot(
            volume, display_name=snapshot_name)
        self.info("Snapshot with id '%s' created", snapshot['id'])

        self.info('Check that snapshot is listed among snapshots')
        self.assertTrue(snapshot['id'] in [i['id']
                        for i in self.clients.block_storage.list_snapshots()])

        self.info('Get snapshot details')
        self.clients.block_storage.get_snapshot_details(snapshot['id'])

        self.info('Check the snapshot is listed under snapshots container')
        self._set_pithos_account(self.account)
        self.clients.pithos.container = 'snapshots'
        self.assertTrue(snapshot['display_name'] in
                        [o['name'] for o in self.clients.pithos.list_objects()])

        self._insist_on_snapshot_transition(
            snapshot, ["UNAVAILABLE", "CREATING"], "AVAILABLE")

        self.snapshots.append(snapshot)
        snapshot_size = snapshot['size'] * GB
        # The snapshot "eats" pithos storage quota
        self._check_quotas({self.account:
                            [(QPITHOS, QADD, snapshot_size, None)]})

    def test_001_setup_linux_server(self):
        """Create a linux server, install fio, and mount a tmpfs"""
        self._create_linux_server()
        cmds = [
            "apt-get update",
            "export DEBIAN_FRONTEND=noninteractive",
            "apt-get install -y --force-yes fio",
            "mkdir -p %s" % TMPFS_MOUNT,
            "mount -t tmpfs -o size=%s tmpfs %s" % (TMPFS_SIZE, TMPFS_MOUNT),
        ]
        self._run_ssh_command(" && ".join(cmds))

    def test_002_fio_simple_verify(self):
        """Run fio task to verify basic I/O consistency"""
        self._run_fio_test("simple-verify", FIO_JOB_SIMPLE_VERIFY)

    def test_003_fio_bench(self):
        """Run fio task to measure I/O performance"""
        self._run_fio_test("bench", FIO_JOB_BENCH)
        self._wait_job("bench")

    def test_004_fio_background(self):
        """Run fio task in the background."""
        self._run_fio_test("prepare-verify", FIO_JOB_PREPARE_VERIFY, True)

    def test_005_snapshot_burst(self):
        """Take some snapshots"""
        for i in range(1, 10):
            self.info("Taking snapshot #%s", i)
            self._snapshot_root_volume()
            time.sleep(random.randint(1, 5))

    def test_006_fio_verify(self):
        """Wait for previous fio task and do a verify run."""
        self._wait_job("prepare-verify")
        self._run_fio_test("verify", FIO_JOB_VERIFY)

    def test_cleanup(self):
        """Cleanup created servers"""
        for s in self.servers:
            self._disconnect_from_network(s)
        self._delete_servers(self.servers)
        for s in self.snapshots:
            self.clients.block_storage.delete_snapshot(s['id'])

    @classmethod
    def tearDownClass(cls):  # noqa
        """Clean up"""
        # Delete snapshot
        snapshots = [s for s in cls.clients.block_storage.list_snapshots()
                     if s['display_name'].startswith("snf-burnin-snapshot")]

        for snapshot in snapshots:
            try:
                cls.clients.block_storage.delete_snapshot(snapshot['id'])
            except ClientError:
                pass
